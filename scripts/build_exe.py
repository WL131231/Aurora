"""PyInstaller 빌드 스크립트 — Aurora을 단일 .exe로 패키징.

사용법:
    python scripts/build_exe.py

요구사항:
    pip install pyinstaller

산출물:
    dist/Aurora.exe
    dist/Aurora/  (모든 의존 파일)

담당: 팀원 D
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """PyInstaller 호출."""
    entry = PROJECT_ROOT / "src" / "aurora" / "main.py"
    ui_dir = PROJECT_ROOT / "ui"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "Aurora",
        "--windowed",
        "--add-data", f"{ui_dir};ui",
        "--paths", str(PROJECT_ROOT / "src"),
        "--clean",
        "--noconfirm",
        str(entry),
    ]
    print("실행:", " ".join(cmd))
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
