# Deckbox

Deckbox is a modern web viewer for a directory of files. It renders Markdown,
PDF, DOCX, JSON, source code, HTML, images, and interactive GraphViz DOT
graphs.

- **Native HTTPS only** — Deckbox owns a dedicated local CA and leaf certificate.
- **PAM authentication** — all browsing and file routes require HTTP Basic
  authentication as the exact OS user that launched Deckbox.
- **Rich rendering** — Markdown, JSON, code, DOCX, PDF, images, HTML, and DOT.
- **Standalone or service** — use `deckbox run` ad hoc or install a
  `systemd --user` service.

## Install

```bash
# From PyPI (once published)
uv tool install deckbox

# Or from the source repository
uv tool install git+https://github.com/bkrabach/deckbox
```

This installs a `deckbox` command on your `PATH`. `python -m deckbox` also
works.

## Quick start

Deckbox must have its local TLS material before it can start:

```bash
cd ./shared-files
deckbox setup-tls
deckbox
```

Then open <https://localhost:8000>. Deckbox does not provide an HTTP listener,
HTTP redirect, or fallback.

Serve a different directory or address:

```bash
deckbox ./notes --port 9000
deckbox run --dir ./notes --host 127.0.0.1 --port 9000
```

The served directory may be a positional `PATH` or supplied with `--dir`. When
both are given, the positional path wins.

## TLS setup and trust

`deckbox setup-tls` is the only command that creates or changes TLS material.
It creates a persistent Deckbox-local CA and a renewable server certificate in
the Deckbox configuration directory. Deckbox never shares certificate material
with Muxplex, Amplifier Unified, or another application.

For a network-facing server, specify every DNS name and IP address clients use:

```bash
deckbox setup-tls \
  --hostname files.example.test \
  --hostname node.example.test \
  --ip 192.0.2.10
```

Inspect without writing:

```bash
deckbox setup-tls --status
deckbox doctor
```

After changing requested names, renew the leaf certificate:

```bash
deckbox setup-tls --renew \
  --hostname files.example.test \
  --ip 192.0.2.10
deckbox service restart
```

Renewal replaces only the leaf certificate and key. It retains the CA, so a
client that already trusts the Deckbox CA does not need a new trust-root
installation.

The unauthenticated bootstrap endpoints are exactly:

- <https://HOST:PORT/health> — Deckbox health JSON.
- <https://HOST:PORT/setup> — CA fingerprint and platform trust instructions.
- <https://HOST:PORT/ca.crt> — the fixed public Deckbox CA certificate.

Every other endpoint, including `/`, `/view/*`, `/raw/*`, `/download/*`,
`/download-zip`, `/api/*`, `/assets/*`, and `/static/*`, requires PAM-backed
HTTP Basic authentication as the exact user that launched Deckbox. There is no
localhost authentication bypass and no `--no-auth` option.

### macOS trust flow

1. Open <https://HOST:PORT/setup> and compare its displayed SHA-256 fingerprint
   out of band with `deckbox setup-tls --status` or `deckbox doctor`.
2. Download the CA from <https://HOST:PORT/ca.crt>.
3. Open the downloaded certificate in **Keychain Access**, add it to the System
   keychain, and set it to **Always Trust**.
4. Fully quit and restart the browser before reopening the HTTPS URL.

The command-line equivalent is:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain ./deckbox-ca.crt
```

Trust only the downloaded, fingerprint-verified Deckbox CA. The CA download
contains no private key.

Deckbox deliberately does not enable HSTS. It also does not provide an HTTP
fallback or HTTP-to-HTTPS redirect.

## Commands

| Command | Purpose |
| --- | --- |
| `deckbox` / `deckbox run` | Start the HTTPS web server (default action) |
| `deckbox setup-tls` | Create Deckbox TLS material |
| `deckbox setup-tls --renew` | Replace only the leaf certificate |
| `deckbox setup-tls --status` | Inspect TLS material without writing |
| `deckbox open` | Open the HTTPS URL in a browser |
| `deckbox doctor` | Check dependencies, TLS, and trusted live HTTPS health |
| `deckbox status` | Show configuration, service, TLS, and listener state |
| `deckbox service install` | Install and start a `systemd --user` service |
| `deckbox service {uninstall,start,stop,restart,status,logs}` | Manage the service |
| `deckbox config {show,path,set,unset}` | Inspect or edit configuration |
| `deckbox update` | Update Deckbox using `uv` |

### Run flags

```text
--dir PATH                 Directory to serve
--host HOST                Bind address (default 0.0.0.0)
--port PORT                Bind port (default 8000)
--log-level LEVEL          Uvicorn log level (default info)
--allow-outside-root       Allow Go to path outside the served directory
```

## Configuration

Settings resolve by precedence: CLI flag, environment variable, configuration
file, then default. The standard variables are `DECKBOX_DIR`, `DECKBOX_HOST`,
`DECKBOX_PORT`, `DECKBOX_LOG_LEVEL`, and `DECKBOX_ALLOW_OUTSIDE_ROOT`.

```yaml
dir: ./shared-files
host: 0.0.0.0
port: 8000
log_level: info
tls_hostnames:
  - files.example.test
tls_ips:
  - 192.0.2.10
```

Use `deckbox config show` to inspect the resolved values. TLS names should be
changed through `deckbox setup-tls --renew`, which preserves certificate and
configuration consistency.

## Running as a service

First complete explicit TLS setup. `deckbox service install` refuses to install
or start a service until valid Deckbox TLS material exists:

```bash
deckbox setup-tls --hostname files.example.test --ip 192.0.2.10
deckbox service install --dir ./shared-files --port 8000
deckbox service status
```

Service installation persists the selected directory, host, port, and TLS name
configuration, then enables the per-user service. Installing or restarting a
live service is a deliberate operational cutover after `setup-tls --status`,
`doctor`, and an HTTPS health check have been verified.

## Rendering

| Type | How it is shown |
| --- | --- |
| Markdown (`.md`) | Rich HTML with tables, task lists, admonitions, TOC, and code highlighting |
| GraphViz (`.dot`, `.gv`) | Themed SVG with pan, zoom, fit, source, and SVG download |
| JSON (`.json`) | Pretty-printed and syntax-highlighted |
| Code | Syntax-highlighted with Pygments |
| DOCX (`.docx`) | Converted to clean semantic HTML |
| PDF (`.pdf`) | Native browser viewer in a sandboxed frame |
| HTML (`.html`) | Sandboxed frame with a raw-file option |
| Images | Displayed inline |
| Other files | Offered as downloads |

DOT rendering requires the `dot` binary from
[GraphViz](https://graphviz.org). Without it, Deckbox shows source text.

## Development

```bash
git clone https://github.com/bkrabach/deckbox
cd deckbox
uv sync --all-groups
deckbox setup-tls
deckbox run --dir .
```

## License

MIT