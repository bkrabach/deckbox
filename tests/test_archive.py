"""Contract tests for Deckbox's server-side ZIP packet builder."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from deckbox.archive import (
    DEFAULT_LIMITS,
    ArchiveError,
    ArchiveLimits,
    iter_zip,
    plan_archive,
)


class ArchivePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build_zip(
        self, *selection: Path, limits: ArchiveLimits = DEFAULT_LIMITS
    ) -> zipfile.ZipFile:
        plan = plan_archive(list(selection), scope=self.root, limits=limits)
        self.archive = zipfile.ZipFile(io.BytesIO(b"".join(iter_zip(plan))))
        return self.archive

    def test_packet_preserves_file_folder_dotfile_and_empty_folder(self) -> None:
        (self.root / "plain.txt").write_text("plain", encoding="utf-8")
        folder = self.root / "packet"
        (folder / ".amplifier").mkdir(parents=True)
        (folder / ".amplifier" / "settings.yaml").write_text("enabled: true\n", encoding="utf-8")
        (folder / "nested").mkdir()
        (folder / "nested" / "note.md").write_text("# Note\n", encoding="utf-8")
        (folder / "empty").mkdir()

        archive = self.build_zip(self.root / "plain.txt", folder)

        self.assertEqual(
            archive.namelist(),
            [
                "plain.txt",
                "packet/",
                "packet/.amplifier/",
                "packet/.amplifier/settings.yaml",
                "packet/empty/",
                "packet/nested/",
                "packet/nested/note.md",
            ],
        )
        self.assertEqual(archive.read("plain.txt"), b"plain")
        self.assertEqual(archive.read("packet/.amplifier/settings.yaml"), b"enabled: true\n")

    def test_duplicate_selection_is_written_once(self) -> None:
        file = self.root / "one.txt"
        file.write_text("one", encoding="utf-8")

        archive = self.build_zip(file, file)

        self.assertEqual(archive.namelist(), ["one.txt"])

    def test_rejects_selection_outside_permitted_scope(self) -> None:
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("not permitted", encoding="utf-8")

        with self.assertRaisesRegex(ArchiveError, "outside"):
            plan_archive([outside], scope=self.root)

    def test_rejects_when_member_limit_is_exceeded_before_streaming(self) -> None:
        (self.root / "one.txt").write_text("one", encoding="utf-8")
        (self.root / "two.txt").write_text("two", encoding="utf-8")

        with self.assertRaisesRegex(ArchiveError, "member limit"):
            plan_archive(
                [self.root / "one.txt", self.root / "two.txt"],
                scope=self.root,
                limits=ArchiveLimits(max_members=1, max_source_bytes=1024),
            )

    def test_rejects_when_source_byte_limit_is_exceeded_before_streaming(self) -> None:
        file = self.root / "large.txt"
        file.write_bytes(b"12345")

        with self.assertRaisesRegex(ArchiveError, "size limit"):
            plan_archive(
                [file],
                scope=self.root,
                limits=ArchiveLimits(max_members=10, max_source_bytes=4),
            )


if __name__ == "__main__":
    unittest.main()
