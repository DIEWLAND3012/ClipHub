"""Create a "ClipHUB" shortcut on the Windows desktop that starts the app from source with its icon.

Uses .venv's pythonw.exe (no console window). Usage: python scripts/make_desktop_shortcut.py
Installed builds (.exe) don't need this: the installer's exe already carries the icon.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if sys.platform != "win32":
        print("Windows only; on macOS use the .app from the release")
        return 1
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    icon = ROOT / "assets" / "icons" / "cliphub.ico"
    script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
$link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'ClipHUB.lnk'))
$link.TargetPath = '{pythonw}'
$link.Arguments = '"{ROOT / "main.py"}"'
$link.WorkingDirectory = '{ROOT}'
$link.IconLocation = '{icon},0'
$link.Description = 'ClipHUB'
$link.Save()
Write-Output (Join-Path $desktop 'ClipHUB.lnk')
"""
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    print(result.stdout.strip() or result.stderr.strip())
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
