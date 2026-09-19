"""HTTPS CA-bootstrap and PAM-boundary contract tests."""

from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from deckbox import config, tls
from deckbox.auth import _PUBLIC_PATHS
from deckbox.config import ResolvedConfig
from deckbox.server import create_app
from deckbox.setup_page import render_setup_page


class TlsBootstrapRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_dir = Path(self.temporary_directory.name) / "config"
        self.patches = [
            patch.object(config, "CONFIG_DIR", self.config_dir),
            patch.object(tls, "CONFIG_DIR", self.config_dir),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = Path(self.temporary_directory.name) / "served"
        self.root.mkdir()
        self.paths = tls.tls_paths()
        tls.setup_local_ca(self.paths, hostnames=("spark-1",), ip_addresses=())
        self.ca_pem = self.paths.ca_cert.read_bytes()
        self.client = TestClient(
            create_app(
                ResolvedConfig(
                    directory=self.root,
                    host="127.0.0.1",
                    port=9443,
                    log_level="warning",
                ),
                auth_required=True,
            )
        )

    def test_setup_page_with_ca_has_fixed_download_and_trust_instructions(self) -> None:
        fingerprint = "AA:BB:CC"

        page = render_setup_page(fingerprint)

        self.assertIn('href="/ca.crt"', page)
        self.assertIn("deckbox-ca.crt", page)
        self.assertIn(fingerprint, page)
        self.assertIn("public CA only", page)
        self.assertIn("no private keys", page)
        self.assertIn("Keychain Access", page)
        self.assertIn("Always Trust", page)
        self.assertIn("security add-trusted-cert", page)
        self.assertIn("full restart", page)
        self.assertIn("out-of-band", page)
        self.assertIn("deckbox setup-tls --status", page)
        self.assertIn("deckbox doctor", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("http://", page)

    def test_setup_page_without_ca_has_no_broken_download_link(self) -> None:
        page = render_setup_page(None)

        self.assertIn("deckbox setup-tls", page)
        self.assertNotIn('href="/ca.crt"', page)

    def test_only_exact_health_setup_and_ca_paths_are_public(self) -> None:
        self.assertEqual(_PUBLIC_PATHS, frozenset({"/health", "/setup", "/ca.crt"}))
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/setup").status_code, 200)
        self.assertEqual(self.client.get("/ca.crt").status_code, 200)

        for path in (
            "/",
            "/assets/highlight.css",
            "/static/css/style.css",
            "/api/dot",
            "/healthcheck",
            "/setup/extra",
            "/ca.crt/extra",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_ca_route_serves_only_fixed_valid_ca_as_attachment_without_hsts(self) -> None:
        response = self.client.get("/ca.crt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.ca_pem)
        self.assertEqual(response.headers["content-type"], "application/x-x509-ca-cert")
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="deckbox-ca.crt"',
        )
        self.assertNotIn(b"PRIVATE KEY", response.content)
        self.assertNotIn("strict-transport-security", response.headers)

    def test_ca_route_returns_not_found_for_absent_or_invalid_fixed_ca(self) -> None:
        self.paths.ca_cert.unlink()
        self.assertEqual(self.client.get("/ca.crt").status_code, 404)
        unavailable_setup = self.client.get("/setup")
        self.assertEqual(unavailable_setup.status_code, 200)
        self.assertIn("deckbox setup-tls", unavailable_setup.text)
        self.assertNotIn('href="/ca.crt"', unavailable_setup.text)

        self.paths.ca_cert.write_bytes(self.paths.leaf_cert.read_bytes())
        self.assertEqual(self.client.get("/ca.crt").status_code, 404)

    def test_setup_response_uses_the_canonical_tls_status_fingerprint(self) -> None:
        status = tls.inspect_tls(self.paths, hostnames=("spark-1",), ip_addresses=())
        response = self.client.get("/setup")

        self.assertTrue(status.ready)
        self.assertIsNotNone(status.ca_fingerprint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn(status.ca_fingerprint, response.text)
        self.assertNotIn(hashlib.sha256(self.ca_pem).hexdigest(), response.text)
        self.assertNotIn("strict-transport-security", response.headers)

    def test_protected_routes_require_exact_launch_user(self) -> None:
        wrong_user = self._basic("wrong-user", "password")
        self.assertEqual(
            self.client.get("/", headers={"Authorization": wrong_user}).status_code, 401
        )

        app = self.client.app
        correct_user = self._basic(app.state.launch_user, "password")
        with patch("deckbox.auth._pam_authenticate", return_value=True):
            self.assertEqual(
                self.client.get("/", headers={"Authorization": correct_user}).status_code, 200
            )

    @staticmethod
    def _basic(username: str, password: str) -> str:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {token}"


if __name__ == "__main__":
    unittest.main()
