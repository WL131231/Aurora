"""PyInstaller 빌드 스크립트 — Aurora 를 단일 .exe 로 패키징.

사용법:
    # venv 활성화 후
    python scripts/build_exe.py

    # 또는 단일 파일 (.exe 한 개로 합침, 처음 실행 5-10초 unpack)
    python scripts/build_exe.py --onefile

요구사항:
    pip install pyinstaller

산출물 (기본 = 폴더 형태):
    dist/Aurora/Aurora.exe    ← 더블클릭 실행
    dist/Aurora/_internal/    ← 의존성 (이동 시 폴더째)

산출물 (--onefile):
    dist/Aurora.exe           ← 단일 파일, 다른 사람한테 줘도 됨

진입점:
    src/aurora/interfaces/webview.py — ``if __name__ == "__main__": launch()``
    이 모듈이 직접 ``webview.launch()`` 호출 → Pywebview 윈도우 + FastAPI 통합 기동.

담당: 정용우 (interfaces 영역)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """PyInstaller 호출."""
    onefile = "--onefile" in sys.argv

    # 진입점 = interfaces/webview.py (launch() 자동 호출)
    entry = PROJECT_ROOT / "src" / "aurora" / "interfaces" / "webview.py"
    ui_dir = PROJECT_ROOT / "ui"
    icon_path = PROJECT_ROOT / "assets" / "aurora.ico"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "Aurora",
        "--windowed",                          # 콘솔 창 안 뜸 (GUI only)
        "--add-data", f"{ui_dir};ui",          # ui/ 폴더 번들링
        "--paths", str(PROJECT_ROOT / "src"),  # aurora 패키지 import 경로
        # PyInstaller 가 동적 import 못 잡는 모듈 명시 (pywebview 백엔드 등)
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--clean",
        "--noconfirm",
    ]

    # 앱 아이콘 (assets/aurora.ico) — generate_icon.py 로 생성
    if icon_path.exists():
        cmd.extend(["--icon", str(icon_path)])
    else:
        print(f"⚠ 아이콘 파일 없음: {icon_path}")
        print("   먼저 실행: python scripts/generate_icon.py")

    if onefile:
        cmd.append("--onefile")

    cmd.append(str(entry))

    print("실행:", " ".join(cmd))
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
