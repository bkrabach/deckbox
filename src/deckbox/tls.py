"""Local TLS material paths for Deckbox."""

from __future__ import annotations

import ipaddress
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import cast

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from deckbox.config import CONFIG_DIR


@dataclass(frozen=True)
class TLSPaths:
    """The fixed locations of Deckbox-owned TLS files."""

    directory: Path
    ca_cert: Path
    ca_key: Path
    leaf_cert: Path
    leaf_key: Path


class TLSIssue(str, Enum):
    """Machine-readable reason TLS material is not ready."""

    MISSING = "missing"
    CA_INCOMPLETE = "ca_incomplete"
    CA_MISSING = "ca_missing"
    LEAF_INCOMPLETE = "leaf_incomplete"
    UNSAFE_PERMISSIONS = "unsafe_permissions"
    CA_CORRUPT = "ca_corrupt"
    CA_INVALID = "ca_invalid"
    LEAF_MISSING = "leaf_missing"
    LEAF_CORRUPT = "leaf_corrupt"
    CERTIFICATE_TIME_INVALID = "certificate_time_invalid"
    LEAF_INVALID = "leaf_invalid"
    LEAF_OUTLIVES_ISSUER = "leaf_outlives_issuer"
    MISSING_SAN_COVERAGE = "missing_san_coverage"


@dataclass(frozen=True)
class TLSStatus:
    """The readiness of locally managed TLS material."""

    ready: bool
    detail: str
    paths: TLSPaths
    ca_fingerprint: str | None
    dns_names: tuple[str, ...]
    ip_addresses: tuple[str, ...]
    leaf_not_after: datetime | None
    reason: TLSIssue | None = None


class TLSError(RuntimeError):
    """TLS material is absent, invalid, or cannot be safely updated."""


_LEAF_CA_EXPIRY_MARGIN = timedelta(minutes=1)


def tls_paths(config_dir: Path | None = None) -> TLSPaths:
    """Return the app-owned TLS paths below *config_dir*."""
    directory = (config_dir if config_dir is not None else CONFIG_DIR) / "tls"
    return TLSPaths(
        directory=directory,
        ca_cert=directory / "ca.crt",
        ca_key=directory / "ca.key",
        leaf_cert=directory / "leaf.crt",
        leaf_key=directory / "leaf.key",
    )


def ca_bytes(paths: TLSPaths) -> bytes | None:
    """Read a valid local CA certificate without changing the filesystem."""
    try:
        certificate_bytes = paths.ca_cert.read_bytes()
        certificate = x509.load_pem_x509_certificate(certificate_bytes)
        constraints = cast(
            x509.BasicConstraints,
            certificate.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value,
        )
    except (OSError, ValueError, x509.ExtensionNotFound):
        return None
    if not constraints.ca:
        return None
    return certificate_bytes


def _certificate_fingerprint(certificate: x509.Certificate) -> str:
    """Return the canonical SHA-256 fingerprint for an X.509 certificate."""
    return certificate.fingerprint(hashes.SHA256()).hex()


def ca_fingerprint(paths: TLSPaths) -> str | None:
    """Return the canonical fingerprint of the fixed valid CA certificate."""
    certificate_bytes = ca_bytes(paths)
    if certificate_bytes is None:
        return None
    return _certificate_fingerprint(x509.load_pem_x509_certificate(certificate_bytes))


def _names(
    hostnames: tuple[str, ...], ip_addresses: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]]:
    def canonical_dns(name: str) -> str:
        if (
            not isinstance(name, str)
            or not name
            or any(character in name for character in "*:/?#@")
        ):
            raise TLSError(f"Invalid TLS DNS name: {name!r}")
        try:
            canonical = name.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise TLSError(f"Invalid TLS DNS name: {name!r}") from error
        labels = canonical.split(".")
        if len(canonical) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) or len(label) > 63
            for label in labels
        ):
            raise TLSError(f"Invalid TLS DNS name: {name!r}")
        return canonical

    def canonical_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise TLSError(f"Invalid TLS IP address: {address!r}") from error
        if parsed.is_unspecified:
            raise TLSError(f"TLS IP address must not be unspecified: {address!r}")
        return parsed

    dns_name_values = ["localhost"]
    address_values = [canonical_ip(address) for address in ("127.0.0.1", "::1", *ip_addresses)]
    for hostname in hostnames:
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            dns_name_values.append(canonical_dns(hostname))
        else:
            if literal.is_unspecified:
                raise TLSError(f"TLS IP address must not be unspecified: {hostname!r}")
            address_values.append(literal)
    dns_names = tuple(dict.fromkeys(dns_name_values))
    addresses = tuple(dict.fromkeys(address_values))
    return dns_names, addresses


