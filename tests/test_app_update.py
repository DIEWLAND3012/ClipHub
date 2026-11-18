"""core.app_update, core.gpu_pack and core.uninstaller: release parsing and the scripts they write."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from core import app_update, gpu_pack, uninstaller


def _release(tag: str, *names: str, draft: bool = False, prerelease: bool = False) -> dict:
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "body": "notes",
            "assets": [{"name": n, "browser_download_url": f"{app_update.DOWNLOAD_PREFIX}{tag}/{n}", "size": 10,
                        "digest": "sha256:" + "ab" * 32} for n in names]}


class ReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = [
            _release("v2.3.0", "ClipHUB-Windows-v2.3.0.exe", "ClipHUB-macOS-v2.3.0.dmg"),
            _release("v2.4.0-beta", "ClipHUB-Windows-v2.4.0.exe", prerelease=True),
            _release("v2.1.0", "ClipHUB-Windows-v2.1.0.exe", "ClipHUB-macOS-v2.1.0.dmg"),
            _release("v2.0.1", "ClipHUB-Windows.exe", "ClipHUB-macOS.dmg"),  # older, unversioned names
            _release("v1.0.0", "source.zip"),
        ]

    def test_parse_for_each_platform(self) -> None:
        for platform, ext in (("win32", ".exe"), ("darwin", ".dmg")):
            with mock.patch.object(sys, "platform", platform):
                releases = app_update.parse_releases(self.data)
            self.assertEqual([r.version for r in releases], ["2.3.0", "2.1.0", "2.0.1"])
            self.assertTrue(all(r.url.endswith(ext) for r in releases))
            self.assertEqual(releases[0].sha256, "ab" * 32)

    def test_newest_only_when_newer(self) -> None:
        releases = app_update.parse_releases(self.data)
        with mock.patch.object(app_update, "__version__", "2.2.0"):
            self.assertEqual(app_update.newest(releases).version, "2.3.0")
        with mock.patch.object(app_update, "__version__", "2.3.0"):
            self.assertIsNone(app_update.newest(releases))
        self.assertIsNone(app_update.newest([]))

    def test_source_run_cannot_self_update(self) -> None:
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertFalse(app_update.can_self_update())

    def test_swap_scripts(self) -> None:
        win = app_update.windows_swap_script(r"C:\d\new.exe", r"C:\Apps\ClipHUB.exe")
        self.assertIn(r'move /y "C:\d\new.exe" "C:\Apps\ClipHUB.exe"', win)
        self.assertIn(r'start "" "C:\Apps\ClipHUB.exe"', win)
        mac = app_update.mac_swap_script("/tmp/a.dmg", "/Applications/ClipHUB.app", 42)
        self.assertIn("kill -0 42", mac)
        self.assertIn('ditto "$MNT/ClipHUB.app" /Applications/ClipHUB.app', mac)
        self.assertIn("open /Applications/ClipHUB.app", mac)

    def test_scripts_cannot_be_injected(self) -> None:
        win = app_update.windows_swap_script("C:\\Users\\a%PATH%b\\new.exe", "C:\\Apps\\ClipHUB.exe")
        self.assertIn("a%%PATH%%b", win)
        with self.assertRaises(ValueError):
            app_update.windows_swap_script('C:\\x" & calc & "', "C:\\Apps\\ClipHUB.exe")
        mac = app_update.mac_swap_script("/tmp/$(touch pwned).dmg", "/Applications/ClipHUB.app", 1)
        self.assertIn("'/tmp/$(touch pwned).dmg'", mac)

    def test_only_this_repos_installers(self) -> None:
        data = [_release("v9.0.0", "ClipHUB-Windows-v9.exe", "ClipHUB-macOS-v9.dmg")]
        for asset in data[0]["assets"]:
            asset["browser_download_url"] = "https://evil.example/" + asset["name"]
        self.assertEqual(app_update.parse_releases(data), [])


BUNDLE = os.path.join(os.sep, "tmp", "_MEI123")  # paths that suit this OS's PATH separator
KEEP = os.path.join(os.sep, "usr", "bin")


class CleanEnvTests(unittest.TestCase):
    def test_pyinstaller_markers_are_dropped(self) -> None:
        from core import scripts
        fake = {"PATH": os.pathsep.join([BUNDLE, KEEP]), "_PYI_ARCHIVE_FILE": "x", "_PYI_APPLICATION_HOME_DIR": "y",
                "_MEIPASS2": "z", "HOME": "h"}
        with mock.patch.dict("os.environ", fake, clear=True), mock.patch.object(sys, "_MEIPASS", BUNDLE,
                                                                                create=True):
            env = scripts.clean_env()
        self.assertNotIn("_PYI_ARCHIVE_FILE", env)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)
        self.assertNotIn("_MEIPASS2", env)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(env["PATH"], KEEP)
        self.assertEqual(env["HOME"], "h")


class GpuPackTests(unittest.TestCase):
    def test_only_cuda_dlls_are_kept(self) -> None:
        self.assertTrue(gpu_pack._wanted("nvidia/cublas/bin/cublas64_12.dll"))
        self.assertTrue(gpu_pack._wanted("nvidia/cublas/bin/cublasLt64_12.dll"))
        self.assertTrue(gpu_pack._wanted("nvidia/cudnn/bin/cudnn_ops64_9.dll"))
        self.assertFalse(gpu_pack._wanted("nvidia/cublas/bin/nvblas64_12.dll"))
        self.assertFalse(gpu_pack._wanted("nvidia/cublas/include/cublas.h"))
        self.assertFalse(gpu_pack._wanted("nvidia/cudnn/lib/cudnn.lib"))


class UninstallTests(unittest.TestCase):
    def test_scripts_remove_every_target(self) -> None:
        paths = [Path(r"C:\Users\a\AppData\Local\ClipHUB"), Path(r"C:\Apps\ClipHUB.exe")]
        win = uninstaller.windows_script(paths, 99)
        self.assertIn('PID eq 99', win)
        for path in paths:
            self.assertIn(f'rmdir /s /q "{path}"', win)
            self.assertIn(f'del /f /q "{path}"', win)
        self.assertIn(r"reg delete HKCU\Software\ClipHUB", win)
        mac = uninstaller.mac_script([Path("/x/ClipHUB.app")], 7)
        self.assertIn("rm -rf", mac)
        self.assertIn("ClipHUB.app", mac)

    def test_never_removes_home_or_root(self) -> None:
        self.assertFalse(uninstaller._safe(Path.home()))
        self.assertFalse(uninstaller._safe(Path(Path.home().anchor)))
        self.assertTrue(uninstaller._safe(Path.home() / "AppData" / "Local" / "ClipHUB"))

    def test_source_run_keeps_the_source(self) -> None:
        with mock.patch.object(sys, "frozen", False, create=True):
            targets = uninstaller.targets()
        self.assertFalse(any(str(t).startswith(str(Path(__file__).resolve().parents[1])) for t in targets))


if __name__ == "__main__":
    unittest.main()
