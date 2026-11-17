"""tasks.fastcut: time text, output names, FFmpeg arguments, and a real cut when FFmpeg exists."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tasks import fastcut


class FormatTests(unittest.TestCase):
    def test_format_time(self) -> None:
        self.assertEqual(fastcut.format_time(75.44), "1:15.4")
        self.assertEqual(fastcut.format_time(3725), "1:02:05.0")
        self.assertEqual(fastcut.format_time(59.96), "1:00.0")
        self.assertEqual(fastcut.format_time(90, tenths=False), "1:30")

    def test_output_ext(self) -> None:
        self.assertEqual(fastcut.output_ext("a.webm", precise=False), "webm")
        self.assertEqual(fastcut.output_ext("a.webm", precise=True), "mp4")
        self.assertEqual(fastcut.output_ext("a.mov", precise=True), "mov")
        self.assertEqual(fastcut.output_ext("a.mp3", precise=True), "mp3")

    def test_suggested_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "song.mp3"
            (Path(tmp) / "song_cut.mp3").write_bytes(b"")
            self.assertEqual(fastcut.suggest_output_path(str(source)).name, "song_cut_2.mp3")


class ArgsTests(unittest.TestCase):
    def test_fast_cut_copies_streams(self) -> None:
        args = fastcut.build_cut_args("in.mp4", "out.part", 12.5, 20.0, precise=False, muxer="mp4")
        self.assertEqual(args[:6], ["-ss", "12.500", "-i", "in.mp4", "-t", "20.000"])
        self.assertIn("copy", args)
        self.assertEqual(args[-3:], ["-f", "mp4", "out.part"])

    def test_precise_cut_reencodes_video(self) -> None:
        args = fastcut.build_cut_args("in.mp4", "out.part", 1, 2, precise=True)
        self.assertIn("libx264", args)
        self.assertEqual(args[-1], "out.part")

    def test_audio_is_always_copied(self) -> None:
        task = fastcut.FastCutTask("a.mp3", "b.mp3", 0, 5, precise=True)
        self.assertFalse(task.precise)
        self.assertIsNone(task.resource())

    def test_too_short_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.mp3"
            source.write_bytes(b"0")
            with self.assertRaises(Exception):
                fastcut.FastCutTask(str(source), str(Path(tmp) / "b.mp3"), 1.0, 1.2).run()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class RealCutTests(unittest.TestCase):
    def test_cut_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "tone.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=6", str(source)], check=True)
            output = Path(tmp) / "tone_cut.wav"
            fastcut.FastCutTask(str(source), str(output), 1.0, 3.0).run()
            from core.ffmpeg import probe_format
            self.assertAlmostEqual(float(probe_format(str(output))["duration"]), 2.0, delta=0.1)
            self.assertTrue(fastcut.waveform_peaks(str(source)))


if __name__ == "__main__":
    unittest.main()