def _write_atomically(path: Path, data: bytes, *, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(temporary_path, path)
        path.chmod(mode)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _status(
    paths: TLSPaths,
    ca_certificate: x509.Certificate,
    leaf_certificate: x509.Certificate,
    dns_names: tuple[str, ...],
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
) -> TLSStatus:
    return TLSStatus(
        ready=True,
        detail="Deckbox TLS material is ready.",
        paths=paths,
        ca_fingerprint=_certificate_fingerprint(ca_certificate),
        dns_names=dns_names,
        ip_addresses=tuple(str(address) for address in addresses),
        leaf_not_after=leaf_certificate.not_valid_after_utc,
    )


def _not_ready(
    paths: TLSPaths,
    reason: TLSIssue,
    detail: str,
    *,
    ca_certificate: x509.Certificate | None = None,
    leaf_certificate: x509.Certificate | None = None,
) -> TLSStatus:
    dns_names: tuple[str, ...] = ()
    ip_addresses: tuple[str, ...] = ()
    if leaf_certificate is not None:
        try:
            san = cast(
                x509.SubjectAlternativeName,
                leaf_certificate.extensions.get_extension_for_oid(
                    ExtensionOID.SUBJECT_ALTERNATIVE_NAME
                ).value,
            )
            dns_names = tuple(san.get_values_for_type(x509.DNSName))
            ip_addresses = tuple(
                str(address) for address in san.get_values_for_type(x509.IPAddress)
            )
        except x509.ExtensionNotFound:
            pass
    return TLSStatus(
        ready=False,
        detail=f"{detail} Run `deckbox setup-tls` to remediate.",
        paths=paths,
        ca_fingerprint=(
            _certificate_fingerprint(ca_certificate) if ca_certificate is not None else None
        ),
        dns_names=dns_names,
        ip_addresses=ip_addresses,
        leaf_not_after=leaf_certificate.not_valid_after_utc if leaf_certificate else None,
        reason=reason,
    )


def _has_mode(path: Path, mode: int) -> bool:
    return path.stat().st_mode & 0o777 == mode


def inspect_tls(
    paths: TLSPaths,
    *,
    hostnames: tuple[str, ...],
    ip_addresses: tuple[str, ...],
) -> TLSStatus:
    """Read and validate existing TLS material without changing the filesystem."""
    dns_names, addresses = _names(hostnames, ip_addresses)
    expected_paths = (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
    present = tuple(path.exists() for path in expected_paths)
    if not any(present):
        return _not_ready(paths, TLSIssue.MISSING, "Deckbox TLS material is missing.")
    if present[0] != present[1]:
        return _not_ready(
            paths, TLSIssue.CA_INCOMPLETE, "Deckbox TLS CA certificate and key are incomplete."
        )
    if not present[0]:
        return _not_ready(paths, TLSIssue.CA_MISSING, "Deckbox TLS CA material is missing.")
    if present[2] != present[3]:
        return _not_ready(
            paths, TLSIssue.LEAF_INCOMPLETE, "Deckbox TLS leaf certificate and key are incomplete."
        )
    if not paths.directory.exists() or not _has_mode(paths.directory, 0o700):
        return _not_ready(
            paths, TLSIssue.UNSAFE_PERMISSIONS, "Deckbox TLS directory permissions must be 0700."
        )
    for path, mode in ((paths.ca_key, 0o600), (paths.leaf_key, 0o600)):
        if path.exists() and not _has_mode(path, mode):
            return _not_ready(
                paths,
                TLSIssue.UNSAFE_PERMISSIONS,
                f"Deckbox TLS key permissions are unsafe: {path.name} must be 0600.",
            )
    for path, mode in ((paths.ca_cert, 0o644), (paths.leaf_cert, 0o644)):
        if path.exists() and not _has_mode(path, mode):
            return _not_ready(
                paths,
                TLSIssue.UNSAFE_PERMISSIONS,
                f"Deckbox TLS certificate permissions are unsafe: {path.name} must be 0644.",
            )

    try:
        ca_certificate = x509.load_pem_x509_certificate(paths.ca_cert.read_bytes())
        ca_key = serialization.load_pem_private_key(paths.ca_key.read_bytes(), password=None)
    except (OSError, ValueError):
        return _not_ready(paths, TLSIssue.CA_CORRUPT, "Deckbox TLS CA material is corrupt.")
    ca_public_key = ca_certificate.public_key()
    if not isinstance(ca_key, rsa.RSAPrivateKey) or not isinstance(ca_public_key, rsa.RSAPublicKey):
        return _not_ready(
            paths,
            TLSIssue.CA_INVALID,
            "Deckbox TLS CA must use an RSA key.",
            ca_certificate=ca_certificate,
        )
    try:
        constraints = cast(
            x509.BasicConstraints,
            ca_certificate.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value,
        )
        usage = cast(
            x509.KeyUsage,
            ca_certificate.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value,
        )
        signature_hash = ca_certificate.signature_hash_algorithm
        if signature_hash is None:
            raise ValueError("CA certificate has no signature hash.")
        ca_public_key.verify(
            ca_certificate.signature,
            ca_certificate.tbs_certificate_bytes,
            padding.PKCS1v15(),
            signature_hash,
        )
    except (InvalidSignature, ValueError, x509.ExtensionNotFound):
        return _not_ready(
            paths,
            TLSIssue.CA_INVALID,
            "Deckbox TLS CA extensions are invalid.",
            ca_certificate=ca_certificate,
        )
    if (
        ca_certificate.subject
        != x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Deckbox Local CA")])
        or ca_certificate.issuer != ca_certificate.subject
        or not constraints.ca
        or constraints.path_length != 0
        or not usage.key_cert_sign
        or not usage.crl_sign
        or ca_public_key.key_size != 2048
        or signature_hash.name != "sha256"
        or ca_key.public_key().public_numbers() != ca_public_key.public_numbers()
    ):
        return _not_ready(
            paths, TLSIssue.CA_INVALID, "Deckbox TLS CA is invalid.", ca_certificate=ca_certificate
        )

    if not present[2]:
        return _not_ready(
            paths,
            TLSIssue.LEAF_MISSING,
            "Deckbox TLS leaf material is missing.",
            ca_certificate=ca_certificate,
        )
    try:
        leaf_certificate = x509.load_pem_x509_certificate(paths.leaf_cert.read_bytes())
        leaf_key = serialization.load_pem_private_key(paths.leaf_key.read_bytes(), password=None)
    except (OSError, ValueError):
        return _not_ready(
            paths,
            TLSIssue.LEAF_CORRUPT,
            "Deckbox TLS leaf material is corrupt.",
            ca_certificate=ca_certificate,
        )
    leaf_public_key = leaf_certificate.public_key()
    if not isinstance(leaf_key, rsa.RSAPrivateKey) or not isinstance(
        leaf_public_key, rsa.RSAPublicKey
    ):
        return _not_ready(
            paths,
            TLSIssue.LEAF_INVALID,
            "Deckbox TLS leaf must use an RSA key.",
            ca_certificate=ca_certificate,
            leaf_certificate=leaf_certificate,
        )
    now = datetime.now(UTC)
    if any(
        certificate.not_valid_before_utc > now or certificate.not_valid_after_utc < now
        for certificate in (ca_certificate, leaf_certificate)
    ):
        return _not_ready(
            paths,
            TLSIssue.CERTIFICATE_TIME_INVALID,
            "Deckbox TLS certificate has expired or is not yet valid.",
            ca_certificate=ca_certificate,
            leaf_certificate=leaf_certificate,
        )
    if leaf_certificate.not_valid_after_utc >= ca_certificate.not_valid_after_utc:
        return _not_ready(
            paths,
            TLSIssue.LEAF_OUTLIVES_ISSUER,
            "Deckbox TLS leaf certificate outlives its issuer.",
            ca_certificate=ca_certificate,
            leaf_certificate=leaf_certificate,
        )
    try:
        constraints = cast(
            x509.BasicConstraints,
            leaf_certificate.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value,
        )
        usage = cast(
            x509.KeyUsage,
            leaf_certificate.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value,
        )
        eku = cast(
            x509.ExtendedKeyUsage,
            leaf_certificate.extensions.get_extension_for_oid(
                ExtensionOID.EXTENDED_KEY_USAGE
            ).value,
        )
        san = cast(
            x509.SubjectAlternativeName,
            leaf_certificate.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            ).value,
        )
        signature_hash = leaf_certificate.signature_hash_algorithm
        if signature_hash is None:
            raise ValueError("Leaf certificate has no signature hash.")
        ca_public_key.verify(
            leaf_certificate.signature,
            leaf_certificate.tbs_certificate_bytes,
            padding.PKCS1v15(),
            signature_hash,
        )
    except (InvalidSignature, ValueError, x509.ExtensionNotFound):
        return _not_ready(
            paths,
            TLSIssue.LEAF_INVALID,
            "Deckbox TLS leaf extensions or signature are invalid.",
            ca_certificate=ca_certificate,
            leaf_certificate=leaf_certificate,
        )
    if (
        constraints.ca
        or not usage.digital_signature
        or not usage.key_encipherment
        or ExtendedKeyUsageOID.SERVER_AUTH not in eku
        or leaf_certificate.issuer != ca_certificate.subject
        or leaf_public_key.key_size != 2048
        or signature_hash.name != "sha256"
        or leaf_key.public_key().public_numbers() != leaf_public_key.public_numbers()
    ):
        return _not_ready(
            paths,
            TLSIssue.LEAF_INVALID,
            "Deckbox TLS leaf certificate is invalid.",
            ca_certificate=ca_certificate,
            leaf_certificate=leaf_certificate,
        )
    actual_dns = tuple(san.get_values_for_type(x509.DNSName))
    actual_addresses = cast(
        tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
        tuple(san.get_values_for_type(x509.IPAddress)),
    )
    if not set(dns_names).issubset(actual_dns) or not set(addresses).issubset(actual_addresses):
        return _not_ready(
            paths,
            TLSIssue.MISSING_SAN_COVERAGE,
            "Deckbox TLS leaf certificate does not cover the requested SANs.",
            ca_certificate=ca_certificate,
            leaf_certificate=leaf_certificate,
        )
    return _status(paths, ca_certificate, leaf_certificate, actual_dns, actual_addresses)


def require_tls(
    paths: TLSPaths,
    *,
    hostnames: tuple[str, ...],
    ip_addresses: tuple[str, ...],
) -> TLSStatus:
    """Return ready TLS status or raise its concrete remediation."""
    status = inspect_tls(paths, hostnames=hostnames, ip_addresses=ip_addresses)
    if not status.ready:
        raise TLSError(status.detail)
    return status


def _new_leaf(
    ca_key: rsa.RSAPrivateKey,
    ca_certificate: x509.Certificate,
    dns_names: tuple[str, ...],
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    now = datetime.now(UTC)
    leaf_not_after = min(
        now + timedelta(days=397),
        ca_certificate.not_valid_after_utc - _LEAF_CA_EXPIRY_MARGIN,
    )
    if leaf_not_after <= now:
        raise TLSError("Deckbox TLS CA expires too soon to issue a leaf certificate safely.")
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_certificate.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
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
                    *(x509.DNSName(name) for name in dns_names),
                    *(x509.IPAddress(address) for address in addresses),
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return leaf_key, leaf_certificate


def _write_leaf(
    paths: TLSPaths, leaf_key: rsa.RSAPrivateKey, leaf_certificate: x509.Certificate
) -> None:
    _write_atomically(
        paths.leaf_key,
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        mode=0o600,
    )
    _write_atomically(
        paths.leaf_cert,
        leaf_certificate.public_bytes(serialization.Encoding.PEM),
        mode=0o644,
    )


def _write_ca(paths: TLSPaths, ca_key: rsa.RSAPrivateKey, ca_certificate: x509.Certificate) -> None:
    _write_atomically(
        paths.ca_key,
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        mode=0o600,
    )
    _write_atomically(
        paths.ca_cert,
        ca_certificate.public_bytes(serialization.Encoding.PEM),
        mode=0o644,
    )


def setup_local_ca(
    paths: TLSPaths,
    *,
    hostnames: tuple[str, ...],
    ip_addresses: tuple[str, ...],
    renew: bool = False,
) -> TLSStatus:
    """Create a Deckbox local CA and server certificate."""
    dns_names, addresses = _names(hostnames, ip_addresses)
    if paths.ca_cert.exists() != paths.ca_key.exists():
        raise TLSError("Deckbox TLS CA certificate and key are incomplete.")
    paths.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.directory.chmod(0o700)
    if paths.ca_cert.exists() and not paths.leaf_cert.exists() and not paths.leaf_key.exists():
        inspected = inspect_tls(paths, hostnames=hostnames, ip_addresses=ip_addresses)
        if inspected.reason is not TLSIssue.LEAF_MISSING:
            raise TLSError(inspected.detail)
        try:
            ca_key = serialization.load_pem_private_key(paths.ca_key.read_bytes(), password=None)
            ca_certificate = x509.load_pem_x509_certificate(paths.ca_cert.read_bytes())
        except (OSError, ValueError) as error:
            raise TLSError("Deckbox TLS CA material is corrupt.") from error
        if not isinstance(ca_key, rsa.RSAPrivateKey):
            raise TLSError("Deckbox TLS CA key is not an RSA private key.")
        leaf_key, leaf_certificate = _new_leaf(ca_key, ca_certificate, dns_names, addresses)
        _write_leaf(paths, leaf_key, leaf_certificate)
        paths.ca_key.chmod(0o600)
        paths.ca_cert.chmod(0o644)
        return _status(paths, ca_certificate, leaf_certificate, dns_names, addresses)
    existing = inspect_tls(paths, hostnames=hostnames, ip_addresses=ip_addresses)
    if existing.reason is TLSIssue.UNSAFE_PERMISSIONS:
        for path, mode in (
            (paths.ca_key, 0o600),
            (paths.leaf_key, 0o600),
            (paths.ca_cert, 0o644),
            (paths.leaf_cert, 0o644),
        ):
            if path.exists():
                path.chmod(mode)
        existing = inspect_tls(paths, hostnames=hostnames, ip_addresses=ip_addresses)
    if existing.ready:
        paths.ca_key.chmod(0o600)
        paths.leaf_key.chmod(0o600)
        paths.ca_cert.chmod(0o644)
        paths.leaf_cert.chmod(0o644)
        if not renew:
            return existing
        ca_key = serialization.load_pem_private_key(paths.ca_key.read_bytes(), password=None)
        if not isinstance(ca_key, rsa.RSAPrivateKey):
            raise TLSError("Deckbox TLS CA key is not an RSA private key.")
        ca_certificate = x509.load_pem_x509_certificate(paths.ca_cert.read_bytes())
        leaf_key, leaf_certificate = _new_leaf(ca_key, ca_certificate, dns_names, addresses)
        _write_leaf(paths, leaf_key, leaf_certificate)
        return _status(paths, ca_certificate, leaf_certificate, dns_names, addresses)
    if existing.reason is TLSIssue.MISSING_SAN_COVERAGE or (
        renew and existing.reason is TLSIssue.LEAF_OUTLIVES_ISSUER
    ):
        ca_key = serialization.load_pem_private_key(paths.ca_key.read_bytes(), password=None)
        if not isinstance(ca_key, rsa.RSAPrivateKey):
            raise TLSError("Deckbox TLS CA key is not an RSA private key.")
        ca_certificate = x509.load_pem_x509_certificate(paths.ca_cert.read_bytes())
        leaf_key, leaf_certificate = _new_leaf(ca_key, ca_certificate, dns_names, addresses)
        _write_leaf(paths, leaf_key, leaf_certificate)
        return _status(paths, ca_certificate, leaf_certificate, dns_names, addresses)
    if paths.ca_cert.exists():
        raise TLSError(existing.detail)
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Deckbox Local CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
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
    leaf_key, leaf_certificate = _new_leaf(ca_key, ca_certificate, dns_names, addresses)
    _write_ca(paths, ca_key, ca_certificate)
    _write_leaf(paths, leaf_key, leaf_certificate)
    return _status(paths, ca_certificate, leaf_certificate, dns_names, addresses)
