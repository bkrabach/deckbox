"""DOCX rendering via mammoth (semantic HTML, images inlined as data URIs)."""

from __future__ import annotations

from pathlib import Path

import mammoth


def render(path: Path) -> str:
    with path.open("rb") as fh:
        result = mammoth.convert_to_html(fh)
    return f'<article class="markdown-body docx-body">{result.value}</article>'
