"""Contract tests for Deckbox's fail-closed HTTPS CLI."""

from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from deckbox import cli, config, tls
from deckbox.config import ResolvedConfig, load_config_file
from deckbox.tls import TLSError, tls_paths


class CliTlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_dir = Path(self.temporary_directory.name) / "config"
        self.config_path = self.config_dir / "config.yaml"
        self.patches = [
            patch.object(config, "CONFIG_DIR", self.config_dir),
            patch.object(config, "CONFIG_PATH", self.config_path),
            patch.object(tls, "CONFIG_DIR", self.config_dir),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.directory = Path(self.temporary_directory.name) / "served"
        self.directory.mkdir()

    def run_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            path=None,
            dir=str(self.directory),
            host="127.0.0.1",
            port=9443,
            log_level="warning",
            allow_outside_root=None,
        )

    def setup_args(
        self,
        *,
        status: bool = False,
        renew: bool = False,
        hostname: list[str] | None = None,
        ip: list[str] | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(status=status, renew=renew, hostname=hostname, ip=ip)

    def test_run_refuses_missing_tls_without_creating_app_or_starting_uvicorn(self) -> None:
        stderr = io.StringIO()
        with (
            patch("deckbox.cli.require_tls", side_effect=TLSError("missing")),
            patch("deckbox.cli.create_app") as create_app,
            patch("deckbox.cli.uvicorn.run") as run_uvicorn,
            redirect_stderr(stderr),
        ):
            exit_code = cli.run(self.run_args())

        self.assertEqual(exit_code, 1)
        self.assertIn("deckbox setup-tls", stderr.getvalue())
        create_app.assert_not_called()
        run_uvicorn.assert_not_called()

    def test_run_uses_real_task_one_leaf_paths_and_https_banner(self) -> None:
        paths = tls_paths(self.config_dir)
        tls.setup_local_ca(paths, hostnames=(), ip_addresses=())
        stdout = io.StringIO()
        with (
            patch("deckbox.cli.create_app", return_value=object()) as create_app,
            patch("deckbox.cli.uvicorn.run") as run_uvicorn,
            patch("deckbox.cli.pam_available", return_value=True),
            patch("deckbox.cli.launch_user", return_value="deckbox-user"),
            redirect_stdout(stdout),
        ):
            exit_code = cli.run(self.run_args())

        self.assertEqual(exit_code, 0)
        create_app.assert_called_once()
        self.assertTrue(create_app.call_args.kwargs["auth_required"])
        self.assertEqual(
            run_uvicorn.call_args.kwargs,
            {
                "host": "127.0.0.1",
                "port": 9443,
                "log_level": "warning",
                "ssl_certfile": str(paths.leaf_cert),
                "ssl_keyfile": str(paths.leaf_key),
            },
        )
        self.assertIn("https://127.0.0.1:9443", stdout.getvalue())

    def test_setup_tls_status_is_read_only_and_missing_returns_one(self) -> None:
        missing = tls.inspect_tls(tls_paths(self.config_dir), hostnames=(), ip_addresses=())
        stdout = io.StringIO()
        with (
            patch("deckbox.cli.inspect_tls", return_value=missing),
            patch("deckbox.cli.setup_local_ca") as issue,
            redirect_stdout(stdout),
        ):
            exit_code = cli.setup_tls(self.setup_args(status=True))

        self.assertEqual(exit_code, 1)
        issue.assert_not_called()
        self.assertIn("deckbox setup-tls", stdout.getvalue())
        self.assertFalse((self.config_dir / "tls").exists())

    def test_setup_tls_persists_requested_names_before_issuing(self) -> None:
        paths = tls_paths(self.config_dir)
        issued = Mock(paths=paths, ca_fingerprint="abc123")
        with patch("deckbox.cli.setup_local_ca", return_value=issued) as issue:
            exit_code = cli.setup_tls(self.setup_args(hostname=["spark-1"], ip=["192.0.2.10"]))

        self.assertEqual(exit_code, 0)
        saved = load_config_file()
        self.assertEqual(saved["tls_hostnames"], ["spark-1"])
        self.assertEqual(saved["tls_ips"], ["192.0.2.10"])
        self.assertEqual(issue.call_args.kwargs["hostnames"], ("spark-1",))
        self.assertEqual(issue.call_args.kwargs["ip_addresses"], ("192.0.2.10",))

    def test_changed_sans_without_renew_do_not_mutate_config_or_certificates(self) -> None:
        initial = cli.setup_tls(self.setup_args(hostname=["spark-1"]))
        self.assertEqual(initial, 0)
        paths = tls_paths(self.config_dir)
        ca_before = paths.ca_cert.read_bytes()
        leaf_before = paths.leaf_cert.read_bytes()
        config_before = self.config_path.read_bytes()

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = cli.setup_tls(self.setup_args(hostname=["new.example"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("--renew", stderr.getvalue())
        self.assertEqual(self.config_path.read_bytes(), config_before)
        self.assertEqual(paths.ca_cert.read_bytes(), ca_before)
        self.assertEqual(paths.leaf_cert.read_bytes(), leaf_before)

    def test_changed_persisted_lists_require_renew_even_when_leaf_covers_them(self) -> None:
        paths = tls_paths(self.config_dir)
        tls.setup_local_ca(
            paths,
            hostnames=("one.example", "two.example", "extra.example"),
            ip_addresses=("192.0.2.10", "192.0.2.11"),
        )
        config.save_config_file(
            {"tls_hostnames": ["one.example", "two.example"], "tls_ips": ["192.0.2.10"]}
        )
        before = {
            path: path.read_bytes()
            for path in (
                paths.ca_cert,
                paths.ca_key,
                paths.leaf_cert,
                paths.leaf_key,
                self.config_path,
            )
        }

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = cli.setup_tls(self.setup_args(hostname=["one.example"], ip=["192.0.2.11"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("--renew", stderr.getvalue())
        self.assertEqual(
            {path: path.read_bytes() for path in before},
            before,
        )

    def test_changed_persisted_lists_require_renew_when_existing_leaf_is_corrupt(self) -> None:
        self.assertEqual(cli.setup_tls(self.setup_args(hostname=["original.example"])), 0)
        paths = tls_paths(self.config_dir)
        paths.leaf_cert.write_bytes(b"not a certificate")
        before = {
            path: path.read_bytes()
            for path in (
                paths.ca_cert,
                paths.ca_key,
                paths.leaf_cert,
                paths.leaf_key,
                self.config_path,
            )
        }

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = cli.setup_tls(self.setup_args(hostname=["replacement.example"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("--renew", stderr.getvalue())
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_issuance_failure_does_not_persist_requested_tls_configuration(self) -> None:
        paths = tls_paths(self.config_dir)
        stderr = io.StringIO()
        with (
            patch("deckbox.cli.setup_local_ca", side_effect=tls.TLSError("issuer failed")),
            redirect_stderr(stderr),
        ):
            exit_code = cli.setup_tls(self.setup_args(hostname=["replacement.example"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("issuer failed", stderr.getvalue())
        self.assertFalse(self.config_path.exists())
        self.assertFalse(paths.directory.exists())

    def test_config_save_failure_rolls_back_initial_tls_issuance(self) -> None:
        paths = tls_paths(self.config_dir)
        stderr = io.StringIO()
        with (
            patch("deckbox.cli.save_config_file", side_effect=OSError("config save failed")),
            redirect_stderr(stderr),
        ):
            exit_code = cli.setup_tls(self.setup_args(hostname=["new.example"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("config save failed", stderr.getvalue())
        self.assertFalse(self.config_path.exists())
        self.assertFalse(paths.directory.exists())

    def test_config_save_failure_rolls_back_leaf_renewal(self) -> None:
        self.assertEqual(cli.setup_tls(self.setup_args(hostname=["original.example"])), 0)
        paths = tls_paths(self.config_dir)
        before = {
            path: path.read_bytes()
            for path in (
                self.config_path,
                paths.ca_cert,
                paths.ca_key,
                paths.leaf_cert,
                paths.leaf_key,
            )
        }
        stderr = io.StringIO()
        with (
            patch("deckbox.cli.save_config_file", side_effect=OSError("config save failed")),
            redirect_stderr(stderr),
        ):
            exit_code = cli.setup_tls(self.setup_args(renew=True, hostname=["replacement.example"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("config save failed", stderr.getvalue())
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_real_config_replace_failure_cleans_up_initial_tls_transaction(self) -> None:
        paths = tls_paths(self.config_dir)
        self.config_dir.mkdir()
        self.config_path.mkdir()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = cli.setup_tls(self.setup_args(hostname=["new.example"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("Is a directory", stderr.getvalue())
        self.assertTrue(self.config_path.is_dir())
        self.assertFalse(self.config_path.with_suffix(".yaml.tmp").exists())
        self.assertFalse(paths.directory.exists())

    def test_real_config_replace_failure_cleans_up_renewal_tls_transaction(self) -> None:
        self.assertEqual(cli.setup_tls(self.setup_args(hostname=["original.example"])), 0)
        paths = tls_paths(self.config_dir)
        before = {
            path: path.read_bytes()
            for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
        }
        self.config_path.unlink()
        self.config_path.mkdir()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = cli.setup_tls(self.setup_args(renew=True, hostname=["replacement.example"]))

        self.assertEqual(exit_code, 1)
        self.assertIn("Is a directory", stderr.getvalue())
        self.assertTrue(self.config_path.is_dir())
        self.assertFalse(self.config_path.with_suffix(".yaml.tmp").exists())
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_status_with_ipv6_host_reports_missing_tls_without_address_family_error(self) -> None:
        args = argparse.Namespace(
            path=None,
            dir=str(self.directory),
            host="::1",
            port=9443,
            log_level=None,
            allow_outside_root=None,
        )
        stdout = io.StringIO()
        with (
            patch("deckbox.service.is_installed", return_value=False),
            redirect_stdout(stdout),
        ):
            exit_code = cli.status(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("TLS readiness : not ready", stdout.getvalue())
        self.assertIn("not listening", stdout.getvalue())

    def test_setup_tls_renew_replaces_leaf_without_rotating_ca(self) -> None:
        self.assertEqual(cli.setup_tls(self.setup_args(hostname=["spark-1"])), 0)
        paths = tls_paths(self.config_dir)
        ca_before = paths.ca_cert.read_bytes()
        leaf_before = paths.leaf_cert.read_bytes()

        self.assertEqual(cli.setup_tls(self.setup_args(renew=True, hostname=["new.example"])), 0)

        self.assertEqual(paths.ca_cert.read_bytes(), ca_before)
        self.assertNotEqual(paths.leaf_cert.read_bytes(), leaf_before)
        self.assertEqual(load_config_file()["tls_hostnames"], ["new.example"])

    def test_config_set_parses_tls_lists_and_rejects_empty_items(self) -> None:
        self.assertEqual(
            config.parse_string_list("one.example, two.example", key="tls_hostnames"),
            ["one.example", "two.example"],
        )
        with self.assertRaises(ValueError):
            config.parse_string_list("192.0.2.10,,::1", key="tls_ips")
        self.assertEqual(
            cli.config_cmd(
                argparse.Namespace(
                    action="set", key="tls_hostnames", value="one.example, two.example"
                )
            ),
            0,
        )
        self.assertEqual(load_config_file()["tls_hostnames"], ["one.example", "two.example"])
        self.assertEqual(
            cli.config_cmd(
                argparse.Namespace(action="set", key="tls_ips", value="192.0.2.10,,::1")
            ),
            1,
        )

    def test_parser_rejects_no_auth_and_accepts_repeated_tls_values(self) -> None:
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--no-auth"])

        args = parser.parse_args(
            [
                "setup-tls",
                "--hostname",
                "spark-1",
                "--hostname",
                "host.example",
                "--ip",
                "192.0.2.10",
            ]
        )
        self.assertEqual(args.hostname, ["spark-1", "host.example"])
        self.assertEqual(args.ip, ["192.0.2.10"])

    def test_served_urls_are_https_and_handle_wildcard_and_ipv6(self) -> None:
        for host, expected in (
            ("0.0.0.0", "https://localhost:9443"),
            ("127.0.0.1", "https://127.0.0.1:9443"),
            ("::1", "https://[::1]:9443"),
        ):
            with self.subTest(host=host):
                self.assertEqual(
                    cli._served_url(ResolvedConfig(self.directory, host, 9443, "info")),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
