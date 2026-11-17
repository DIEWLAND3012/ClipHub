"""Tests for core.naming: safe user-typed file names and never overwriting a file."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.naming import safe_filename, strip_extension, unique_path


class SafeFilenameTests(unittest.TestCase):
    def test_removes_forbidden_characters(self) -> None:
        self.assertEqual(safe_filename('a/b\\c:d*e?f"g<h>i|j'), "a b c d e f g h i j")

    def test_keeps_thai_korean_and_emoji(self) -> None:
        self.assertEqual(safe_filename("คลิปวันหยุด 여름밤 🌙"), "คลิปวันหยุด 여름밤 🌙")

    def test_trims_spaces_and_trailing_dots(self) -> None:
        self.assertEqual(safe_filename("   my   clip ...  "), "my clip")

    def test_empty_and_reserved(self) -> None:
        self.assertEqual(safe_filename("  /// "), "")
        self.assertEqual(safe_filename("CON"), "CON_")

    def test_length_is_capped(self) -> None:
        self.assertEqual(len(safe_filename("ก" * 500)), 180)

    def test_strip_extension(self) -> None:
        self.assertEqual(strip_extension("clip.MP4", "mp4"), "clip")
        self.assertEqual(strip_extension("clip.mp4", "mov"), "clip.mp4")


class UniquePathTests(unittest.TestCase):
    def test_numbers_taken_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(unique_path(folder, "clip", "mp4"), folder / "clip.mp4")
            (folder / "clip.mp4").write_bytes(b"x")
            self.assertEqual(unique_path(folder, "clip", ".MP4"), folder / "clip (2).mp4")
            self.assertEqual(unique_path(folder, "clip", "mp4", taken=[folder / "clip (2).mp4"]),
                             folder / "clip (3).mp4")
            self.assertEqual(unique_path(folder, "clip", "mov"), folder / "clip.mov")


if __name__ == "__main__":
    unittest.main()
