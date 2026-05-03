"""GitHub Releases 기반 자동 업데이터 — PyInstaller .exe 자기 갱신.

흐름 (사용자 마찰 0):
    1. **시작 시** ``apply_pending_update()`` — 직전 실행에서 다운로드된 ``Aurora.exe.new``
       있으면 현재 exe 와 swap → 새 버전으로 재시작 (사용자는 한 번 끄면 새 버전).
    2. **백그라운드** ``start_background_check()`` — GitHub Releases API 호출 →
       최신 tag 가 ``__version__`` 보다 높으면 ``Aurora.exe.new`` 로 다운로드 (사용자 GUI
       사용 안 막음).
    3. **다음 시작** 1번이 swap → 사용자는 GUI 다시 켰을 때 자동으로 새 버전.

플랫폼 지원:
    - **Windows**: 같은 디렉토리 내 ``rename`` 으로 lock 우회 (PyInstaller --onefile).
    - **macOS**: ``.app`` 번들 swap 은 디렉토리 트리 교체라 본 모듈 미지원 — 다음 release
      페이지로 사용자 redirect (Phase 3 자체 구현 검토).

환경:
    - ``getattr(sys, 'frozen', False) is False`` (dev/pytest) → 모든 함수 no-op.
    - 네트워크 실패·API rate limit → 조용히 skip (다음 시작 때 재시도).

의존성:
    표준 라이브러리만 (urllib + threading) — PyInstaller bundle 부담 0.

담당: 정용우 (interfaces 영역, distribution 레이어)
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from aurora import __version__

logger = logging.getLogger(__name__)

# ============================================================
# 설정 상수
# ============================================================

GITHUB_API_LATEST = "https://api.github.com/repos/WL131231/Aurora/releases/latest"
HTTP_TIMEOUT_SEC = 5  # GitHub API + 네트워크 끊김 빠른 fail
DOWNLOAD_TIMEOUT_SEC = 300  # 60 MB 다운로드 여유

# 플랫폼별 release asset 이름 (workflow 의 Rename for release asset 단계 산출물)
ASSET_NAME = {
    "Windows": "Aurora-windows.exe",
    "Darwin": "Aurora-macOS.zip",  # 본 모듈 미지원 (자동 swap X)
}

# UI zip asset 이름 — OS 무관, .exe 와 별개 release artifact (PR b: UI 핫 업데이트)
UI_ASSET_NAME = "Aurora-ui.zip"

# UI override 디렉토리 이름 — .exe 옆에 풀어두면 webview 가 _MEIPASS 보다 우선 사용
UI_OVERRIDE_DIR = "ui_override"


# ============================================================
# 내부 헬퍼
# ============================================================


def _is_frozen() -> bool:
    """PyInstaller bundle 환경 여부 — dev/pytest 에서는 항상 False."""
    return bool(getattr(sys, "frozen", False))


def _parse_version(raw: str) -> tuple[int, ...]:
    """``"v0.1.0"`` → ``(0, 1, 0)`` — semantic 비교용.

    ``v`` prefix 제거 + dot split. 부분 (``"0.1"``) / pre-release (``"0.1.0-rc1"``)
    엔 부분만 매핑 (rc 무시). testing 단계 단순화.
    """
    s = raw.lstrip("v").split("-", 1)[0]  # "v0.1.0-rc1" → "0.1.0"
    parts: list[int] = []
    for chunk in s.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break  # "1.0a" 같은 비표준은 거기서 끊음
    return tuple(parts)


def _exe_path() -> Path:
    """현재 실행 중 PyInstaller .exe 의 절대 경로."""
    return Path(sys.executable).resolve()


# ============================================================
# Public API
# ============================================================


# Windows subprocess.Popen creation flags — 부모 process 와 완전 분리.
# DETACHED_PROCESS: 콘솔 X (GUI 앱)
# CREATE_NEW_PROCESS_GROUP: 부모 group 분리 (Ctrl+C 전파 X)
# CREATE_BREAKAWAY_FROM_JOB: 부모 job 객체와 분리 (PyInstaller 가 만든 job 영향 X)
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _spawn_clean_env(exe: Path) -> None:
    """새 .exe 를 부모 PyInstaller 환경과 완전 분리해 spawn.

    Why: ``apply_pending_update`` 가 ``subprocess.Popen([new_exe]) + sys.exit(0)``
    로 단순 spawn 하면 새 process 가 부모의 ``_MEIPASS`` / ``_PYI_*`` env 상속.
    부모 atexit hook (PyInstaller 가 ``_MEI<random>`` 임시 디렉토리 정리) 가
    새 process 의 numpy 등 import 와 race → ``numpy.linalg`` circular import.

    해결책 (3겹):
        1. ``_MEI`` / ``_PYI`` env 키 제거 → 새 process 가 자기 ``_MEI`` 만들게 강제
        2. ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB``
           → 부모 process 종료가 새 process 수명 영향 X
        3. ``close_fds=True`` → 부모의 file handle 안 상속

    Args:
        exe: 새로 시작할 .exe 경로 (swap 직후의 정상 .exe).
    """
    # 1. env 정리 — PyInstaller bootloader 가 set 한 키 제거
    clean_env = {
        k: v for k, v in os.environ.items()
        if not (k.startswith("_MEI") or k.startswith("_PYI"))
    }

    # 2. flags — Windows 전용 분리
    flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB

    subprocess.Popen(  # noqa: S603 — 자기 자신 재시작, 신뢰 가능
        [str(exe)],
        env=clean_env,
        creationflags=flags,
        close_fds=True,
        cwd=str(exe.parent),  # CWD = .exe 디렉토리 (.env 옆에서 읽기 위해)
    )


def apply_pending_update() -> bool:
    """직전 다운로드된 업데이트가 있으면 swap → 새 버전 재시작.

    main.py 가장 처음 호출. swap 성공하면 ``sys.exit(0)`` 으로 현재 프로세스 종료
    후 새 exe 실행 → 본 함수는 반환되지 않음. swap 실패·해당 없음 시 ``False`` 반환.

    Returns:
        ``True`` (실제로는 도달 안 함) — swap + 재시작 트리거. ``False`` — no-op.

    Side effects:
        - ``Aurora.exe.old`` 정리 (이전 swap 잔재).
        - ``Aurora.exe`` ↔ ``Aurora.exe.new`` rename.
        - ``_spawn_clean_env`` 으로 새 exe 시작 (부모 _MEI env 분리) + ``sys.exit(0)``.
        - 짧은 ``time.sleep(0.5)`` — 새 process 가 _MEI 풀 시간 확보.
    """
    if not _is_frozen():
        return False

    if platform.system() != "Windows":
        # macOS .app swap 은 본 모듈 미지원 — 사용자가 release 페이지 수동 다운로드
        return False

    exe = _exe_path()
    new_path = exe.with_suffix(exe.suffix + ".new")
    old_path = exe.with_suffix(exe.suffix + ".old")

    if not new_path.exists():
        return False

    try:
        # 1. 이전 .old 정리 (혹시 직전 실행에서 정리 못한 잔재)
        if old_path.exists():
            old_path.unlink()
        # 2. 현재 .exe → .old (Windows 는 lock 중인 .exe 도 rename 은 가능)
        exe.rename(old_path)
        # 3. .new → .exe
        new_path.rename(exe)
        logger.info("auto-update applied: %s → %s (재시작)", new_path.name, exe.name)
        # 4. 새 exe spawn — 부모 _MEI env 분리 (numpy circular import race fix)
        _spawn_clean_env(exe)
        # 5. 짧은 대기 — 새 process 의 _MEI 풀기가 부모 정리와 race 안 나게
        time.sleep(0.5)
        sys.exit(0)
    except OSError as e:
        logger.warning("auto-update apply 실패 (사용자 직접 다운 권장): %s", e)
        # rollback — .new 그대로 두면 다음 시작 시 또 시도
        return False


def fetch_latest_release() -> dict | None:
    """GitHub API ``/releases/latest`` 호출 — 네트워크 실패 시 ``None``.

    Returns:
        ``{"tag_name": "v0.1.1", "assets": [...]}`` 형태. 비공개 repo / rate limit /
        네트워크 끊김 시 ``None``.
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310 — https 고정
            return json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        logger.debug("update check 실패 (조용히 skip): %s", e)
        return None


