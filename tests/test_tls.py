"""Contract tests for Deckbox's locally managed TLS material."""

from __future__ import annotations

import ipaddress
import re
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from deckbox import config, tls
from deckbox.config import resolve
from deckbox.tls import (
    TLSError,
    TLSIssue,
    TLSPaths,
    ca_bytes,
    inspect_tls,
    require_tls,
    setup_local_ca,
    tls_paths,
)


class TLSPathTests(unittest.TestCase):
    def test_tls_paths_are_fixed_below_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)

            paths = tls_paths(config_dir)

            self.assertEqual(paths.directory, config_dir / "tls")
            self.assertEqual(paths.ca_cert, config_dir / "tls" / "ca.crt")
            self.assertEqual(paths.ca_key, config_dir / "tls" / "ca.key")
            self.assertEqual(paths.leaf_cert, config_dir / "tls" / "leaf.crt")
            self.assertEqual(paths.leaf_key, config_dir / "tls" / "leaf.key")


class TLSSetupTests(unittest.TestCase):
    def write_valid_tls_material(
        self,
        paths: TLSPaths,
        *,
        ca_not_after: datetime,
        leaf_not_after: datetime,
    ) -> None:
        paths.directory.mkdir(mode=0o700)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(UTC)
        ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Deckbox Local CA")])
        ca_certificate = (
            x509.CertificateBuilder()
            .subject_name(ca_subject)
            .issuer_name(ca_subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(ca_not_after)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf_certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(leaf_not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                        x509.IPAddress(ipaddress.ip_address("::1")),
                    ]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        paths.ca_key.write_bytes(
            ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        paths.ca_cert.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
        paths.leaf_key.write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        paths.leaf_cert.write_bytes(leaf_certificate.public_bytes(serialization.Encoding.PEM))
        paths.ca_key.chmod(0o600)
        paths.leaf_key.chmod(0o600)
        paths.ca_cert.chmod(0o644)
        paths.leaf_cert.chmod(0o644)

    def test_setup_generates_secure_ca_and_leaf_with_requested_sans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))

            status = setup_local_ca(
                paths,
                hostnames=("Deckbox.Example",),
                ip_addresses=("192.0.2.10",),
            )

            self.assertTrue(status.ready)
            self.assertEqual(status.dns_names, ("localhost", "deckbox.example"))
            self.assertEqual(status.ip_addresses, ("127.0.0.1", "::1", "192.0.2.10"))
            self.assertIsNotNone(status.ca_fingerprint)
            self.assertIsNotNone(status.leaf_not_after)
            self.assertEqual(paths.directory.stat().st_mode & 0o777, 0o700)
            for key in (paths.ca_key, paths.leaf_key):
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            for certificate in (paths.ca_cert, paths.leaf_cert):
                self.assertEqual(certificate.stat().st_mode & 0o777, 0o644)

            ca = x509.load_pem_x509_certificate(paths.ca_cert.read_bytes())
            leaf = x509.load_pem_x509_certificate(paths.leaf_cert.read_bytes())
            self.assertEqual(ca.subject.rfc4514_string(), "CN=Deckbox Local CA")
            self.assertEqual(ca.issuer, ca.subject)
            self.assertIsInstance(ca.public_key(), rsa.RSAPublicKey)
            self.assertEqual(ca.public_key().key_size, 2048)
            self.assertEqual(ca.signature_hash_algorithm.name, "sha256")
            constraints = ca.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
            self.assertTrue(constraints.ca)
            self.assertEqual(constraints.path_length, 0)
            key_usage = ca.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
            self.assertTrue(key_usage.key_cert_sign)
            self.assertTrue(key_usage.crl_sign)
            self.assertEqual(ca.fingerprint(hashes.SHA256()).hex(), status.ca_fingerprint)
            self.assertEqual((ca.not_valid_after_utc - ca.not_valid_before_utc).days, 3650)

            self.assertEqual(leaf.issuer, ca.subject)
            self.assertIsInstance(leaf.public_key(), rsa.RSAPublicKey)
            self.assertEqual(leaf.public_key().key_size, 2048)
            self.assertEqual(leaf.signature_hash_algorithm.name, "sha256")
            leaf_constraints = leaf.extensions.get_extension_for_oid(
                ExtensionOID.BASIC_CONSTRAINTS
            ).value
            self.assertFalse(leaf_constraints.ca)
            leaf_usage = leaf.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
            self.assertTrue(leaf_usage.digital_signature)
            self.assertTrue(leaf_usage.key_encipherment)
            eku = leaf.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
            self.assertEqual(list(eku), [ExtendedKeyUsageOID.SERVER_AUTH])
            sans = leaf.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            ).value
            self.assertEqual(
                {str(value) for value in sans},
                {
                    "<DNSName(value='localhost')>",
                    "<DNSName(value='deckbox.example')>",
                    "<IPAddress(value=127.0.0.1)>",
                    "<IPAddress(value=::1)>",
                    "<IPAddress(value=192.0.2.10)>",
                },
            )
            self.assertEqual((leaf.not_valid_after_utc - leaf.not_valid_before_utc).days, 397)

    def test_setup_is_idempotent_when_existing_material_covers_requested_sans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            requested_dns = ("deckbox.example",)
            requested_ips = ("192.0.2.10",)
            setup_local_ca(paths, hostnames=requested_dns, ip_addresses=requested_ips)
            original = {
                path: path.read_bytes()
                for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
            }

            status = setup_local_ca(paths, hostnames=requested_dns, ip_addresses=requested_ips)

            self.assertTrue(status.ready)
            self.assertEqual(
                {path: path.read_bytes() for path in original},
                original,
            )

    def test_renew_replaces_only_the_leaf_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            requested_dns = ("deckbox.example",)
            requested_ips = ("192.0.2.10",)
            setup_local_ca(paths, hostnames=requested_dns, ip_addresses=requested_ips)
            original_ca = (paths.ca_cert.read_bytes(), paths.ca_key.read_bytes())
            original_leaf = (paths.leaf_cert.read_bytes(), paths.leaf_key.read_bytes())

            status = setup_local_ca(
                paths,
                hostnames=requested_dns,
                ip_addresses=requested_ips,
                renew=True,
            )

            self.assertTrue(status.ready)
            self.assertEqual((paths.ca_cert.read_bytes(), paths.ca_key.read_bytes()), original_ca)
            self.assertNotEqual(
                (paths.leaf_cert.read_bytes(), paths.leaf_key.read_bytes()), original_leaf
            )

    def test_renewed_leaf_expires_before_a_near_expiry_ca(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            now = datetime.now(UTC)
            ca_not_after = now + timedelta(days=2)
            self.write_valid_tls_material(
                paths,
                ca_not_after=ca_not_after,
                leaf_not_after=now + timedelta(days=1),
            )

            status = setup_local_ca(paths, hostnames=(), ip_addresses=(), renew=True)

            self.assertTrue(status.ready)
            self.assertIsNotNone(status.leaf_not_after)
            self.assertLess(status.leaf_not_after, ca_not_after)

    def test_inspection_rejects_leaf_that_outlives_its_issuer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            now = datetime.now(UTC)
            self.write_valid_tls_material(
                paths,
                ca_not_after=now + timedelta(days=1),
                leaf_not_after=now + timedelta(days=2),
            )

            status = inspect_tls(paths, hostnames=(), ip_addresses=())

            self.assertFalse(status.ready)
            self.assertEqual(status.reason, TLSIssue.LEAF_OUTLIVES_ISSUER)
            self.assertIn("outlives", status.detail)

    def test_setup_reissues_missing_sans_using_reason_not_presentation_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            setup_local_ca(paths, hostnames=(), ip_addresses=())
            original_inspect_tls = inspect_tls

            def reworded_inspection(*args: object, **kwargs: object) -> object:
                return replace(
                    original_inspect_tls(*args, **kwargs),
                    detail="Presentation text must not control lifecycle behavior.",
                )

            with patch.object(tls, "inspect_tls", side_effect=reworded_inspection):
                status = setup_local_ca(paths, hostnames=("new-name.example",), ip_addresses=())

            self.assertTrue(status.ready)
            self.assertTrue(
                inspect_tls(paths, hostnames=("new-name.example",), ip_addresses=()).ready
            )

    def test_renew_repairs_leaf_that_outlives_its_valid_ca_without_rotating_ca(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            now = datetime.now(UTC)
            self.write_valid_tls_material(
                paths,
                ca_not_after=now + timedelta(days=2),
                leaf_not_after=now + timedelta(days=3),
            )
            original_ca = (paths.ca_cert.read_bytes(), paths.ca_key.read_bytes())
            original_leaf = (paths.leaf_cert.read_bytes(), paths.leaf_key.read_bytes())
            self.assertFalse(inspect_tls(paths, hostnames=(), ip_addresses=()).ready)

            status = setup_local_ca(paths, hostnames=(), ip_addresses=(), renew=True)

            self.assertTrue(status.ready)
            self.assertEqual((paths.ca_cert.read_bytes(), paths.ca_key.read_bytes()), original_ca)
            self.assertNotEqual(
                (paths.leaf_cert.read_bytes(), paths.leaf_key.read_bytes()), original_leaf
            )
            self.assertTrue(inspect_tls(paths, hostnames=(), ip_addresses=()).ready)

    def test_renew_reissues_outliving_leaf_using_reason_not_presentation_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            now = datetime.now(UTC)
            self.write_valid_tls_material(
                paths,
                ca_not_after=now + timedelta(days=2),
                leaf_not_after=now + timedelta(days=3),
            )
            self.assertEqual(
                inspect_tls(paths, hostnames=(), ip_addresses=()).reason,
                TLSIssue.LEAF_OUTLIVES_ISSUER,
            )
            original_inspect_tls = inspect_tls

            def reworded_inspection(*args: object, **kwargs: object) -> object:
                return replace(
                    original_inspect_tls(*args, **kwargs),
                    detail="Presentation text must not control lifecycle behavior.",
                )

            with patch.object(tls, "inspect_tls", side_effect=reworded_inspection):
                status = setup_local_ca(paths, hostnames=(), ip_addresses=(), renew=True)

            self.assertTrue(status.ready)
            self.assertTrue(inspect_tls(paths, hostnames=(), ip_addresses=()).ready)

    def test_inspection_requires_a_leaf_to_cover_newly_requested_sans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            setup_local_ca(paths, hostnames=(), ip_addresses=())

            status = inspect_tls(
                paths,
                hostnames=("new-name.example",),
                ip_addresses=("192.0.2.10",),
            )

            self.assertFalse(status.ready)
            self.assertEqual(status.reason, TLSIssue.MISSING_SAN_COVERAGE)
            self.assertIn("SAN", status.detail)

    def test_setup_rejects_invalid_configured_dns_names_and_ip_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            for hostname in ("*.example.test", "https://example.test", "name/path", "bad..name"):
                with self.subTest(hostname=hostname), self.assertRaises(TLSError):
                    setup_local_ca(paths, hostnames=(hostname,), ip_addresses=())
            for address in ("not-an-address", "0.0.0.0", "::"):
                with self.subTest(address=address), self.assertRaises(TLSError):
                    setup_local_ca(paths, hostnames=(), ip_addresses=(address,))

    def test_setup_refuses_to_rotate_a_partial_ca(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            paths.directory.mkdir(mode=0o700)
            paths.ca_cert.write_bytes(b"partial CA")
            before = paths.ca_cert.read_bytes()

            with self.assertRaisesRegex(TLSError, "CA"):
                setup_local_ca(paths, hostnames=(), ip_addresses=())

            self.assertEqual(paths.ca_cert.read_bytes(), before)
            self.assertFalse(paths.ca_key.exists())

    def test_setup_replaces_a_missing_leaf_without_rotating_the_valid_ca(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            setup_local_ca(paths, hostnames=(), ip_addresses=())
            original_ca = (paths.ca_cert.read_bytes(), paths.ca_key.read_bytes())
            paths.leaf_cert.unlink()
            paths.leaf_key.unlink()

            status = setup_local_ca(paths, hostnames=(), ip_addresses=())

            self.assertTrue(status.ready)
            self.assertEqual((paths.ca_cert.read_bytes(), paths.ca_key.read_bytes()), original_ca)
            self.assertTrue(paths.leaf_cert.exists())
            self.assertTrue(paths.leaf_key.exists())

    def test_setup_reissues_leaf_when_requested_sans_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            setup_local_ca(paths, hostnames=(), ip_addresses=())
            original_ca = (paths.ca_cert.read_bytes(), paths.ca_key.read_bytes())
            original_leaf = (paths.leaf_cert.read_bytes(), paths.leaf_key.read_bytes())

            status = setup_local_ca(
                paths,
                hostnames=("new-name.example",),
                ip_addresses=("192.0.2.10",),
            )

            self.assertTrue(status.ready)
            self.assertEqual((paths.ca_cert.read_bytes(), paths.ca_key.read_bytes()), original_ca)
            self.assertNotEqual(
                (paths.leaf_cert.read_bytes(), paths.leaf_key.read_bytes()), original_leaf
            )
            self.assertEqual(status.dns_names, ("localhost", "new-name.example"))

    def test_setup_corrects_permissions_without_rotating_valid_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            setup_local_ca(paths, hostnames=(), ip_addresses=())
            original = {
                path: path.read_bytes()
                for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
            }
            paths.directory.chmod(0o755)
            for path in original:
                path.chmod(0o777)

            status = setup_local_ca(paths, hostnames=(), ip_addresses=())

            self.assertTrue(status.ready)
            self.assertEqual({path: path.read_bytes() for path in original}, original)
            self.assertEqual(paths.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(paths.ca_key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(paths.leaf_key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(paths.ca_cert.stat().st_mode & 0o777, 0o644)
            self.assertEqual(paths.leaf_cert.stat().st_mode & 0o777, 0o644)

    def test_ca_bytes_rejects_corrupt_or_non_ca_certificates_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            paths.directory.mkdir(mode=0o700)
            paths.ca_cert.write_bytes(b"not a certificate")
            corrupt_before = paths.ca_cert.read_bytes()

            self.assertIsNone(ca_bytes(paths))
            self.assertEqual(paths.ca_cert.read_bytes(), corrupt_before)
            self.assertEqual(paths.directory.stat().st_mode & 0o777, 0o700)

        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            setup_local_ca(paths, hostnames=(), ip_addresses=())
            paths.ca_cert.write_bytes(paths.leaf_cert.read_bytes())
            leaf_before = paths.ca_cert.read_bytes()

            self.assertIsNone(ca_bytes(paths))
            self.assertEqual(paths.ca_cert.read_bytes(), leaf_before)
            self.assertEqual(paths.directory.stat().st_mode & 0o777, 0o700)

    def test_ca_bytes_returns_none_when_ca_path_is_a_directory_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            paths.directory.mkdir(mode=0o700)
            paths.ca_cert.mkdir()
            mode_before = paths.directory.stat().st_mode & 0o777

            self.assertIsNone(ca_bytes(paths))

            self.assertTrue(paths.ca_cert.is_dir())
            self.assertEqual(paths.directory.stat().st_mode & 0o777, mode_before)

    def test_hostname_ip_literal_becomes_an_ip_san_not_a_dns_san(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))

            status = setup_local_ca(paths, hostnames=("192.0.2.10",), ip_addresses=())

            leaf = x509.load_pem_x509_certificate(paths.leaf_cert.read_bytes())
            sans = leaf.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            ).value
            self.assertNotIn("192.0.2.10", status.dns_names)
            self.assertIn("192.0.2.10", status.ip_addresses)
            self.assertNotIn("192.0.2.10", sans.get_values_for_type(x509.DNSName))
            self.assertIn(
                "192.0.2.10", {str(value) for value in sans.get_values_for_type(x509.IPAddress)}
            )

    def test_inspection_and_ca_read_have_no_filesystem_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))

            status = inspect_tls(paths, hostnames=(), ip_addresses=())

            self.assertFalse(status.ready)
            self.assertFalse(paths.directory.exists())
            self.assertIsNone(ca_bytes(paths))
            self.assertFalse(paths.directory.exists())

    def test_tls_remediation_uses_setup_tls_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))

            status = inspect_tls(paths, hostnames=(), ip_addresses=())

            self.assertIn("deckbox setup-tls", status.detail)
            self.assertNotIn("deckbox tls setup", status.detail)

    def test_require_tls_raises_the_inspection_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tls_paths(Path(directory))
            inspected = inspect_tls(paths, hostnames=(), ip_addresses=())

            with self.assertRaisesRegex(TLSError, re.escape(inspected.detail)):
                require_tls(paths, hostnames=(), ip_addresses=())


class TLSConfigTests(unittest.TestCase):
    def test_resolve_converts_yaml_tls_lists_to_tuples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_path = config_dir / "config.yaml"
            config_path.write_text(
                "tls_hostnames:\n- Deckbox.Example\n- files.example\ntls_ips:\n- 192.0.2.10\n",
                encoding="utf-8",
            )

            with (
                patch.object(config, "CONFIG_DIR", config_dir),
                patch.object(config, "CONFIG_PATH", config_path),
            ):
                resolved = resolve()

            self.assertEqual(resolved.tls_hostnames, ("Deckbox.Example", "files.example"))
            self.assertEqual(resolved.tls_ips, ("192.0.2.10",))

    def test_resolve_rejects_non_string_tls_yaml_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_path = config_dir / "config.yaml"
            config_path.write_text("tls_hostnames:\n- valid.example\n- 42\n", encoding="utf-8")

            with (
                patch.object(config, "CONFIG_DIR", config_dir),
                patch.object(config, "CONFIG_PATH", config_path),
                self.assertRaisesRegex(ValueError, "tls_hostnames must be a list of strings"),
            ):
                resolve()


if __name__ == "__main__":
    unittest.main()
