"""PyInstaller 빌드 스크립트 — Aurora 를 크로스플랫폼 단일 실행기로 패키징.

지원 플랫폼:
    - Windows: ``.exe`` (단일 파일 또는 폴더)
    - macOS:   ``.app`` 번들 (zip 으로 배포)
    - Linux:   ELF 단일 파일 (Phase 3 검토)

사용법:
    # venv 활성화 후
    python scripts/build_exe.py             # 폴더 빌드 (기본)
    python scripts/build_exe.py --onefile   # 단일 파일

요구사항:
    pip install pyinstaller

산출물 (Windows):
    --onefile X: dist/Aurora/Aurora.exe + _internal/ 폴더
    --onefile O: dist/Aurora.exe (단일)

산출물 (macOS):
    dist/Aurora.app/ (번들)  ← 이걸 zip 으로 배포
    --onefile 옵션 없이 자동 .app 생성 (--windowed 효과)

진입점:
    src/aurora/main.py — main() 호출 → bot_instance.configure_from_settings + webview.launch

담당: 정용우 (interfaces 영역)
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# 플랫폼 감지 + 분기
# ============================================================

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# PyInstaller --add-data separator: Windows ';' / Unix ':' (os.pathsep 자동 분기)
DATA_SEP = os.pathsep

# 플랫폼별 pywebview 백엔드 — PyInstaller 가 동적 import 못 잡으니 명시
# Windows: EdgeChromium (winforms), macOS: WebKit (cocoa), Linux: GTK/QT
PLATFORM_HIDDEN_IMPORTS = {
    "Windows": ["webview.platforms.winforms"],
    "Darwin":  ["webview.platforms.cocoa"],
    "Linux":   ["webview.platforms.gtk", "webview.platforms.qt"],
}

# 플랫폼별 아이콘 파일 (assets/ 에 있으면 사용, 없으면 skip)
PLATFORM_ICON = {
    "Windows": "aurora.ico",
    "Darwin":  "aurora.icns",
    "Linux":   "aurora.png",
}


def main() -> int:
    """PyInstaller 호출."""
    onefile = "--onefile" in sys.argv
    plat = platform.system()

    # 진입점 = main.py (BotInstance auto-configure + webview.launch)
    # webview.py 직접 entry 도 가능하지만 main.py 가 통합 hub (PR #48)
    entry = PROJECT_ROOT / "src" / "aurora" / "main.py"
    ui_dir = PROJECT_ROOT / "ui"
    data_dir = PROJECT_ROOT / "data"   # team_aliases.json 등 (PR #60)
    assets_dir = PROJECT_ROOT / "assets"

    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--name", "Aurora",
        "--windowed",                           # GUI only (콘솔 창 X)
        "--paths", str(PROJECT_ROOT / "src"),   # aurora 패키지 import 경로
        # ui/ 데이터 번들 — PyInstaller 가 sys._MEIPASS 아래에 풀어둠
        "--add-data", f"{ui_dir}{DATA_SEP}ui",
        "--clean",
        "--noconfirm",
    ]

    # data/team_aliases.json 등 매핑·샘플 (PR #60 testing 단계 한정)
    if data_dir.exists():
        cmd.extend(["--add-data", f"{data_dir}{DATA_SEP}data"])

    # 플랫폼별 hidden imports
    for module in PLATFORM_HIDDEN_IMPORTS.get(plat, []):
        cmd.extend(["--hidden-import", module])

    # uvicorn 동적 import 모듈 (cross-platform 공통)
    for module in [
        "uvicorn.logging",
        "uvicorn.loops", "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan", "uvicorn.lifespan.on",
    ]:
        cmd.extend(["--hidden-import", module])

    # 봇 런타임에 안 쓰이는 백테스트/데이터 수집 의존성 — .exe 사이즈 폭증 방지.
    # tenacity 는 라이브 어댑터 (ccxt_client) 가 retry 사용 → exclude 풀림 (PR #53 E-11)
    # aurora.backtest exclude 도 풀림 (2026-05-03 발견):
    #   - exchange.data 가 from aurora.backtest.replay import TF_MINUTES 사용
    #   - exchange.ccxt_client 가 from aurora.backtest.tf import normalize_to_ccxt 사용
    #   - 봇 런타임도 backtest 모듈 의존 → exclude 시 ModuleNotFoundError
    #   - backtest 모듈 자체는 가벼움 (pyarrow 만 분리해서 exclude 유지)
    cmd.extend([
        "--exclude-module", "pyarrow",          # parquet 엔진 (50 MB+) — fetch_ohlcv 전용
    ])

    # 앱 아이콘 (플랫폼별)
    icon_name = PLATFORM_ICON.get(plat)
    if icon_name:
        icon_path = assets_dir / icon_name
        if icon_path.exists():
            cmd.extend(["--icon", str(icon_path)])
        else:
            # ASCII only — Windows cp1252 stdout 회피 (CI 환경 안전)
            print(f"[warn] icon not found: {icon_path} (skip)")
            if IS_WINDOWS:
                print("       generate via: python scripts/generate_icon.py")

    # --onefile 옵션 (사용자 명시 시)
    # macOS 는 --onefile 사용해도 .app 번들 생성됨 (PyInstaller 동작)
    if onefile:
        cmd.append("--onefile")

    cmd.append(str(entry))

    # ASCII only — CI Windows runner 의 cp1252 stdout encoding 회피.
    # 한글 출력은 콘솔 한정 (사용자 머신은 UTF-8 가정해도 안전 X).
    print(f"[platform] {plat} ({sys.version})")
    print("[exec]", " ".join(cmd))
    rc = subprocess.run(cmd, check=False).returncode

    # v0.1.74 (ChoYoon Claude #133 fix O): macOS .app Info.plist 후처리 —
    # LSMinimumSystemVersion=13.0 + 버전 메타 박음. PyInstaller 측 default
    # Info.plist 박힘 → 후처리로 minimum macOS version + 버전 박음. Finder/
    # Launchpad 사전 호환 검증 + LC_BUILD_VERSION 정합.
    if rc == 0 and plat == "Darwin":
        _patch_info_plist()

    return rc


def _patch_info_plist() -> None:
    """v0.1.74: macOS .app Info.plist 후처리 — LSMinimumSystemVersion 박음.

    PyInstaller 측 .app 번들 자동 생성 후 `Aurora.app/Contents/Info.plist` 에
    macOS 13 minimum + 버전 메타 박음. ChoYoon Claude #133 fix O.
    """
    import plistlib

    app_path = PROJECT_ROOT / "dist" / "Aurora.app"
    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.exists():
        print(f"[warn] Info.plist not found: {plist_path} (skip patch)")
        return
    try:
        with plist_path.open("rb") as f:
            plist = plistlib.load(f)
        plist["LSMinimumSystemVersion"] = "13.0"
        plist["NSHighResolutionCapable"] = True
        # 버전 메타 — pyproject.toml 또는 hardcoded (Phase 1 hardcoded). frozen
        # 본체는 __version__ 박혀있어 별도 출처 fetching X.
        try:
            from aurora import __version__ as _v
            plist["CFBundleVersion"] = _v
            plist["CFBundleShortVersionString"] = _v
        except ImportError:
            pass
        with plist_path.open("wb") as f:
            plistlib.dump(plist, f)
        print(f"[ok] Info.plist patched: LSMinimumSystemVersion=13.0 ({plist_path})")
    except (OSError, plistlib.InvalidFileException) as e:
        print(f"[warn] Info.plist patch failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