def is_newer(remote_tag: str, local_version: str = __version__) -> bool:
    """``remote_tag`` 가 ``local_version`` 보다 높은가."""
    try:
        return _parse_version(remote_tag) > _parse_version(local_version)
    except (ValueError, TypeError):
        return False


def download_update(asset_url: str, target: Path) -> bool:
    """asset URL → ``target`` 경로 다운로드. 실패 시 부분 파일 정리.

    Args:
        asset_url: GitHub release asset 의 ``browser_download_url``.
        target: 저장 경로 (보통 ``Aurora.exe.new``).

    Returns:
        ``True`` 성공, ``False`` 실패.
    """
    try:
        # urlretrieve 는 대용량도 stream 으로 처리 — 메모리 폭주 X
        urllib.request.urlretrieve(asset_url, str(target))  # noqa: S310 — https 고정
        return True
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.warning("update download 실패: %s", e)
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        return False


# ============================================================
# UI 핫 업데이트 (PR b)
# ============================================================
#
# 동작 흐름:
#     1. /update/apply_ui POST → fetch_latest_release() → assets 에서 Aurora-ui.zip 찾기
#     2. download_ui_zip() → 임시 zip 다운로드
#     3. apply_ui_update() → <exe_dir>/ui_override/ 에 풀어둠 (기존 override 정리 후 swap)
#     4. webview.py 의 _ui_index_path() 가 ui_override/ 우선 lookup → 새 GUI 즉시 로드
#         (사용자는 location.reload() 만 하면 됨, 앱 종료 X)


