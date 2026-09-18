"""TLS diagnostics and isolated native-HTTPS smoke tests."""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from deckbox import cli, config, service, tls
from deckbox.config import ResolvedConfig
from deckbox.doctor import Diagnostic, inspect_tls, probe_https_health, run_doctor


def _trusted_context(ca_cert: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(ca_cert))
    return context


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            content = json.dumps({"status": "ok", "version": "test"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_error(404)

    def log_message(self, _format: str, *args: object) -> None:
        pass


class _InvalidUtf8HealthHandler(_HealthHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1")
            self.end_headers()
            self.wfile.write(b"\xff")
            return
        self.send_error(404)


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class TlsDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_dir = Path(self.temporary_directory.name) / "config"
        self.config_path = self.config_dir / "config.yaml"
        self.root = Path(self.temporary_directory.name) / "served"
        self.root.mkdir()
        self.patches = [
            patch.object(config, "CONFIG_DIR", self.config_dir),
            patch.object(config, "CONFIG_PATH", self.config_path),
            patch.object(tls, "CONFIG_DIR", self.config_dir),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def cfg(self, *, host: str = "127.0.0.1", port: int = 9443) -> ResolvedConfig:
        return ResolvedConfig(self.root, host, port, "warning")

    def _tls_status(self, cfg: ResolvedConfig) -> tls.TLSStatus:
        paths = tls.tls_paths(self.config_dir)
        tls.setup_local_ca(paths, hostnames=cfg.tls_hostnames, ip_addresses=cfg.tls_ips)
        return tls.inspect_tls(paths, hostnames=cfg.tls_hostnames, ip_addresses=cfg.tls_ips)

    def _https_server(
        self,
        status: tls.TLSStatus,
        *,
        server_class: type[ThreadingHTTPServer] = ThreadingHTTPServer,
        address: tuple[str, int] = ("127.0.0.1", 0),
        handler_class: type[BaseHTTPRequestHandler] = _HealthHandler,
    ) -> tuple[ThreadingHTTPServer, threading.Thread]:
        server = server_class(address, handler_class)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(status.paths.leaf_cert, status.paths.leaf_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, thread

    def test_inspection_reports_missing_tls_as_an_actionable_failure_without_writes(self) -> None:
        results = inspect_tls(self.cfg())

        self.assertTrue(all(isinstance(result, Diagnostic) for result in results))
        self.assertTrue(
            any(not result.ok and "deckbox setup-tls" in result.detail for result in results)
        )
        self.assertFalse((self.config_dir / "tls").exists())

    def test_probe_accepts_a_ca_trusted_native_tls_health_response(self) -> None:
        initial = self.cfg()
        status = self._tls_status(initial)
        server, _ = self._https_server(status)
        cfg = self.cfg(port=server.server_port)

        result = probe_https_health(cfg, status)

        self.assertTrue(result.ok, result.detail)
        self.assertIn("HTTPS health", result.detail)

    def test_probe_rejects_a_plain_http_listener(self) -> None:
        status = self._tls_status(self.cfg())
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        result = probe_https_health(self.cfg(port=server.server_port), status)

        self.assertFalse(result.ok)
        self.assertIn("TLS negotiation", result.detail)

    def test_probe_reports_invalid_health_json_for_a_non_utf8_200_response(self) -> None:
        status = self._tls_status(self.cfg())
        server, _ = self._https_server(status, handler_class=_InvalidUtf8HealthHandler)

        result = probe_https_health(self.cfg(port=server.server_port), status)

        self.assertFalse(result.ok)
        self.assertIn("health JSON", result.detail)

    def test_probe_rejects_a_trusted_certificate_with_the_wrong_hostname(self) -> None:
        issued_for = ResolvedConfig(
            self.root,
            "0.0.0.0",
            9443,
            "warning",
            tls_hostnames=("expected.example.test",),
        )
        status = self._tls_status(issued_for)
        server, _ = self._https_server(status)
        wrong_name = ResolvedConfig(
            self.root,
            "0.0.0.0",
            server.server_port,
            "warning",
            tls_hostnames=("wrong.example.test",),
        )

        result = probe_https_health(wrong_name, status)

        self.assertFalse(result.ok)
        self.assertIn("hostname verification", result.detail)

    def test_doctor_fails_for_missing_or_invalid_tls_material(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            missing_exit = run_doctor(self.cfg())

        self.assertEqual(missing_exit, 1)
        self.assertIn("deckbox setup-tls", output.getvalue())

        status = self._tls_status(self.cfg())
        status.paths.leaf_cert.write_bytes(b"not a certificate")
        output = io.StringIO()
        with redirect_stdout(output):
            invalid_exit = run_doctor(self.cfg())

        self.assertEqual(invalid_exit, 1)
        self.assertIn("TLS", output.getvalue())

    def test_concrete_bind_ip_must_be_covered_before_run_doctor_or_status_succeeds(self) -> None:
        config.save_config_file({"host": "127.0.0.2"})
        paths = tls.tls_paths(self.config_dir)
        tls.setup_local_ca(paths, hostnames=(), ip_addresses=())
        args = argparse.Namespace(
            path=None,
            dir=str(self.root),
            host="127.0.0.2",
            port=9443,
            log_level="warning",
            allow_outside_root=None,
        )

        with (
            patch("deckbox.cli.create_app") as create_app,
            patch("deckbox.cli.uvicorn.run") as run_uvicorn,
        ):
            self.assertEqual(cli.run(args), 1)
        doctor_output = io.StringIO()
        with redirect_stdout(doctor_output):
            self.assertEqual(run_doctor(self.cfg(host="127.0.0.2")), 1)
        status_output = io.StringIO()
        with (
            patch("deckbox.service.is_installed", return_value=False),
            redirect_stdout(status_output),
        ):
            self.assertEqual(cli.status(args), 1)

        self.assertIn("SAN", doctor_output.getvalue())
        self.assertIn("SAN", status_output.getvalue())
        create_app.assert_not_called()
        run_uvicorn.assert_not_called()

        self.assertEqual(
            cli.setup_tls(argparse.Namespace(status=False, renew=True, hostname=None, ip=None)), 0
        )
        with (
            patch("deckbox.cli.create_app", return_value=object()) as create_app,
            patch("deckbox.cli.uvicorn.run") as run_uvicorn,
            patch("deckbox.cli.pam_available", return_value=True),
        ):
            self.assertEqual(cli.run(args), 0)
        create_app.assert_called_once()
        run_uvicorn.assert_called_once()

    def test_service_tls_guard_requires_a_concrete_bind_identity(self) -> None:
        paths = tls.tls_paths(self.config_dir)
        tls.setup_local_ca(paths, hostnames=(), ip_addresses=())

        with self.assertRaisesRegex(RuntimeError, "setup-tls"):
            service._require_tls(self.cfg(host="127.0.0.2"))

        self.assertTrue(tls.inspect_tls(paths, hostnames=(), ip_addresses=()).ready)

    def test_malformed_persisted_tls_names_are_actionable_without_diagnostic_mutation(self) -> None:
        paths = tls.tls_paths(self.config_dir)
        tls.setup_local_ca(paths, hostnames=(), ip_addresses=())
        before = {
            path: path.read_bytes()
            for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
        }

        for persisted in (
            {"tls_hostnames": ["invalid name"]},
            {"tls_ips": ["not-an-ip"]},
        ):
            with self.subTest(persisted=persisted):
                config.save_config_file(persisted)
                args = argparse.Namespace(
                    path=None,
                    dir=str(self.root),
                    host=None,
                    port=None,
                    log_level=None,
                    allow_outside_root=None,
                )
                doctor_output = io.StringIO()
                with redirect_stdout(doctor_output):
                    doctor_exit = run_doctor(cli._runtime_config(args))
                status_output = io.StringIO()
                with (
                    patch("deckbox.service.is_installed", return_value=False),
                    redirect_stdout(status_output),
                ):
                    status_exit = cli.status(args)

                self.assertEqual(doctor_exit, 1)
                self.assertEqual(status_exit, 1)
                self.assertIn("deckbox setup-tls", doctor_output.getvalue())
                self.assertIn("deckbox setup-tls", status_output.getvalue())
                self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_type_invalid_persisted_tls_schema_is_reported_without_mutation(self) -> None:
        paths = tls.tls_paths(self.config_dir)
        tls.setup_local_ca(paths, hostnames=(), ip_addresses=())
        before_tls = {
            path: path.read_bytes()
            for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
        }
        args = argparse.Namespace(
            path=None,
            dir=str(self.root),
            host=None,
            port=None,
            log_level=None,
            allow_outside_root=None,
        )

        for contents in ("tls_hostnames:\n  - 42\n", "tls_ips: not-a-list\n"):
            with self.subTest(contents=contents):
                self.config_path.write_text(contents, encoding="utf-8")
                before_config = self.config_path.read_bytes()
                doctor_stderr = io.StringIO()
                with redirect_stderr(doctor_stderr):
                    doctor_exit = cli.doctor(args)
                status_stderr = io.StringIO()
                with (
                    patch("deckbox.service.is_installed", return_value=False),
                    redirect_stderr(status_stderr),
                ):
                    status_exit = cli.status(args)

                self.assertEqual(doctor_exit, 1)
                self.assertEqual(status_exit, 1)
                self.assertIn("invalid TLS configuration", doctor_stderr.getvalue())
                self.assertIn("invalid TLS configuration", status_stderr.getvalue())
                self.assertIn("deckbox setup-tls", doctor_stderr.getvalue())
                self.assertIn("deckbox setup-tls", status_stderr.getvalue())
                self.assertNotIn("Traceback", doctor_stderr.getvalue())
                self.assertNotIn("Traceback", status_stderr.getvalue())
                self.assertEqual(self.config_path.read_bytes(), before_config)
                self.assertEqual({path: path.read_bytes() for path in before_tls}, before_tls)

    def test_doctor_and_status_skip_handshake_when_valid_tls_listener_is_stopped(self) -> None:
        self._tls_status(self.cfg())
        doctor_output = io.StringIO()
        with redirect_stdout(doctor_output):
            doctor_exit = run_doctor(self.cfg())

        status_output = io.StringIO()
        with (
            patch("deckbox.service.is_installed", return_value=False),
            redirect_stdout(status_output),
        ):
            status_exit = cli.status(
                argparse.Namespace(
                    path=None,
                    dir=str(self.root),
                    host="127.0.0.1",
                    port=9443,
                    log_level="warning",
                    allow_outside_root=None,
                )
            )

        self.assertEqual(doctor_exit, 0)
        self.assertIn("handshake", doctor_output.getvalue())
        self.assertIn("skipped", doctor_output.getvalue().lower())
        self.assertEqual(status_exit, 0)
        self.assertIn("TLS readiness", status_output.getvalue())
        self.assertIn("TLS path", status_output.getvalue())
        self.assertIn("CA SHA-256", status_output.getvalue())
        self.assertIn("leaf expiry", status_output.getvalue())
        self.assertIn("DNS SANs", status_output.getvalue())
        self.assertIn("IP SANs", status_output.getvalue())
        self.assertIn("not listening", status_output.getvalue())
        self.assertIn("https://127.0.0.1:9443", status_output.getvalue())

    def test_doctor_and_status_fail_when_a_listening_endpoint_fails_trusted_health(self) -> None:
        self._tls_status(self.cfg())
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        cfg = self.cfg(port=server.server_port)

        doctor_output = io.StringIO()
        with redirect_stdout(doctor_output):
            doctor_exit = run_doctor(cfg)
        status_output = io.StringIO()
        args = argparse.Namespace(
            path=None,
            dir=str(self.root),
            host="127.0.0.1",
            port=server.server_port,
            log_level="warning",
            allow_outside_root=None,
        )
        with (
            patch("deckbox.service.is_installed", return_value=False),
            redirect_stdout(status_output),
        ):
            status_exit = cli.status(args)

        self.assertEqual(doctor_exit, 1)
        self.assertIn("TLS negotiation", doctor_output.getvalue())
        self.assertEqual(status_exit, 1)
        self.assertIn("TLS negotiation", status_output.getvalue())

    def test_ipv6_wildcard_bind_probes_ipv6_loopback_for_doctor_and_status(self) -> None:
        cfg = self.cfg(host="::")
        status = self._tls_status(cfg)
        try:
            server, _ = self._https_server(
                status,
                server_class=_IPv6ThreadingHTTPServer,
                address=("::1", 0),
            )
        except OSError as error:
            self.skipTest(f"IPv6 loopback is unavailable: {error}")
        cfg = self.cfg(host="::", port=server.server_port)

        doctor_output = io.StringIO()
        with redirect_stdout(doctor_output):
            doctor_exit = run_doctor(cfg)
        status_output = io.StringIO()
        args = argparse.Namespace(
            path=None,
            dir=str(self.root),
            host="::",
            port=server.server_port,
            log_level="warning",
            allow_outside_root=None,
        )
        with (
            patch("deckbox.service.is_installed", return_value=False),
            redirect_stdout(status_output),
        ):
            status_exit = cli.status(args)

        self.assertEqual(doctor_exit, 0)
        self.assertIn("HTTPS health verified", doctor_output.getvalue())
        self.assertNotIn("skipped", doctor_output.getvalue().lower())
        self.assertEqual(status_exit, 0)
        self.assertIn("HTTPS health verified", status_output.getvalue())
        self.assertNotIn("not listening", status_output.getvalue())


class IsolatedHttpsSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_dir = Path(self.temporary_directory.name) / "config"
        self.root = Path(self.temporary_directory.name) / "served"
        self.root.mkdir()
        self.port = self._reserve_port()
        self.environment = {**os.environ, "DECKBOX_CONFIG_DIR": str(self.config_dir)}
        self.processes: list[subprocess.Popen[str]] = []
        self.addCleanup(self._stop_processes)

    @staticmethod
    def _reserve_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _deckbox(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "deckbox", *args],
            env=self.environment,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    def _start(self) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "deckbox",
                "run",
                "--dir",
                str(self.root),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            env=self.environment,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        return process

    def _stop_processes(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def test_explicit_setup_then_run_serves_only_ca_trusted_native_https_bootstrap_paths(
        self,
    ) -> None:
        before_setup = self._deckbox(
            "run", "--dir", str(self.root), "--host", "127.0.0.1", "--port", str(self.port)
        )
        self.assertEqual(before_setup.returncode, 1)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=0.2)

        setup = self._deckbox("setup-tls", "--hostname", "localhost")
        self.assertEqual(setup.returncode, 0, setup.stderr)
        expected_ca = (self.config_dir / "tls" / "ca.crt").read_bytes()
        process = self._start()
        context = _trusted_context(self.config_dir / "tls" / "ca.crt")
        base_url = f"https://localhost:{self.port}"

        for _ in range(50):
            if process.poll() is not None:
                self.fail(process.stderr.read())
            try:
                with urllib.request.urlopen(
                    f"{base_url}/health", context=context, timeout=0.2
                ) as response:
                    health = json.load(response)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.05)
                continue
        else:
            self.fail("isolated Deckbox HTTPS listener did not become ready")

        self.assertEqual(health["status"], "ok")
        with urllib.request.urlopen(f"{base_url}/setup", context=context, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertNotIn("strict-transport-security", response.headers)
        with urllib.request.urlopen(f"{base_url}/ca.crt", context=context, timeout=2) as response:
            self.assertEqual(response.read(), expected_ca)
            self.assertNotIn("strict-transport-security", response.headers)
        with self.assertRaises(urllib.error.HTTPError) as root_error:
            urllib.request.urlopen(f"{base_url}/", context=context, timeout=2)
        self.assertEqual(root_error.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
