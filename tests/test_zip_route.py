"""HTTP contract tests for Deckbox ZIP packet downloads."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from deckbox.browse import url_path
from deckbox.config import ResolvedConfig
from deckbox.server import create_app


class ZipDownloadRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()
        self.folder = self.root / "folder"
        self.folder.mkdir()
        (self.root / "one.txt").write_text("one", encoding="utf-8")
        (self.folder / "two.txt").write_text("two", encoding="utf-8")
        app = create_app(
            ResolvedConfig(
                directory=self.root,
                host="127.0.0.1",
                port=8000,
                log_level="warning",
            ),
            auth_required=False,
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_selected_listing_items_download_as_a_zip_attachment(self) -> None:
        response = self.client.post(
            "/download-zip",
            data={
                "base": url_path(self.root),
                "path": [url_path(self.root / "one.txt"), url_path(self.folder)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment;", response.headers["content-disposition"])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(archive.namelist(), ["one.txt", "folder/", "folder/two.txt"])
        self.assertEqual(archive.read("folder/two.txt"), b"two")

    def test_rejects_selection_that_is_not_a_direct_child_of_base(self) -> None:
        response = self.client.post(
            "/download-zip",
            data={
                "base": url_path(self.root),
                "path": url_path(self.folder / "two.txt"),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Every selected item must be in the current folder.",
        )

    def test_rejects_foreign_origin(self) -> None:
        response = self.client.post(
            "/download-zip",
            data={
                "base": url_path(self.root),
                "path": url_path(self.root / "one.txt"),
            },
            headers={"Origin": "https://attacker.example"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Cross-origin ZIP download rejected")

    def test_ui_assets_have_a_content_revision_to_invalidate_stale_browser_cache(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.text,
            r'href="/static/css/style\.css\?v=[0-9a-f]{12}"',
        )
        self.assertRegex(
            response.text,
            r'src="/static/js/app\.js\?v=[0-9a-f]{12}"',
        )


if __name__ == "__main__":
    unittest.main()
