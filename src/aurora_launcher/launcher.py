"""Aurora Launcher — Pywebview 미니 GUI + 본체 .exe 자동 swap + 실행.

흐름 (사용자 마찰 0):
    1. launcher 더블클릭 → GUI 시작 + 백그라운드에서 GitHub Releases /latest 체크
    2. 새 버전 있으면 다이얼로그 → 사용자 "다운로드" 클릭 → ``Aurora.exe.new``
       다운 + 본체 .exe 와 swap (본체 실행 X 상태라 race condition 없음)
    3. 사용자 "Aurora 시작" 클릭 → ``subprocess.Popen(Aurora.exe, env=...)``
       으로 본체 실행 + launcher 종료
    4. 본체 측 자기-swap 은 ``AURORA_FROM_LAUNCHER`` env 받으면 skip — 중복 방지

본체 호환:
    launcher 안 쓰는 사용자 (직접 본체 .exe 실행) 도 그대로 작동 — 본체의
    ``apply_pending_update`` / ``start_background_check`` 가 fallback.

플랫폼:
    Windows 본 구현. macOS .app 번들 swap 은 본 모듈 미지원 (Phase 3 자체).

의존성:
    표준 라이브러리 + pywebview (GUI). PyInstaller 빌드 시 numpy/pandas 등 본체
    의존성 미포함 → 미니 .exe (~10MB).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from aurora_launcher import __version__

logger = logging.getLogger(__name__)

# ============================================================
# 설정 상수
# ============================================================

GITHUB_API_LATEST = "https://api.github.com/repos/WL131231/Aurora/releases/latest"
HTTP_TIMEOUT_SEC = 5

# 본체 .exe 이름 — release.yml 의 Aurora-windows.exe 와 정합
AURORA_EXE_NAME = "Aurora.exe"

# 본체 + .new + .old + .aurora_version 모두 격리 폴더 (v0.1.17).
# 사용자에게 launcher.exe 만 보이고 본체 파일들은 _aurora/ 안에 숨김.
AURORA_DATA_DIR = "_aurora"

# 본체에 전달할 env 마커 — 본체 자기-swap 중복 방지
LAUNCHER_ENV_MARKER = "AURORA_FROM_LAUNCHER"


# ============================================================
# 헬퍼
# ============================================================


def _is_frozen() -> bool:
    """PyInstaller bundle 환경 여부."""
    return bool(getattr(sys, "frozen", False))


def _launcher_dir() -> Path:
    """launcher .exe 가 있는 디렉토리 (본체 .exe 도 같은 폴더 가정)."""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    # dev 환경 — repo root
    return Path(__file__).resolve().parents[2]


def _aurora_data_dir() -> Path:
    """본체 데이터 격리 폴더 — ``<launcher_dir>/_aurora/`` (v0.1.17).

    사용자에게 launcher.exe 만 보이고 본체 .exe / .new / .old / .aurora_version
    은 모두 본 폴더 안에 숨김.
    """
    return _launcher_dir() / AURORA_DATA_DIR


def _aurora_exe_path() -> Path:
    """본체 Aurora.exe 절대 경로 — _aurora/ 폴더 안 (v0.1.17 격리)."""
    return _aurora_data_dir() / AURORA_EXE_NAME


def _migrate_legacy_layout() -> None:
    """v0.1.16 이전 layout (launcher 옆 Aurora.exe) → _aurora/ 로 이전.

    Why: 기존 사용자가 v0.1.17 launcher 받으면 _aurora/ 에 본체 없음 → 다시 다운.
    legacy Aurora.exe 가 launcher 옆에 있으면 자동으로 _aurora/ 안으로 이동 →
    재다운 비용 절감 + .new / .old 잔재 정리.
    """
    legacy_exe = _launcher_dir() / AURORA_EXE_NAME
    new_exe = _aurora_exe_path()
    if legacy_exe.exists() and not new_exe.exists():
        try:
            new_exe.parent.mkdir(parents=True, exist_ok=True)
            legacy_exe.rename(new_exe)
            logger.info("legacy %s → %s 이전 완료", legacy_exe, new_exe)
        except OSError as e:
            logger.warning("legacy 이전 실패 (재다운 필요): %s", e)
    # legacy .new / .old / .aurora_version 잔재 정리 (best-effort)
    for name in ("Aurora.exe.new", "Aurora.exe.old", ".aurora_version"):
        legacy = _launcher_dir() / name
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass


def _parse_version(raw: str) -> tuple[int, ...]:
    """``"v0.1.10"`` → ``(0, 1, 10)``."""
    s = raw.lstrip("v").split("-", 1)[0]
    parts: list[int] = []
    for chunk in s.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


# ============================================================
# GitHub API + 다운로드
# ============================================================


def fetch_latest_release() -> dict | None:
    """GitHub Releases /latest 호출 — 네트워크 실패 시 None."""
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310
            return json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        logger.debug("update check 실패 (조용히 skip): %s", e)
        return None


def find_aurora_exe_url(release: dict) -> str | None:
    """release assets 에서 Aurora-windows.exe URL 반환."""
    for asset in release.get("assets", []):
        if asset.get("name") == "Aurora-windows.exe":
            url = asset.get("browser_download_url")
            return str(url) if url else None
    return None


def download_to(url: str, target: Path) -> bool:
    """url → target 경로 다운로드. 실패 시 부분 파일 정리."""
    try:
        urllib.request.urlretrieve(url, str(target))  # noqa: S310
        return True
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.warning("download 실패: %s", e)
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        return False


def get_local_aurora_version() -> str | None:
    """본체 .exe 의 버전 추정 — Aurora.exe 옆 .version 파일 또는 모름.

    PyInstaller 빌드된 .exe 는 외부에서 버전 추출 어려움. 본체가 시작 시 .version
    파일 작성하는 패턴 (별도 PR) 또는 처음에는 None 반환.
    None 일 때는 launcher 가 무조건 최신 버전 다운 권유.
    """
    version_file = _aurora_data_dir() / ".aurora_version"
    if version_file.exists():
        try:
            return version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return None


# ============================================================
# Swap (본체 .exe 갱신)
# ============================================================


def apply_swap(downloaded_new: Path) -> bool:
    """다운로드된 .new → 본체 .exe 와 swap.

    본체 실행 X 상태라 lock 없음 → 안전 swap (race condition 없음).

    Args:
        downloaded_new: 임시 다운로드 경로 (예: ``Aurora.exe.new``).

    Returns:
        ``True`` swap 성공, ``False`` 실패.
    """
    exe = _aurora_exe_path()
    old_path = exe.with_suffix(exe.suffix + ".old")
    try:
        # _aurora/ 폴더 자동 생성 (v0.1.17 격리 흐름 첫 다운로드 케이스)
        exe.parent.mkdir(parents=True, exist_ok=True)
        # 기존 .old 정리
        if old_path.exists():
            old_path.unlink()
        # 본체 있으면 .old 로 백업
        if exe.exists():
            exe.rename(old_path)
        # .new → 본체
        downloaded_new.rename(exe)
        # 백업 정리 (성공 후 즉시)
        if old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass
        return True
    except OSError as e:
        logger.warning("swap 실패: %s", e)
        return False


# ============================================================
# 본체 실행
# ============================================================


def launch_aurora() -> bool:
    """본체 Aurora.exe 실행 — env 마커 전달로 본체 자기-swap 중복 방지.

    Returns:
        ``True`` 실행 시작 성공, ``False`` 본체 .exe 미존재.
    """
    exe = _aurora_exe_path()
    if not exe.exists():
        logger.error("본체 .exe 미존재: %s", exe)
        return False

    env = os.environ.copy()
    env[LAUNCHER_ENV_MARKER] = "1"

    # detached + fds 분리 — launcher 종료 후에도 본체 살림
    DETACHED_PROCESS = 0x00000008  # noqa: N806
    CREATE_NEW_PROCESS_GROUP = 0x00000200  # noqa: N806
    flags = (
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        if platform.system() == "Windows"
        else 0
    )

    try:
        subprocess.Popen(  # noqa: S603 — 본체 실행, 신뢰 가능
            [str(exe)],
            env=env,
            creationflags=flags,
            close_fds=True,
            # v0.1.17: cwd = launcher 옆 (본체는 _aurora/ 안). .env / config_store 등을
            # launcher 옆에서 찾게 → 사용자가 .env 를 launcher.exe 옆에 두면 인식 OK.
            cwd=str(_launcher_dir()),
        )
        return True
    except OSError as e:
        logger.error("본체 실행 실패: %s", e)
        return False


# ============================================================
# Pywebview API — JS bridge
# ============================================================


class LauncherApi:
    """Pywebview JS bridge — UI 가 호출하는 백엔드 메서드."""

    def get_local_version(self) -> str:
        """현재 설치된 본체 버전. 모르면 'unknown'."""
        v = get_local_aurora_version()
        return v if v else "unknown"

    def get_launcher_version(self) -> str:
        """launcher 자체 버전."""
        return __version__

    def check_update(self) -> dict:
        """업데이트 체크 — 결과를 UI 에 dict 로 반환.

        Returns:
            ``{"latest": str | None, "has_update": bool, "url": str | None,
              "error": str | None}``
        """
        release = fetch_latest_release()
        if release is None:
            return {"latest": None, "has_update": False, "url": None,
                    "error": "GitHub Releases 조회 실패 (네트워크 또는 rate limit)"}
        latest_tag = release.get("tag_name", "")
        url = find_aurora_exe_url(release)
        local_v = get_local_aurora_version()
        has_update = False
        if local_v is not None:
            try:
                has_update = _parse_version(latest_tag) > _parse_version(local_v)
            except (ValueError, TypeError):
                has_update = False
        else:
            # 로컬 버전 미상 — .aurora_version 파일 없음. 안전하게 has_update=True
            # 로 가정해 사용자 다운 권유. 첫 다운 후 .aurora_version 작성됨 → 다음
            # 부터는 정상 비교. (v0.1.14 fix — 이전엔 exe 존재 시 has_update=False
            # 라 사용자가 launcher 통해 swap 못 받음.)
            has_update = True
        return {"latest": latest_tag, "has_update": has_update, "url": url,
                "error": None}

    def download_and_swap(self, url: str) -> dict:
        """본체 .exe 다운로드 + swap. UI 가 progress 표시 위해 바로 반환.

        Returns:
            ``{"success": bool, "message": str}``
        """
        target = _aurora_exe_path().with_suffix(_aurora_exe_path().suffix + ".new")
        # _aurora/ 폴더 자동 생성 (첫 다운 시 폴더 없음)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not download_to(url, target):
            return {"success": False, "message": "다운로드 실패 (네트워크 확인)"}
        if not apply_swap(target):
            return {"success": False, "message": "swap 실패 (본체 .exe 권한 확인)"}
        # 새 버전 기록
        try:
            release = fetch_latest_release()
            if release is not None:
                version_file = _aurora_data_dir() / ".aurora_version"
                version_file.parent.mkdir(parents=True, exist_ok=True)
                version_file.write_text(
                    release.get("tag_name", "").lstrip("v"), encoding="utf-8",
                )
        except OSError:
            pass
        return {"success": True, "message": "업데이트 적용 완료"}

    def launch(self) -> dict:
        """본체 실행."""
        if launch_aurora():
            return {"success": True, "message": "Aurora 시작됨"}
        return {"success": False, "message": "본체 .exe 미존재 — 먼저 업데이트"}

    def quit(self) -> None:
        """launcher 종료 — pywebview 윈도우 destroy + os._exit (v0.1.18 fix).

        Why: 이전 sys.exit(0) 은 js_api thread 에서 호출되어 pywebview main thread
        가 catch 안 함 → launcher 종료 X (사용자 보고). webview.windows[0].destroy()
        + os._exit(0) 으로 강제 종료.
        """
        try:
            import webview  # type: ignore[import-not-found]
            if webview.windows:
                webview.windows[0].destroy()
        except Exception:  # noqa: BLE001 — 종료 흐름이라 예외 무시
            pass
        os._exit(0)  # noqa: S603 — 의도적 강제 종료


# ============================================================
# GUI 진입점
# ============================================================


def _ui_index_path() -> Path:
    """index.html 경로 — 빌드 / dev 모두 대응."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ui" / "index.html"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / "ui" / "index.html"


def main() -> None:
    """launcher 진입점 — pywebview 윈도우 시작."""
    import webview  # type: ignore[import-not-found]

    # v0.1.17: 이전 layout (launcher 옆 Aurora.exe) → _aurora/ 자동 이전.
    _migrate_legacy_layout()

    api = LauncherApi()
    ui_path = _ui_index_path()

    webview.create_window(
        "Aurora Launcher",
        str(ui_path),
        js_api=api,
        width=1280,                  # 본체 .exe 와 동일 크기 (v0.1.16 redesign)
        height=800,
        min_size=(960, 600),
        resizable=True,
        background_color="#1e202c",  # v0.1.15 brand bg
    )

    # GUI 떠 있는 동안 백그라운드로 자동 update check (1회)
    def _bg_check() -> None:
        api.check_update()  # 캐시 효과 — UI 가 다시 호출하면 빠른 응답

    threading.Thread(target=_bg_check, daemon=True).start()

    webview.start()


if __name__ == "__main__":
    main()