def find_ui_asset_url(release: dict) -> str | None:
    """release dict 의 assets 에서 ``Aurora-ui.zip`` 의 ``browser_download_url`` 반환."""
    for asset in release.get("assets", []):
        if asset.get("name") == UI_ASSET_NAME:
            url = asset.get("browser_download_url")
            return str(url) if url else None
    return None


def apply_ui_update(zip_path: Path, exe_dir: Path) -> bool:
    """UI zip 을 ``<exe_dir>/ui_override/`` 에 풀기 — 기존 override 정리 후 swap.

    Args:
        zip_path: 다운로드된 ``Aurora-ui.zip`` 임시 파일.
        exe_dir: .exe 가 있는 디렉토리 (override 위치 기준).

    Returns:
        ``True`` 성공, ``False`` zip 손상·디렉토리 권한 에러.

    Side effects:
        - 기존 ``<exe_dir>/ui_override/`` 통째 삭제 후 새 zip 풀기.
        - 처리 중 에러 시 부분 디렉토리 그대로 남을 수 있음 — 다음 실행 시 정리.
    """
    import shutil
    import zipfile

    target = exe_dir / UI_OVERRIDE_DIR
    try:
        # 기존 override 정리 (안전: 일부만 풀린 상태 방지)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            # zip slip 방지 — 모든 엔트리가 target 안에 있는지 검증
            for name in zf.namelist():
                resolved = (target / name).resolve()
                if not str(resolved).startswith(str(target.resolve())):
                    logger.warning("UI zip slip 의심 (skip): %s", name)
                    return False
            zf.extractall(target)
        # zip 자체 정리 (다음 다운로드를 위해)
        try:
            zip_path.unlink()
        except OSError:
            pass
        return True
    except (zipfile.BadZipFile, OSError) as e:
        logger.warning("UI 적용 실패: %s", e)
        return False


def _check_and_download_sync() -> None:
    """백그라운드 thread 의 실제 작업 — check + download 직렬 실행."""
    if not _is_frozen() or platform.system() != "Windows":
        return  # dev/pytest/macOS no-op

    release = fetch_latest_release()
    if release is None:
        return

    tag = release.get("tag_name", "")
    if not is_newer(tag):
        logger.debug("update check: 현재 최신 (%s)", __version__)
        return

    asset_name = ASSET_NAME.get(platform.system())
    if asset_name is None:
        return

    asset_url: str | None = None
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            asset_url = asset.get("browser_download_url")
            break

    if asset_url is None:
        logger.debug("update check: %s 새 버전 있으나 asset 미발견 (%s)", tag, asset_name)
        return

    target = _exe_path().with_suffix(_exe_path().suffix + ".new")
    if target.exists():
        # 직전 실행에서 다운로드 완료 — 사용자가 끄고 켜면 apply_pending_update 가 적용
        logger.info("update %s 이미 다운로드 완료 — 다음 실행 시 적용", tag)
        return

    logger.info("update %s 발견 → 백그라운드 다운로드 시작 (사용자 GUI 사용 가능)", tag)
    if download_update(asset_url, target):
        logger.info("update %s 다운로드 완료 → 다음 실행 시 자동 적용", tag)


def start_background_check() -> None:
    """백그라운드 thread 에서 update check + 다운로드 실행 — main.py 가 호출.

    Daemon thread 라 메인 종료 시 같이 죽음 (다운로드 진행 중이면 부분 파일 정리).
    """
    if not _is_frozen():
        return  # dev 에서는 시동 비용 없음
    t = threading.Thread(target=_check_and_download_sync, daemon=True, name="updater")
    t.start()
