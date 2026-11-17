"""Tests for core.hwaccel: GPU encoder options, engine choice and parallel limits (no hardware needed)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.errors import ToolFailedError
from core.hwaccel import Engine, cpu_engine, encoder_args, parallel_limit, pick_engine
from tasks import converter
from tasks.compressor import build_compress_args
from tasks.converter import ConverterTask, ConvertSettings, build_convert_args

NV = Engine("nvenc:0", "nvenc", "NVIDIA", frozenset({"h264", "h265", "av1"}), 0, slots=3)
NV1 = Engine("nvenc:1", "nvenc", "NVIDIA 2", frozenset({"h264", "h265"}), 1, slots=3)
QSV = Engine("qsv", "qsv", "Intel", frozenset({"h264", "h265"}), slots=3)
CPU = cpu_engine()


def pairs(args: list[str]) -> dict[str, str]:
    return {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("-")}


class EncoderArgsTests(unittest.TestCase):
    def test_constant_quality_per_family(self) -> None:
        self.assertEqual(pairs(encoder_args(NV, "h265"))["-c:v"], "hevc_nvenc")
        self.assertEqual(pairs(encoder_args(NV, "h265"))["-cq"], "21")
        self.assertEqual(pairs(encoder_args(NV1, "h264"))["-gpu"], "1")  # second NVIDIA card
        qsv = pairs(encoder_args(QSV, "h264"))
        self.assertEqual((qsv["-c:v"], qsv["-global_quality"], qsv["-pix_fmt"]), ("h264_qsv", "20", "nv12"))
        amf = pairs(encoder_args(Engine("amf", "amf", "AMD", frozenset({"h264"})), "h264"))
        self.assertEqual((amf["-c:v"], amf["-rc"]), ("h264_amf", "cqp"))
        vt = pairs(encoder_args(Engine("videotoolbox", "videotoolbox", "Apple", frozenset({"h265"})), "h265"))
        self.assertEqual(vt["-c:v"], "hevc_videotoolbox")

    def test_target_bitrate(self) -> None:
        nv = pairs(encoder_args(NV, "h264", 2500))
        self.assertEqual((nv["-b:v"], nv["-maxrate"]), ("2500k", "2500k"))
        self.assertNotIn("-cq", encoder_args(NV, "h264", 2500))
        self.assertEqual(pairs(encoder_args(QSV, "h264", 900))["-b:v"], "900k")

    def test_cpu_is_not_a_gpu_engine(self) -> None:
        with self.assertRaises(ValueError):
            encoder_args(CPU, "h264")


class PickEngineTests(unittest.TestCase):
    engines = [CPU, QSV, NV]

    def test_spreads_jobs_over_gpus(self) -> None:
        self.assertIs(pick_engine("h264", self.engines, {}), NV)
        self.assertIs(pick_engine("h264", self.engines, {"nvenc:0": 1}), QSV)  # GPU0 has a job -> GPU1
        self.assertIs(pick_engine("h264", self.engines, {"nvenc:0": 1, "qsv": 1}), NV)  # both loaded: fastest

    def test_gpu_codecs_wait_for_a_gpu_instead_of_the_cpu(self) -> None:
        full = {"nvenc:0": 3, "qsv": 3}
        self.assertIsNone(pick_engine("h264", self.engines, full))
        self.assertIs(pick_engine("h264", [CPU, QSV], {"qsv": 2}), QSV)

    def test_codec_support(self) -> None:
        self.assertIs(pick_engine("av1", [CPU, QSV], {}), CPU)  # this iGPU cannot encode AV1
        self.assertIs(pick_engine("vp9", self.engines, {}), CPU)

    def test_gpu_switched_off(self) -> None:
        self.assertIs(pick_engine("h264", self.engines, {}, use_gpu=False), CPU)

    def test_jobs_without_video_need_no_engine(self) -> None:
        self.assertIsNone(pick_engine(None, self.engines, {}))

    def test_parallel_limit(self) -> None:
        self.assertEqual(parallel_limit([CPU]), 2)
        self.assertEqual(parallel_limit([CPU, QSV]), 4)
        self.assertEqual(parallel_limit([CPU, QSV, NV, NV1]), 6)
        self.assertEqual(parallel_limit([CPU, QSV], setting=1), 1)


class TaskGpuArgsTests(unittest.TestCase):
    def test_converter_uses_gpu_encoder(self) -> None:
        args = build_convert_args("in.mkv", "o.part", "mp4", ConvertSettings(video_codec="h265"), engine=QSV)
        opts = pairs(args)
        self.assertEqual(opts["-c:v"], "hevc_qsv")
        self.assertEqual(opts["-tag:v"], "hvc1")
        self.assertIn("-vf", args)

    def test_converter_keeps_cpu_for_codecs_the_gpu_lacks(self) -> None:
        args = build_convert_args("in.mkv", "o.part", "mkv", ConvertSettings(video_codec="av1"), engine=QSV)
        self.assertEqual(pairs(args)["-c:v"], "libsvtav1")

    def test_compressor_uses_gpu_at_target_bitrate(self) -> None:
        opts = pairs(build_compress_args("in.mp4", "o.part", 1800, 128, engine=NV))
        self.assertEqual((opts["-c:v"], opts["-b:v"]), ("h264_nvenc", "1800k"))
        self.assertEqual(pairs(build_compress_args("in.mp4", "o.part", 1800, 128))["-c:v"], "libx264")

    def test_task_resources(self) -> None:
        self.assertEqual(ConverterTask("a.mkv", "a.mp4").resource(), "h264")
        self.assertEqual(ConverterTask("a.mkv", "a.mkv", settings=ConvertSettings(video_codec="vp9")).resource(), "vp9")
        self.assertIsNone(ConverterTask("a.mkv", "a.mp3").resource())
        self.assertIsNone(ConverterTask("a.mkv", "b.mkv", settings=ConvertSettings(video_codec="copy")).resource())

    def test_converter_falls_back_to_cpu_when_the_gpu_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.mkv"
            source.write_bytes(b"x")
            output = Path(tmp) / "out.mp4"
            task = ConverterTask(str(source), str(output))
            task.engine = QSV
            calls: list[list[str]] = []
            messages: list[str] = []
            task.set_progress_callback(lambda _p, m: messages.append(m))

            def fake_run(args, *, duration, on_progress=None, is_cancelled=lambda: False):
                calls.append(list(args))
                if "h264_qsv" in args:
                    raise ToolFailedError("FFmpeg ทำงานไม่สำเร็จ: device not found")
                Path(args[-1]).write_bytes(b"out")

            with mock.patch.object(converter, "probe_duration", return_value=5.0), \
                    mock.patch.object(converter, "run_ffmpeg", fake_run):
                task.run()
            self.assertTrue(output.is_file())
            self.assertEqual(len(calls), 2)
            self.assertIn("libx264", calls[1])
            self.assertTrue(any("ใช้ CPU แทน" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
