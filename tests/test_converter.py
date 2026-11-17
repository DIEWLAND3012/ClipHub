"""Tests for tasks.converter: command building, validation, naming and the two-stage process."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.base_task import TaskStatus
from core.errors import InputError, TaskCancelledError, ToolFailedError
from tasks import converter
from tasks.converter import (
    AUDIO_CODECS_FOR, VIDEO_CODECS_FOR, ConverterTask, ConvertSettings, audio_sidecar_path,
    build_convert_args, build_extract_args, check_settings, copy_problem, describe_conversion,
    describe_settings, suggest_output_path,
)


def pairs(args: list[str]) -> dict[str, str]:
    """Option -> value for the simple '-flag value' pairs in an ffmpeg argument list."""
    return {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("-")}


class BuildConvertArgsTests(unittest.TestCase):
    def test_video_targets(self) -> None:
        muxers = {"mp4": "mp4", "mkv": "matroska", "avi": "avi", "mov": "mov"}
        for ext, muxer in muxers.items():
            with self.subTest(ext=ext):
                args = build_convert_args("in.webm", "out.part", ext)
                opts = pairs(args)
                self.assertEqual(args[:2], ["-i", "in.webm"])
                self.assertEqual(args[-3:], ["-f", muxer, "out.part"])
                self.assertEqual(opts["-c:v"], "libx264")
                self.assertEqual(opts["-preset"], "medium")
                self.assertEqual(opts["-crf"], "20")
                self.assertEqual(opts["-pix_fmt"], "yuv420p")
                self.assertEqual(opts["-c:a"], "aac")
                self.assertEqual(opts["-b:a"], "192k")
                self.assertNotIn("-vn", args)
                if ext == "mp4":
                    self.assertEqual(opts["-movflags"], "+faststart")
                else:
                    self.assertNotIn("-movflags", args)

    def test_video_keeps_video_and_audio_only(self) -> None:
        args = build_convert_args("in.mkv", "out.part", "mp4")
        self.assertIn("0:v:0?", args)
        self.assertIn("0:a?", args)
        self.assertIn("scale=trunc(iw/2)*2:trunc(ih/2)*2", args)

    def test_audio_targets(self) -> None:
        expected = {
            "mp3": ({"-c:a": "libmp3lame", "-q:a": "2"}, "mp3"),
            "wav": ({"-c:a": "pcm_s16le"}, "wav"),
            "aac": ({"-c:a": "aac", "-b:a": "192k"}, "adts"),
            "flac": ({"-c:a": "flac"}, "flac"),
        }
        for ext, (codec, muxer) in expected.items():
            with self.subTest(ext=ext):
                args = build_convert_args("in.mp4", "out.part", ext)
                self.assertIn("-vn", args)
                self.assertEqual(args[:2], ["-i", "in.mp4"])
                self.assertEqual(args[-3:], ["-f", muxer, "out.part"])
                opts = pairs(args)
                for key, value in codec.items():
                    self.assertEqual(opts[key], value)
                self.assertNotIn("-c:v", args)

    def test_extension_is_normalised(self) -> None:
        self.assertEqual(
            build_convert_args("a", "b", ".MP4"), build_convert_args("a", "b", "mp4")
        )

    def test_unsupported_target(self) -> None:
        with self.assertRaises(InputError) as ctx:
            build_convert_args("a", "b", "xyz")
        self.assertEqual(str(ctx.exception), "รูปแบบ .xyz ไม่รองรับ")

    def test_webm_target_uses_vp9_and_opus(self) -> None:
        args = build_convert_args("in.mp4", "out.part", "webm")
        opts = pairs(args)
        self.assertEqual(opts["-c:v"], "libvpx-vp9")
        self.assertEqual(opts["-c:a"], "libopus")
        self.assertEqual(args[-3:], ["-f", "webm", "out.part"])

    def test_new_audio_targets(self) -> None:
        m4a = build_convert_args("in.mp4", "out.part", "m4a")
        self.assertEqual(pairs(m4a)["-c:a"], "aac")
        self.assertEqual(m4a[-3:-1], ["-f", "ipod"])
        opus = build_convert_args("in.mp4", "out.part", "opus")
        self.assertEqual(pairs(opus)["-c:a"], "libopus")
        self.assertEqual(opus[-3:-1], ["-f", "ogg"])


class SettingsTests(unittest.TestCase):
    def test_codec_encoders_and_quality(self) -> None:
        cases = {
            ("mp4", "h265"): ("libx265", "-crf", "23"),
            ("mkv", "av1"): ("libsvtav1", "-crf", "32"),
            ("mkv", "vp9"): ("libvpx-vp9", "-crf", "31"),
            ("mov", "prores"): ("prores_ks", "-profile:v", "2"),
            ("mp4", "h264"): ("libx264", "-crf", "20"),
        }
        for (ext, codec), (encoder, key, value) in cases.items():
            with self.subTest(ext=ext, codec=codec):
                opts = pairs(build_convert_args("in.mkv", "o.part", ext, ConvertSettings(video_codec=codec)))
                self.assertEqual(opts["-c:v"], encoder)
                self.assertEqual(opts[key], value)

    def test_quality_levels_move_crf(self) -> None:
        crf = {q: pairs(build_convert_args("i", "o", "mp4", ConvertSettings(video_codec="h264", quality=q)))["-crf"]
               for q in ("high", "balanced", "small")}
        self.assertLess(int(crf["high"]), int(crf["balanced"]))
        self.assertLess(int(crf["balanced"]), int(crf["small"]))

    def test_hevc_gets_apple_tag_in_mp4_and_mov(self) -> None:
        for ext in ("mp4", "mov"):
            with self.subTest(ext=ext):
                self.assertEqual(pairs(build_convert_args("i", "o", ext, ConvertSettings(video_codec="h265")))["-tag:v"], "hvc1")
        self.assertNotIn("-tag:v", build_convert_args("i", "o", "mkv", ConvertSettings(video_codec="h265")))

    def test_resolution_only_downscales_and_fps(self) -> None:
        opts = pairs(build_convert_args("i", "o", "mp4", ConvertSettings(height=720, fps=30)))
        self.assertIn("min(720,ih)", opts["-vf"])
        self.assertEqual(opts["-r"], "30")

    def test_copy_skips_filters(self) -> None:
        args = build_convert_args("i", "o", "mkv", ConvertSettings(video_codec="copy", audio_codec="copy"))
        opts = pairs(args)
        self.assertEqual((opts["-c:v"], opts["-c:a"]), ("copy", "copy"))
        self.assertNotIn("-vf", args)
        self.assertNotIn("-crf", args)

    def test_copy_hevc_into_mp4_is_tagged(self) -> None:
        args = build_convert_args("i", "o", "mp4", ConvertSettings(video_codec="copy"), source_video_codec="hevc")
        self.assertEqual(pairs(args)["-tag:v"], "hvc1")

    def test_audio_codec_and_bitrate_in_video_target(self) -> None:
        opts = pairs(build_convert_args("i", "o", "mkv", ConvertSettings(audio_codec="opus", audio_bitrate=128)))
        self.assertEqual((opts["-c:a"], opts["-b:a"]), ("libopus", "128k"))

    def test_audio_target_bitrate_and_lossless(self) -> None:
        mp3 = pairs(build_convert_args("i", "o", "mp3", ConvertSettings(audio_bitrate=320)))
        self.assertEqual((mp3["-c:a"], mp3["-b:a"]), ("libmp3lame", "320k"))
        flac = build_convert_args("i", "o", "flac", ConvertSettings(audio_bitrate=320))
        self.assertNotIn("-b:a", flac)  # lossless ignores bitrate
        copy = pairs(build_convert_args("i", "o", "m4a", ConvertSettings(audio_codec="copy")))
        self.assertEqual(copy["-c:a"], "copy")

    def test_container_codec_rules(self) -> None:
        check_settings("mov", ConvertSettings(video_codec="prores"))
        with self.assertRaises(InputError):
            check_settings("webm", ConvertSettings(video_codec="h264"))
        with self.assertRaises(InputError):
            check_settings("mp4", ConvertSettings(audio_codec="opus"))
        with self.assertRaises(InputError):
            check_settings("mp4", ConvertSettings(quality="ultra"))
        for ext in VIDEO_CODECS_FOR:
            with self.subTest(ext=ext):
                check_settings(ext, ConvertSettings())  # every container has working defaults
                self.assertIn(ext, AUDIO_CODECS_FOR)

    def test_copy_problem(self) -> None:
        vp9 = {"video": {"codec": "vp9"}, "audio": {"codec": "opus"}}
        self.assertIn("VP9", copy_problem("mp4", ConvertSettings(video_codec="copy"), vp9))
        self.assertEqual(copy_problem("mkv", ConvertSettings(video_codec="copy", audio_codec="copy"), vp9), "")
        self.assertIn("OPUS", copy_problem("m4a", ConvertSettings(audio_codec="copy"), vp9))
        h264 = {"video": {"codec": "h264"}, "audio": {"codec": "aac"}}
        self.assertEqual(copy_problem("mp4", ConvertSettings(video_codec="copy", audio_codec="copy"), h264), "")

    def test_describe_settings(self) -> None:
        self.assertEqual(describe_settings("mp4", ConvertSettings(video_codec="h265", height=1080)), "H.265 · 1080p")
        self.assertEqual(describe_settings("mkv", ConvertSettings(video_codec="copy")), "คงเดิม (ไม่เข้ารหัสใหม่)")
        self.assertEqual(describe_settings("mp3", ConvertSettings(audio_bitrate=256)), "256 kbps")
        self.assertEqual(describe_settings("flac", ConvertSettings()), "Lossless")


class BuildExtractArgsTests(unittest.TestCase):
    def test_mp3_and_wav(self) -> None:
        mp3 = build_extract_args("in.mkv", "side.part", "mp3")
        self.assertEqual(
            mp3, ["-i", "in.mkv", "-map", "0:a:0", "-vn",
                  "-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3", "side.part"],
        )
        wav = build_extract_args("in.mkv", "side.part", "wav")
        self.assertEqual(
            wav, ["-i", "in.mkv", "-map", "0:a:0", "-vn",
                  "-c:a", "pcm_s16le", "-f", "wav", "side.part"],
        )

    def test_only_mp3_and_wav(self) -> None:
        for ext in ("flac", "aac", "mp4", ""):
            with self.subTest(ext=ext), self.assertRaises(InputError):
                build_extract_args("in.mkv", "side.part", ext)


class HelperTests(unittest.TestCase):
    def test_audio_sidecar_path(self) -> None:
        self.assertEqual(
            audio_sidecar_path("/v/clip_converted.mp4", "mp3"), Path("/v/clip_converted_audio.mp3")
        )
        self.assertEqual(audio_sidecar_path("/v/a.mkv", ".WAV"), Path("/v/a_audio.wav"))

    def test_describe_conversion(self) -> None:
        self.assertEqual(describe_conversion(".mkv", "mp4"), "MKV → MP4")
        self.assertEqual(describe_conversion("mkv", ".mp4", "mp3"), "MKV → MP4 · + เสียง MP3")
        self.assertEqual(describe_conversion("", "mp4"), "? → MP4")

    def test_summary(self) -> None:
        self.assertEqual(ConverterTask("/v/a.mkv", "/v/a.mp4").summary(), "MKV → MP4")
        self.assertEqual(
            ConverterTask("/v/a.mkv", "/v/a.mp4", extract_audio=True).summary(),
            "MKV → MP4 · + เสียง MP3",
        )
        self.assertEqual(
            ConverterTask("/v/a.mkv", "/v/a.mp4", True, audio_format="wav").summary(),
            "MKV → MP4 · + เสียง WAV",
        )

    def test_kind_and_title_contract(self) -> None:
        self.assertEqual(ConverterTask.kind, "convert")
        self.assertEqual(ConverterTask.title, "แปลงไฟล์")


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.source = self.dir / "clip.mkv"
        self.source.write_bytes(b"original bytes")


class SuggestOutputPathTests(TempDirCase):
    def test_first_free_name(self) -> None:
        self.assertEqual(
            suggest_output_path(str(self.source), "mp4"), self.dir / "clip_converted.mp4"
        )

    def test_adds_counter_when_taken(self) -> None:
        (self.dir / "clip_converted.mp4").write_bytes(b"x")
        self.assertEqual(
            suggest_output_path(str(self.source), "mp4"), self.dir / "clip_converted (2).mp4"
        )
        (self.dir / "clip_converted (2).mp4").write_bytes(b"x")
        self.assertEqual(
            suggest_output_path(str(self.source), ".MP4"), self.dir / "clip_converted (3).mp4"
        )

    def test_other_extension_does_not_collide(self) -> None:
        (self.dir / "clip_converted.mp4").write_bytes(b"x")
        self.assertEqual(
            suggest_output_path(str(self.source), "mp3"), self.dir / "clip_converted.mp3"
        )

    def test_sidecar_name_must_be_free_too(self) -> None:
        (self.dir / "clip_converted_audio.mp3").write_bytes(b"x")
        self.assertEqual(
            suggest_output_path(str(self.source), "mp4", sidecar_ext="mp3"),
            self.dir / "clip_converted (2).mp4",
        )
        self.assertEqual(  # without the separator the first name is fine
            suggest_output_path(str(self.source), "mp4"), self.dir / "clip_converted.mp4"
        )

    def test_taken_paths_are_respected(self) -> None:
        taken = [self.dir / "clip_converted.mp4"]
        self.assertEqual(
            suggest_output_path(str(self.source), "mp4", taken=taken),
            self.dir / "clip_converted (2).mp4",
        )


class ValidateTests(TempDirCase):
    def test_missing_source(self) -> None:
        task = ConverterTask(str(self.dir / "nope.mkv"), str(self.dir / "o.mp4"))
        with self.assertRaisesRegex(InputError, "ไม่พบไฟล์ต้นทาง"):
            task.validate()

    def test_unsupported_target(self) -> None:
        task = ConverterTask(str(self.source), str(self.dir / "out.xyz"))
        with self.assertRaises(InputError) as ctx:
            task.validate()
        self.assertEqual(str(ctx.exception), "รูปแบบ .xyz ไม่รองรับ")

    def test_missing_target_extension(self) -> None:
        task = ConverterTask(str(self.source), str(self.dir / "out"))
        with self.assertRaisesRegex(InputError, "ไม่รองรับ"):
            task.validate()

    def test_webm_is_a_target(self) -> None:
        ConverterTask(str(self.source), str(self.dir / "out.webm")).validate()

    def test_rejects_codec_the_container_cannot_hold(self) -> None:
        task = ConverterTask(str(self.source), str(self.dir / "out.webm"), settings=ConvertSettings(video_codec="h264"))
        with self.assertRaisesRegex(InputError, "H.264"):
            task.validate()

    def test_supported_targets_pass(self) -> None:
        for ext in ("mp4", "mkv", "avi", "mov", "mp3", "wav", "aac", "flac", "MP4"):
            with self.subTest(ext=ext):
                ConverterTask(str(self.source), str(self.dir / f"out.{ext}")).validate()

    def test_rejects_bad_separator_format(self) -> None:
        task = ConverterTask(str(self.source), str(self.dir / "o.mp4"), True, audio_format="flac")
        with self.assertRaisesRegex(InputError, "MP3 / WAV"):
            task.validate()
        # the format is only checked when the separator is on
        ConverterTask(str(self.source), str(self.dir / "o.mp4"), False, audio_format="flac").validate()

    def test_never_overwrites_the_source(self) -> None:
        with self.assertRaisesRegex(InputError, "ซ้ำกับไฟล์ต้นทาง"):
            ConverterTask(str(self.source), str(self.source)).validate()
        twin = self.dir / "clip_audio.mp3"
        twin.write_bytes(b"x")
        clash = ConverterTask(str(twin), str(self.dir / "clip.mp4"), extract_audio=True)
        with self.assertRaisesRegex(InputError, "ซ้ำกับไฟล์ต้นทาง"):
            clash.validate()


class FakeFfmpeg:
    """Stands in for run_ffmpeg: records args, reports progress and writes the .part file."""

    def __init__(self, fail_on_call: int | None = None, on_call=None, after_call=None,
                 error: str = "") -> None:
        self.calls: list[list[str]] = []
        self.fail_on_call = fail_on_call
        self.on_call = on_call        # runs before ffmpeg "starts"
        self.after_call = after_call  # runs once the stage has finished
        self.error = error

    def __call__(self, args, *, duration, on_progress=None, is_cancelled=lambda: False) -> None:
        self.calls.append(list(args))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        if is_cancelled():
            raise TaskCancelledError("ยกเลิกงานแล้ว")
        Path(args[-1]).write_bytes(b"out")
        for fraction in (0.0, 0.5, 1.0):
            if on_progress is not None:
                on_progress(fraction)
        if self.fail_on_call == len(self.calls):
            raise ToolFailedError("FFmpeg ทำงานไม่สำเร็จ: x", stderr=self.error)
        if self.after_call is not None:
            self.after_call(len(self.calls))


class ProcessTests(TempDirCase):
    def run_task(self, task: ConverterTask, fake: FakeFfmpeg) -> list[tuple[int, str]]:
        seen: list[tuple[int, str]] = []
        task.set_progress_callback(lambda percent, message: seen.append((percent, message)))
        with mock.patch.object(converter, "probe_duration", return_value=10.0), \
                mock.patch.object(converter, "run_ffmpeg", fake):
            task.run()
        return seen

    def parts(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir() if p.name.endswith(".part"))

    def test_single_stage(self) -> None:
        output = self.dir / "clip_converted.mp4"
        task = ConverterTask(str(self.source), str(output))
        fake = FakeFfmpeg()
        seen = self.run_task(task, fake)

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertTrue(output.is_file())
        self.assertEqual(self.parts(), [])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][-3:], ["-f", "mp4", str(output) + ".part"])
        percents = [p for p, _ in seen]
        self.assertEqual(percents, sorted(percents))
        self.assertIn(50, percents)  # fraction 0.5 maps onto 0-100
        self.assertEqual(percents[-1], 100)
        self.assertEqual(seen[0][1], "กำลังแปลงไฟล์")

    def test_audio_target_single_stage(self) -> None:
        output = self.dir / "clip_converted.mp3"
        task = ConverterTask(str(self.source), str(output))
        fake = FakeFfmpeg()
        self.run_task(task, fake)
        self.assertTrue(output.is_file())
        self.assertIn("-vn", fake.calls[0])

    def test_separator_runs_two_stages_and_never_touches_source(self) -> None:
        output = self.dir / "clip_converted.mp4"
        task = ConverterTask(str(self.source), str(output), extract_audio=True)
        fake = FakeFfmpeg()
        seen = self.run_task(task, fake)

        sidecar = self.dir / "clip_converted_audio.mp3"
        self.assertEqual(task.audio_output_path, sidecar)
        self.assertTrue(output.is_file())
        self.assertTrue(sidecar.is_file())
        self.assertEqual(self.parts(), [])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[1][-3:], ["-f", "mp3", str(sidecar) + ".part"])
        self.assertIn("0:a:0", fake.calls[1])
        for call in fake.calls:  # the source is only ever an input
            self.assertEqual(call.count(str(self.source)), 1)
            self.assertEqual(call[call.index(str(self.source)) - 1], "-i")
        self.assertEqual(self.source.read_bytes(), b"original bytes")

        convert_stage = [p for p, m in seen if m == "กำลังแปลงไฟล์"]
        separate_stage = [p for p, m in seen if m == "กำลังแยกเสียง"]
        self.assertTrue(all(0 <= p <= 50 for p in convert_stage))
        self.assertTrue(all(50 <= p <= 100 for p in separate_stage))
        self.assertIn(25, convert_stage)
        self.assertIn(75, separate_stage)

    def test_separator_wav(self) -> None:
        output = self.dir / "clip_converted.mkv"
        task = ConverterTask(str(self.source), str(output), True, audio_format="wav")
        fake = FakeFfmpeg()
        self.run_task(task, fake)
        self.assertTrue((self.dir / "clip_converted_audio.wav").is_file())
        self.assertEqual(fake.calls[1][-3:-1], ["-f", "wav"])

    def test_failure_in_second_stage_publishes_nothing(self) -> None:
        output = self.dir / "clip_converted.mp4"
        task = ConverterTask(str(self.source), str(output), extract_audio=True)
        with self.assertRaises(ToolFailedError):
            self.run_task(task, FakeFfmpeg(fail_on_call=2))
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertFalse(output.exists())
        self.assertFalse((self.dir / "clip_converted_audio.mp3").exists())
        self.assertEqual(self.parts(), [])

    def test_video_without_audio_gives_thai_message(self) -> None:
        output = self.dir / "clip_converted.mp4"
        task = ConverterTask(str(self.source), str(output), extract_audio=True)
        fake = FakeFfmpeg(fail_on_call=2, error="Stream map '0:a:0' matches no streams.")
        with self.assertRaisesRegex(ToolFailedError, "ไม่มีสตรีมเสียง"):
            self.run_task(task, fake)

    def test_other_ffmpeg_errors_pass_through(self) -> None:
        task = ConverterTask(str(self.source), str(self.dir / "o.mp4"))
        with self.assertRaisesRegex(ToolFailedError, "FFmpeg ทำงานไม่สำเร็จ"):
            self.run_task(task, FakeFfmpeg(fail_on_call=1, error="Invalid data"))

    def test_cancel_during_first_stage(self) -> None:
        output = self.dir / "clip_converted.mp4"
        task = ConverterTask(str(self.source), str(output), extract_audio=True)
        fake = FakeFfmpeg(on_call=lambda n: task.cancel_task())
        with self.assertRaises(TaskCancelledError):  # run_ffmpeg sees the flag and stops
            self.run_task(task, fake)
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertFalse(output.exists())
        self.assertEqual(self.parts(), [])

    def test_cancel_between_stages(self) -> None:
        output = self.dir / "clip_converted.mp4"
        task = ConverterTask(str(self.source), str(output), extract_audio=True)
        fake = FakeFfmpeg(after_call=lambda n: task.cancel_task() if n == 1 else None)
        with self.assertRaises(TaskCancelledError):
            self.run_task(task, fake)
        self.assertEqual(len(fake.calls), 1, "stage 2 must not start after a cancel")
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertFalse(output.exists(), "nothing is published after a cancel")
        self.assertEqual(self.parts(), [])

    def test_creates_missing_output_folder(self) -> None:
        output = self.dir / "new" / "deeper" / "clip_converted.mp4"
        task = ConverterTask(str(self.source), str(output))
        self.run_task(task, FakeFfmpeg())
        self.assertTrue(output.is_file())

    def test_copy_checks_the_source_codec_first(self) -> None:
        task = ConverterTask(str(self.source), str(self.dir / "o.mp4"), settings=ConvertSettings(video_codec="copy"))
        with mock.patch.object(converter, "probe_media", return_value={"video": {"codec": "vp9"}, "audio": None}), \
                self.assertRaisesRegex(InputError, "คงเดิม"):
            self.run_task(task, FakeFfmpeg())
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_summary_mentions_settings(self) -> None:
        task = ConverterTask("/v/a.mkv", "/v/a.mp4", settings=ConvertSettings(video_codec="h265", height=720))
        self.assertEqual(task.summary(), "MKV → MP4 · H.265 · 720p")

    def test_run_passes_cancel_flag_to_ffmpeg(self) -> None:
        task = ConverterTask(str(self.source), str(self.dir / "o.mp4"))
        captured = {}

        def fake(args, *, duration, on_progress=None, is_cancelled=lambda: False):
            captured["flag"] = is_cancelled
            captured["duration"] = duration
            Path(args[-1]).write_bytes(b"x")

        with mock.patch.object(converter, "probe_duration", return_value=42.0), \
                mock.patch.object(converter, "run_ffmpeg", fake):
            task.run()
        self.assertEqual(captured["duration"], 42.0)
        self.assertFalse(captured["flag"]())
        task.cancel_task()
        self.assertTrue(captured["flag"]())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class RealConversionTests(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.video = self.dir / "src.mkv"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=161x121:rate=10:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", str(self.video)],
            check=True,
        )

    def streams(self, path: Path) -> list[str]:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
             str(path)], capture_output=True, text=True, check=True,
        ).stdout
        return out.split()

    def test_video_with_odd_size_to_mp4_with_sidecar(self) -> None:
        original = self.video.read_bytes()
        output = self.dir / "src_converted.mp4"
        task = ConverterTask(str(self.video), str(output), extract_audio=True)
        task.run()
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertIn("video", self.streams(output))
        self.assertEqual(self.streams(self.dir / "src_converted_audio.mp3"), ["audio"])
        self.assertEqual(self.video.read_bytes(), original)
        self.assertEqual([p for p in self.dir.iterdir() if p.name.endswith(".part")], [])

    def test_every_codec_really_encodes(self) -> None:
        cases = [("mp4", "h265"), ("mov", "prores"), ("mkv", "av1"), ("webm", "vp9"), ("mkv", "copy")]
        for ext, codec in cases:
            with self.subTest(ext=ext, codec=codec):
                output = self.dir / f"codec_{codec}.{ext}"
                ConverterTask(str(self.video), str(output),
                              settings=ConvertSettings(video_codec=codec, quality="small")).run()
                self.assertIn("video", self.streams(output))

    def test_every_target_format(self) -> None:
        for ext in ("mp4", "mkv", "avi", "mov", "webm", "mp3", "m4a", "wav", "aac", "flac", "opus"):
            with self.subTest(ext=ext):
                output = self.dir / f"all.{ext}"
                ConverterTask(str(self.video), str(output)).run()
                self.assertTrue(output.is_file())
                self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
