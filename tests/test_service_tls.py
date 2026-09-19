"""Contract tests for Deckbox's TLS-gated systemd lifecycle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckbox import config, service, tls
from deckbox.config import resolve
from deckbox.tls import TLSError


class ServiceTlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.config_dir = root / "config"
        self.unit_dir = root / "systemd" / "user"
        self.unit_path = self.unit_dir / service.SERVICE_NAME
        self.directory = root / "served"
        self.directory.mkdir()
        self.cfg = resolve(
            directory=self.directory,
            host="127.0.0.1",
            port=9443,
            log_level="warning",
            tls_hostnames=("spark-1",),
            tls_ips=("192.0.2.10",),
        )
        self.patches = [
            patch.object(config, "CONFIG_DIR", self.config_dir),
            patch.object(config, "CONFIG_PATH", self.config_dir / "config.yaml"),
            patch.object(tls, "CONFIG_DIR", self.config_dir),
            patch.object(service, "USER_UNIT_DIR", self.unit_dir),
            patch.object(service, "UNIT_PATH", self.unit_path),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_install_refuses_invalid_tls_before_writes_or_systemctl(self) -> None:
        with (
            patch("deckbox.service.require_tls", side_effect=TLSError("missing")),
            patch("deckbox.service._run") as run_systemctl,
            patch("deckbox.service.save_config_file") as save_config,
            self.assertRaisesRegex(RuntimeError, "setup-tls"),
        ):
            service.install(self.cfg, enable_linger=False)

        run_systemctl.assert_not_called()
        save_config.assert_not_called()
        self.assertFalse(self.unit_path.exists())

    def test_start_and_restart_refuse_invalid_tls_without_systemctl(self) -> None:
        systemctl_calls = []
        for operation in (service.start, service.restart):
            with (
                self.subTest(operation=operation.__name__),
                patch("deckbox.service.require_tls", side_effect=TLSError("missing")),
                patch("deckbox.service._run") as run_systemctl,
                self.assertRaisesRegex(RuntimeError, "setup-tls"),
            ):
                operation(self.cfg)
            systemctl_calls.append(run_systemctl)
        for run_systemctl in systemctl_calls:
            run_systemctl.assert_not_called()

    def test_valid_tls_allows_start_and_restart(self) -> None:
        with (
            patch("deckbox.service.require_tls") as require_tls,
            patch("deckbox.service._run") as run_systemctl,
        ):
            service.start(self.cfg)
            service.restart(self.cfg)

        self.assertEqual(require_tls.call_count, 2)
        self.assertEqual(
            run_systemctl.call_args_list[0].args[0],
            ["systemctl", "--user", "start", service.SERVICE_NAME],
        )
        self.assertEqual(
            run_systemctl.call_args_list[1].args[0],
            ["systemctl", "--user", "restart", service.SERVICE_NAME],
        )

    def test_generated_unit_is_https_only_and_keeps_explicit_configuration(self) -> None:
        unit = service._unit_text(self.cfg)

        self.assertIn("UMask=0077", unit)
        self.assertIn("run --dir", unit)
        self.assertIn(str(self.directory), unit)
        self.assertIn("--host 127.0.0.1", unit)
        self.assertIn("--port 9443", unit)
        self.assertIn("--log-level warning", unit)
        self.assertNotIn("--no-auth", unit)
        self.assertNotIn("leaf.key", unit)
        self.assertNotIn("leaf.crt", unit)

    def test_install_persists_tls_configuration_after_tls_validation(self) -> None:
        with (
            patch("deckbox.service.require_tls") as require_tls,
            patch("deckbox.service.systemctl_available", return_value=True),
            patch("deckbox.service._run") as run_systemctl,
            patch("deckbox.service.resolve_tool_bin", return_value=["/usr/bin/deckbox"]),
        ):
            service.install(self.cfg, enable_linger=False)

        require_tls.assert_called_once()
        saved = config.load_config_file()
        self.assertEqual(saved["tls_hostnames"], ["spark-1"])
        self.assertEqual(saved["tls_ips"], ["192.0.2.10"])
        self.assertTrue(self.unit_path.exists())
        self.assertEqual(run_systemctl.call_count, 2)


if __name__ == "__main__":
    unittest.main()
