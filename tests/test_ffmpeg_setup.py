"""core.ffmpeg_setup: remembered result, version compare, staged updates (no network)."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import ffmpeg, ffmpeg_setup

EXE = ".exe" if sys.platform == "win32" else ""


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for patcher in (mock.patch.object(ffmpeg_setup, "data_dir", return_value=self.data),
                        mock.patch.object(ffmpeg, "managed_dir", return_value=self.data / "ffmpeg")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _tools(self, folder: Path) -> tuple[str, str]:
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in ("ffmpeg", "ffprobe"):
            (folder / f"{name}{EXE}").write_text("", encoding="utf-8")
            paths.append(str(folder / f"{name}{EXE}"))
        return paths[0], paths[1]

    def test_version_compare(self) -> None:
        self.assertTrue(ffmpeg_setup.is_newer("9.0.2", "9.0.1"))
        self.assertTrue(ffmpeg_setup.is_newer("10.0", "9.0.2"))
        self.assertFalse(ffmpeg_setup.is_newer("9.0.2", "9.0.2"))
        self.assertFalse(ffmpeg_setup.is_newer("", "9.0"))
        self.assertTrue(ffmpeg_setup.is_newer("9.0", ""))

    def test_remembered_result_skips_the_search(self) -> None:
        tools = self._tools(self.data / "elsewhere")
        ffmpeg_setup.save_state({"source": "system", "ffmpeg": tools[0], "ffprobe": tools[1]})
        with mock.patch.object(ffmpeg, "find_tool", side_effect=AssertionError("searched")):
            self.assertFalse(ffmpeg_setup.needs_install())

    def test_found_tools_are_remembered(self) -> None:
        tools = self._tools(self.data / "system")
        with mock.patch.object(ffmpeg, "find_tool", side_effect=list(tools)), \
                mock.patch.object(ffmpeg_setup, "version_of", return_value="8.1"):
            self.assertFalse(ffmpeg_setup.needs_install())
        state = ffmpeg_setup.load_state()
        self.assertEqual((state["source"], state["ffmpeg"], state["version"]), ("system", tools[0], "8.1"))

    def test_missing_ffmpeg_needs_install(self) -> None:
        with mock.patch.object(ffmpeg, "find_tool", side_effect=Exception("missing")):
            self.assertTrue(ffmpeg_setup.needs_install())

    def test_stale_memory_is_checked_again(self) -> None:
        ffmpeg_setup.save_state({"ffmpeg": str(self.data / "gone"), "ffprobe": str(self.data / "gone")})
        with mock.patch.object(ffmpeg, "find_tool", side_effect=Exception("missing")):
            self.assertTrue(ffmpeg_setup.needs_install())

    def test_staged_update_is_swapped_in(self) -> None:
        self._tools(self.data / "ffmpeg" / "bin")
        staged = self._tools(self.data / "ffmpeg-update" / "bin")
        with mock.patch.object(ffmpeg_setup, "version_of", return_value="9.1"):
            ffmpeg_setup.apply_staged_update()
        self.assertFalse((self.data / "ffmpeg-update").exists())
        self.assertTrue((self.data / "ffmpeg" / "bin" / f"ffmpeg{EXE}").is_file())
        state = ffmpeg_setup.load_state()
        self.assertEqual((state["source"], state["version"]), ("managed", "9.1"))
        self.assertTrue(staged)

    def test_system_ffmpeg_is_never_updated(self) -> None:
        tools = self._tools(self.data / "system")
        ffmpeg_setup.save_state({"source": "system", "ffmpeg": tools[0], "ffprobe": tools[1], "checked": 0})
        with mock.patch.object(ffmpeg_setup, "latest_release", side_effect=AssertionError("network")):
            self.assertEqual(ffmpeg_setup.check_for_update(), "")

    def test_update_check_waits_between_checks(self) -> None:
        tools = self._tools(self.data / "ffmpeg" / "bin")
        ffmpeg_setup.save_state({"source": "managed", "ffmpeg": tools[0], "ffprobe": tools[1],
                                 "version": "9.0", "checked": time.time()})
        with mock.patch.object(ffmpeg_setup, "latest_release", side_effect=AssertionError("network")):
            self.assertEqual(ffmpeg_setup.check_for_update(), "")

    def test_offline_update_check_is_quiet(self) -> None:
        tools = self._tools(self.data / "ffmpeg" / "bin")
        ffmpeg_setup.save_state({"source": "managed", "ffmpeg": tools[0], "ffprobe": tools[1],
                                 "version": "9.0", "checked": 0})
        with mock.patch.object(ffmpeg_setup, "latest_release", side_effect=OSError("offline")):
            self.assertEqual(ffmpeg_setup.check_for_update(), "")


if __name__ == "__main__":
    unittest.main()
