# Deckbox

A pleasant, modern web viewer for a folder of files. Point it at a directory,
open a browser, and browse — with first-class rendering for Markdown, PDF, DOCX,
JSON, source code, HTML, and an elegant interactive renderer for GraphViz DOT
graphs.

- **Zero-config** — run it in any folder; it serves the current directory.
- **Modern UI** — clean light/dark themes, breadcrumb navigation, live filter.
- **Rich renderers** — Markdown, JSON, code (syntax-highlighted), DOCX, PDF,
  images, and raw HTML — plus a pan/zoom **DOT graph** viewer.
- **Safe by default** — binds `0.0.0.0`, but any non-localhost request must
  authenticate via **PAM as the user who launched the server**. Localhost is
  never challenged.
- **Runs standalone or as a service** — `deckbox run` for ad-hoc use, or install
  a `systemd --user` service that survives logout and reboot.

## Install

```bash
# From PyPI (once published)
uv tool install deckbox

# Or straight from GitHub
uv tool install git+https://github.com/bkrabach/deckbox
```

This puts a `deckbox` command on your PATH. `python -m deckbox` also works.

## Quick start

```bash
cd ~/some/folder
deckbox                 # serves this folder at http://0.0.0.0:8000

deckbox ~/notes         # or point it at a folder directly (positional path)
```

Then open <http://localhost:8000>.

Serve a specific directory or change the address:

```bash
deckbox ~/notes --port 9000         # positional path
deckbox run --dir ~/notes --port 9000   # --dir also works
deckbox run --host 127.0.0.1        # localhost-only, no auth
```

The served directory can be given as a positional `PATH` (`deckbox ~/notes`,
`deckbox doctor ~/notes`) or with `--dir`. If both are given, the positional
path wins.

## Commands

| Command | Purpose |
|---------|---------|
| `deckbox` / `deckbox run` | Start the web server (default action) |
| `deckbox open` | Open the served URL in a web browser |
| `deckbox doctor` | Diagnostics: deps, served dir, graphviz, PAM, port |
| `deckbox status` | Show resolved config, service state, and port status |
| `deckbox update` | Update to the latest version (via `uv`) |
| `deckbox service install` | Install & start a `systemd --user` service |
| `deckbox service {uninstall,start,stop,restart,status,logs}` | Manage the service |
| `deckbox config {show,path,set,unset}` | Inspect or edit configuration |

### Run flags

```
--dir PATH        Directory to serve
--host HOST       Bind address (default 0.0.0.0)
--port PORT       Bind port (default 8000)
--log-level LVL   uvicorn log level (default info)
--no-auth         Disable PAM auth even for remote clients (trusted networks only)
```

## Configuration

Every setting resolves by precedence (first wins):

1. **CLI flag** (e.g. `--dir`)
2. **Environment variable** (`DECKBOX_DIR`, `DECKBOX_HOST`, `DECKBOX_PORT`, `DECKBOX_LOG_LEVEL`)
3. **Config file** — `~/.config/deckbox/config.yaml`
4. **Default** — for the served directory, the fallback is the current working directory

```bash
deckbox config set dir ~/notes
deckbox config set port 9000
deckbox config show
deckbox config path
```

Example `~/.config/deckbox/config.yaml`:

```yaml
dir: /home/me/notes
host: 0.0.0.0
port: 8000
log_level: info
```

## Authentication

- Requests from **localhost** (`127.0.0.1` / `::1`) are **never** challenged.
- Requests from **any other host** must pass **HTTP Basic auth**, where the
  username must be the OS user that launched the server and the password is
  verified through **PAM** (the `login` service). The client IP is read from
  the socket, so it cannot be spoofed with headers.

This means: run it, and it "just works" locally; reach it over the network and
your browser asks you to log in as yourself.

> PAM authentication requires the `python-pam` dependency (installed
> automatically) and a working PAM stack. Run `deckbox doctor` to verify.

## Running as a service

Install a per-user systemd service that serves a chosen directory and restarts
on failure:

```bash
deckbox service install --dir ~/notes --port 8000
deckbox service status
deckbox service logs
deckbox service stop
deckbox service uninstall
```

`service install` persists your chosen directory/host/port to the config file
and enables lingering (best effort) so the service runs without an active login.

## Rendering

| Type | How it's shown |
|------|----------------|
| Markdown (`.md`) | Rich HTML (tables, task lists, admonitions, TOC, code highlighting) |
| GraphViz (`.dot`, `.gv`) | Themed SVG in an interactive pan/zoom/fit viewer with a source toggle |
| JSON (`.json`) | Pretty-printed and syntax-highlighted |
| Code (many) | Syntax-highlighted via Pygments |
| DOCX (`.docx`) | Converted to clean semantic HTML |
| PDF (`.pdf`) | Native browser viewer (sandboxed iframe) |
| HTML (`.html`) | Rendered in a sandboxed iframe, with an "open raw" escape hatch |
| Images | Displayed inline |
| Anything else | Offered as a download |

### GraphViz DOT

DOT files are rendered with a tasteful default theme (rounded, filled nodes; a
modern font stack applied to the SVG) — your explicit attributes always win.
The result is an interactive viewer: scroll to zoom, drag to pan, **Fit**,
**100%**, **Source**, and **Download SVG**. Requires the `dot` binary
([GraphViz](https://graphviz.org)); without it, the source is shown instead.

```bash
# Debian/Ubuntu
sudo apt install graphviz
# macOS
brew install graphviz
```

## Development

```bash
git clone https://github.com/bkrabach/deckbox
cd deckbox
uv venv && source .venv/bin/activate
uv pip install -e .
deckbox run --dir .
```

## License

MIT
