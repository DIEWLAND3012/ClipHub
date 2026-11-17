"""tasks.file_compressor: file kinds, output names, lossless zip, Office repack, images (when Pillow exists)."""
from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from tasks import file_compressor as fc

HAS_PIL = importlib.util.find_spec("PIL") is not None


class KindTests(unittest.TestCase):
    def test_kinds(self) -> None:
        cases = {"a.MP4": "video", "b.png": "image", "c.pdf": "pdf", "d.docx": "office",
                 "e.wav": "audio", "f.txt": "other", "g": "other"}
        for name, kind in cases.items():
            self.assertEqual(fc.kind_of(name), kind, name)

    def test_output_extensions(self) -> None:
        self.assertEqual(fc.output_ext("a.bmp", "balanced"), "png")
        self.assertEqual(fc.output_ext("a.jpeg", "small"), "jpg")
        self.assertEqual(fc.output_ext("a.wav", "high"), "flac")
        self.assertEqual(fc.output_ext("a.wav", "small"), "mp3")
        self.assertEqual(fc.output_ext("a.mp3", "small"), "mp3")
        self.assertEqual(fc.output_ext("a.docx", "small"), "docx")
        self.assertEqual(fc.output_ext("a.exe", "small"), "zip")


class FileTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_suggested_name_is_unique(self) -> None:
        source = self.dir / "notes.txt"
        source.write_text("x", encoding="utf-8")
        (self.dir / "notes_compressed.zip").write_text("", encoding="utf-8")
        self.assertEqual(fc.suggest_output_path(str(source)).name, "notes_compressed_2.zip")

    def test_other_files_are_zipped_losslessly(self) -> None:
        source = self.dir / "log.txt"
        source.write_text("ClipHUB line\n" * 5000, encoding="utf-8")
        output = self.dir / "log_compressed.zip"
        task = fc.FileCompressTask(str(source), str(output))
        task.run()
        with zipfile.ZipFile(output) as bundle:
            self.assertEqual(bundle.read("log.txt"), source.read_bytes())
        self.assertLess(task.result.new_bytes, task.result.original_bytes)

    def test_office_document_is_repacked(self) -> None:
        source = self.dir / "a.docx"
        with zipfile.ZipFile(source, "w", zipfile.ZIP_STORED) as bundle:
            bundle.writestr("[Content_Types].xml", "<x/>")
            bundle.writestr("word/document.xml", "<w>" + "text " * 5000 + "</w>")
        output = self.dir / "a_compressed.docx"
        fc.FileCompressTask(str(source), str(output)).run()
        with zipfile.ZipFile(output) as bundle:
            self.assertEqual(bundle.namelist(), ["[Content_Types].xml", "word/document.xml"])
            self.assertIsNone(bundle.testzip())
        self.assertLess(output.stat().st_size, source.stat().st_size)

    def test_result_is_never_bigger(self) -> None:
        source = self.dir / "a.docx"
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            bundle.writestr("x.bin", bytes(range(256)) * 4)
        output = self.dir / "b.docx"
        task = fc.FileCompressTask(str(source), str(output))
        task.run()
        self.assertLessEqual(task.result.new_bytes, task.result.original_bytes)

    def test_video_is_rejected(self) -> None:
        source = self.dir / "a.mp4"
        source.write_bytes(b"0")
        with self.assertRaises(Exception):
            fc.FileCompressTask(str(source), str(self.dir / "b.mp4")).run()

    @unittest.skipUnless(HAS_PIL, "Pillow not installed")
    def test_jpeg_gets_smaller(self) -> None:
        from PIL import Image
        image = Image.effect_noise((1200, 900), 60).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=98)
        data = buffer.getvalue()
        smaller = fc.compress_image_bytes(data, "jpg", "small")
        self.assertLess(len(smaller), len(data))
        self.assertEqual(Image.open(io.BytesIO(smaller)).format, "JPEG")


if __name__ == "__main__":
    unittest.main()
