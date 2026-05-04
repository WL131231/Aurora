"""PyInstaller 빌드 스크립트 — Aurora Launcher 미니 .exe.

본체 (Aurora.exe) 와 별개 작은 wrapper:
    - pywebview + 표준 라이브러리만 (~10MB)
    - 본체 의존성 (numpy / pandas / ccxt / fastapi 등) 미포함

산출물:
    Windows: dist/Aurora-launcher.exe
    macOS:   dist/Aurora-launcher.app  (zip 으로 release 첨부)

사용법:
    python scripts/build_launcher.py

진입점: src/aurora_launcher/launcher.py
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_SEP = os.pathsep  # Windows ';' / Unix ':'

PLATFORM_HIDDEN_IMPORTS = {
    "Windows": ["webview.platforms.winforms"],
    "Darwin": ["webview.platforms.cocoa"],
    "Linux": ["webview.platforms.gtk", "webview.platforms.qt"],
}


PLATFORM_ICON = {
    "Windows": "aurora-launcher.ico",  # v0.1.14 — 본체와 구분되는 검정 다이아몬드
    "Darwin": "aurora-launcher.icns",
    "Linux": "aurora-launcher.png",
}
PLATFORM_ICON_FALLBACK = {
    "Windows": "aurora.ico",  # 검정 launcher 아이콘 미존재 시 본체 아이콘 fallback
    "Darwin": "aurora.icns",
    "Linux": "aurora.png",
}


def main() -> int:
    plat = platform.system()
    entry = PROJECT_ROOT / "src" / "aurora_launcher" / "launcher.py"
    ui_dir = PROJECT_ROOT / "src" / "aurora_launcher" / "ui"
    assets_dir = PROJECT_ROOT / "assets"

    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--name", "Aurora-launcher",
        "--windowed",
        "--paths", str(PROJECT_ROOT / "src"),
        # ui/ 번들 (HTML/CSS/JS) — _MEIPASS/ui 아래에 풀림
        "--add-data", f"{ui_dir}{DATA_SEP}ui",
        "--clean",
        "--noconfirm",
        "--onefile",
    ]

    # 런처 아이콘 — 검정 다이아몬드 (assets/aurora-launcher.ico, v0.1.14).
    # 미존재 시 본체 그라디언트 아이콘 fallback.
    icon_name = PLATFORM_ICON.get(plat)
    fallback_name = PLATFORM_ICON_FALLBACK.get(plat)
    chosen_icon = None
    if icon_name and (assets_dir / icon_name).exists():
        chosen_icon = assets_dir / icon_name
    elif fallback_name and (assets_dir / fallback_name).exists():
        chosen_icon = assets_dir / fallback_name
        print(f"[warn] launcher icon {icon_name} 미존재 → fallback {fallback_name}")
    if chosen_icon:
        cmd.extend(["--icon", str(chosen_icon)])

    for module in PLATFORM_HIDDEN_IMPORTS.get(plat, []):
        cmd.extend(["--hidden-import", module])

    # 본체 의존성 모두 exclude — launcher 는 표준 + pywebview 만
    for module in [
        "numpy", "pandas", "ccxt", "fastapi", "uvicorn", "pydantic",
        "matplotlib", "scipy", "pyarrow", "aurora.backtest",
    ]:
        cmd.extend(["--exclude-module", module])

    cmd.append(str(entry))

    print(f"[platform] {plat} ({sys.version})")
    print("[exec]", " ".join(cmd))
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
