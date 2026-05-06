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
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from aurora_launcher import __version__

logger = logging.getLogger(__name__)

# ============================================================
# 설정 상수
# ============================================================

GITHUB_API_LATEST = "https://api.github.com/repos/WL131231/Aurora/releases/latest"
# v0.1.59: 5 → 15초 보강 (방화벽 / 외부 네트워크 환경에서 GitHub API 응답 5초 넘음 보고).
HTTP_TIMEOUT_SEC = 15

# v0.1.59: GitHub API 공식 정책 — 모든 요청 User-Agent 필수, 미설정 시 403 거부 가능.
# 이전엔 urllib 기본 ("Python-urllib/X.Y") 박혀 일부 환경에서 거부 → "GitHub Releases
# 조회 실패" 에러. ChoYoon Claude #133 코드 점검 verify.
LAUNCHER_USER_AGENT = f"Aurora-Launcher/{__version__}"

# 본체 .exe 이름 — release.yml 의 Aurora-windows.exe 와 정합
AURORA_EXE_NAME = "Aurora.exe"

# 본체 데이터 격리 폴더 이름 — v0.1.17~v0.1.21 launcher 옆 ``_aurora/``.
# v0.1.22 부터 ``%LOCALAPPDATA%\Aurora\`` (Windows 표준 hidden 위치) 로 이전.
# 본 상수는 (a) legacy migration 시 launcher 옆 ``_aurora/`` 탐색,
# (b) Windows 가 아닌 dev 환경 fallback — 두 용도로만 사용.
AURORA_DATA_DIR = "_aurora"
AURORA_LOCALAPPDATA_NAME = "Aurora"

# 본체에 전달할 env 마커 — 본체 자기-swap 중복 방지
LAUNCHER_ENV_MARKER = "AURORA_FROM_LAUNCHER"
LAUNCHER_PATH_ENV = "AURORA_LAUNCHER_PATH"
"""launcher .exe 절대 경로 (frozen 모드만). 본체가 재시작 요청 시 launcher 다시
spawn 하기 위함. v0.1.43 신규 — UI 업데이트 팝업의 '재시작하기' 버튼 흐름."""

LAUNCHER_AUTO_START_ENV = "AURORA_LAUNCHER_AUTO_START"
"""launcher 가 시작 시 자동으로 START 클릭 (auto-launch 본체). 본체 /relaunch
엔드포인트가 launcher spawn 시 박음. v0.1.43 신규."""


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
    """본체 데이터 격리 폴더 — ``%LOCALAPPDATA%\\Aurora\\`` (v0.1.22).

    이전 (v0.1.17 ~ v0.1.21): ``<launcher_dir>/_aurora/``
    현재 (v0.1.22~):          ``%LOCALAPPDATA%\\Aurora\\``

    Why: launcher.exe 옆에 ``_aurora/`` 폴더가 보이지 않도록 OS 표준 hidden 위치
    (Windows LocalAppData) 로 이동. launcher.exe 만 사용자 눈에 보임.

    플랫폼:
        - Windows: ``%LOCALAPPDATA%\\Aurora`` (= ``C:\\Users\\<user>\\AppData\\Local\\Aurora``)
        - Windows 가 아니거나 ``LOCALAPPDATA`` env 미설정: launcher 옆 ``_aurora/``
          (dev 환경 + 비-Windows fallback)
    """
    if platform.system() == "Windows":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / AURORA_LOCALAPPDATA_NAME
    return _launcher_dir() / AURORA_DATA_DIR


def _aurora_exe_path() -> Path:
    """본체 Aurora.exe 절대 경로 — 격리 폴더 안."""
    return _aurora_data_dir() / AURORA_EXE_NAME


def _migrate_legacy_layout() -> None:
    """legacy layout → 현재 격리 폴더로 자동 이전 (best-effort).

    두 단계 마이그레이션:
        1. v0.1.16 이전: ``<launcher>/Aurora.exe`` → 격리 폴더
        2. v0.1.21 이전: ``<launcher>/_aurora/`` 폴더 통째 → ``%LOCALAPPDATA%\\Aurora\\``

    실패해도 launcher 시작 차단 X (재다운으로 복구 가능).
    """
    new_data_dir = _aurora_data_dir()
    legacy_data_dir = _launcher_dir() / AURORA_DATA_DIR  # <launcher>/_aurora/

    # ── 1단계: legacy ``<launcher>/_aurora/`` 폴더 → 새 위치 (v0.1.22 이전).
    # 새 위치와 legacy 가 같으면 (Windows 아닌 fallback) skip.
    if (
        legacy_data_dir.exists()
        and legacy_data_dir.resolve() != new_data_dir.resolve()
        and not new_data_dir.exists()
    ):
        try:
            new_data_dir.parent.mkdir(parents=True, exist_ok=True)
            new_data_dir.mkdir(parents=True, exist_ok=True)
            for item in legacy_data_dir.iterdir():
                target = new_data_dir / item.name
                try:
                    item.rename(target)
                except OSError as e:
                    logger.warning("legacy 파일 이전 실패 (%s): %s", item.name, e)
            try:
                legacy_data_dir.rmdir()  # 빈 폴더면 정리
            except OSError:
                pass  # 일부 파일 남아 있으면 그냥 둠
            logger.info("legacy %s → %s 이전 완료", legacy_data_dir, new_data_dir)
        except OSError as e:
            logger.warning("legacy data_dir 이전 실패: %s", e)

    # ── 2단계: launcher 옆 잔재 (v0.1.16 이전) → 새 위치.
    legacy_exe = _launcher_dir() / AURORA_EXE_NAME
    new_exe = _aurora_exe_path()
    if legacy_exe.exists() and not new_exe.exists():
        try:
            new_exe.parent.mkdir(parents=True, exist_ok=True)
            legacy_exe.rename(new_exe)
            logger.info("legacy %s → %s 이전 완료", legacy_exe, new_exe)
        except OSError as e:
            logger.warning("legacy exe 이전 실패 (재다운 필요): %s", e)
    # launcher 옆 잔재 (.new / .old / .aurora_version) 정리.
    # v0.1.24: launcher.exe.old 는 swap 직후 unlink 되지만 release 잔재 가능 → 정리.
    # legacy launcher.exe.new 는 apply_pending_launcher_update 가 호환 처리하므로
    # 여기선 unlink X (swap 흐름 방해 방지).
    for name in (
        "Aurora.exe.new", "Aurora.exe.old", ".aurora_version",
        "Aurora-launcher.exe.old",
    ):
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
    """GitHub Releases /latest 호출 — 네트워크 실패 시 None.

    v0.1.59: User-Agent 헤더 필수 (GitHub API 정책) + HTTPError 명시 catch +
    INFO/WARNING 로그 (이전엔 debug silently skip → 사용자 진단 불가).
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": LAUNCHER_USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310
            return json.load(resp)
    except urllib.error.HTTPError as e:
        # 403 (rate limit / User-Agent reject) / 404 등 — 명시 로그
        logger.warning(
            "update check HTTP %d: %s — GitHub API 거부 (User-Agent 또는 rate limit)",
            e.code, e.reason,
        )
        return None
    except urllib.error.URLError as e:
        # DNS / 방화벽 / 연결 차단 — 명시 로그
        logger.warning("update check 네트워크 실패: %s", e.reason)
        return None
    except (json.JSONDecodeError, TimeoutError) as e:
        logger.warning("update check 응답 파싱/타임아웃 실패: %s", e)
        return None


def find_aurora_exe_url(release: dict) -> str | None:
    """release assets 에서 Aurora-windows.exe URL 반환."""
    for asset in release.get("assets", []):
        if asset.get("name") == "Aurora-windows.exe":
            url = asset.get("browser_download_url")
            return str(url) if url else None
    return None


def find_launcher_url(release: dict) -> str | None:
    """release assets 에서 Aurora-launcher.exe URL 반환 (self-update v0.1.19)."""
    for asset in release.get("assets", []):
        if asset.get("name") == "Aurora-launcher.exe":
            url = asset.get("browser_download_url")
            return str(url) if url else None
    return None


def download_to(url: str, target: Path) -> bool:
    """url → target 경로 다운로드. 실패 시 부분 파일 정리.

    v0.1.59: urlretrieve 대신 명시 Request + urlopen — User-Agent 헤더 박음.
    이전 urlretrieve 는 GitHub release CDN 에서 일부 환경 거부 가능성.
    """
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": LAUNCHER_USER_AGENT},
        )
        with (
            urllib.request.urlopen(req) as resp,  # noqa: S310 — User-Agent 박힘
            target.open("wb") as f,
        ):
            shutil.copyfileobj(resp, f)
        return True
    except urllib.error.HTTPError as e:
        logger.warning("download HTTP %d: %s (%s)", e.code, e.reason, url)
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


# ============================================================
# Launcher self-update (v0.1.19) — PR #71 본체 swap race fix 패턴 차용
# ============================================================


def _launcher_exe_path() -> Path:
    """현재 실행 중 Aurora-launcher.exe 경로 (frozen 환경)."""
    return Path(sys.executable).resolve()


def _launcher_new_path() -> Path:
    """다운된 launcher.new 임시 위치 — 격리 폴더 안 (v0.1.24).

    v0.1.23 이전: launcher 옆 ``Aurora-launcher.exe.new``  ← 사용자 눈에 보임
    v0.1.24~:    ``%LOCALAPPDATA%\\Aurora\\Aurora-launcher.exe.new``  ← 숨김
    """
    return _aurora_data_dir() / "Aurora-launcher.exe.new"


def apply_pending_launcher_update() -> bool:
    """직전 다운된 launcher.new 가 있으면 swap → 새 launcher 재시작 (race fix).

    main() 가장 처음 호출. swap 시 race condition 회피를 위해 PR #71 의
    _spawn_clean_env 패턴 차용 (env 정리 + DETACHED + CREATE_BREAKAWAY_FROM_JOB).

    v0.1.24: ``.new`` 가 LocalAppData 격리 폴더에 있을 수도, legacy (launcher 옆) 일 수도.
    호환성 위해 두 위치 모두 체크 — 어느쪽이든 발견 시 swap.

    Returns:
        True (실제로는 도달 X — sys.exit). False — 해당 없음 / 실패.
    """
    if not _is_frozen() or platform.system() != "Windows":
        return False
    exe = _launcher_exe_path()
    new_path = _launcher_new_path()                          # LocalAppData (v0.1.24~)
    legacy_new = exe.with_suffix(exe.suffix + ".new")        # launcher 옆 (v0.1.23 이전 호환)
    old_path = exe.with_suffix(exe.suffix + ".old")

    # v0.1.24 위치 우선, 없으면 legacy 위치 (마이그레이션 호환).
    src: Path | None = None
    if new_path.exists():
        src = new_path
    elif legacy_new.exists():
        src = legacy_new
    if src is None:
        return False

    try:
        if old_path.exists():
            old_path.unlink()
        exe.rename(old_path)
        # ``.new`` 가 다른 볼륨 (LocalAppData = C:, launcher.exe = D: 가능) 일 수 있음 →
        # ``rename()`` 대신 ``shutil.move()`` 사용. 같은 볼륨이면 rename 으로 fast path.
        shutil.move(str(src), str(exe))
        logger.info("launcher self-update applied: %s 재시작", exe.name)

        # 새 launcher 분리 spawn (race fix — PR #71 패턴)
        _DETACHED_PROCESS = 0x00000008  # noqa: N806
        _CREATE_NEW_PROCESS_GROUP = 0x00000200  # noqa: N806
        _CREATE_BREAKAWAY_FROM_JOB = 0x01000000  # noqa: N806
        clean_env = {
            k: v for k, v in os.environ.items()
            if not (k.startswith("_MEI") or k.startswith("_PYI"))
        }
        flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB
        subprocess.Popen(  # noqa: S603 — 자기 재시작
            [str(exe)],
            env=clean_env,
            creationflags=flags,
            close_fds=True,
            cwd=str(exe.parent),
        )
        time.sleep(0.5)  # 새 process _MEI 풀 시간 확보
        sys.exit(0)
    except OSError as e:
        logger.warning("launcher self-update apply 실패: %s", e)
        return False


def _check_and_download_launcher_update() -> None:
    """백그라운드 thread — launcher 자기 update check + 다운로드 (다음 시작 시 apply)."""
    if not _is_frozen() or platform.system() != "Windows":
        return
    release = fetch_latest_release()
    if release is None:
        return
    tag = release.get("tag_name", "")
    try:
        if _parse_version(tag) <= _parse_version(__version__):
            return  # 현재 launcher 가 최신 또는 더 높음
    except (ValueError, TypeError):
        return
    url = find_launcher_url(release)
    if url is None:
        return
    # v0.1.24: launcher 옆 X → 격리 폴더 (LocalAppData) 에 다운로드 (사용자 눈에 안 보임)
    target = _launcher_new_path()
    if target.exists():
        logger.info("launcher %s 이미 다운 완료 — 다음 시작 시 적용", tag)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("launcher %s 발견 → 백그라운드 다운로드 (%s)", tag, target)
    if download_to(url, target):
        logger.info("launcher %s 다운 완료 → 다음 시작 시 자동 swap", tag)


def start_background_launcher_check() -> None:
    """launcher 시작 시 백그라운드 자기 update check thread 띄우기."""
    if not _is_frozen():
        return
    t = threading.Thread(
        target=_check_and_download_launcher_update,
        daemon=True,
        name="launcher-self-updater",
    )
    t.start()


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
    # v0.1.43: launcher .exe 경로 박음 — 본체 /relaunch 가 launcher 다시 spawn 가능.
    # frozen 환경 (sys.executable = launcher.exe) 만 의미. dev 환경은 skip (직접 본체
    # 실행 모드로 안전 fallback).
    if _is_frozen():
        env[LAUNCHER_PATH_ENV] = sys.executable
    # auto-start env 는 launcher 가 본체 spawn 시 절대 박지 않음 — 새 본체가 자기
    # 다시 재시작 명령 무한 루프 위험 차단.
    env.pop(LAUNCHER_AUTO_START_ENV, None)

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

    def __init__(self, *, auto_start: bool = False) -> None:
        # v0.1.43: 본체 /relaunch 가 자식 launcher spawn 시 env 박음.
        # UI 가 ``is_auto_start()`` 로 확인 후 START 자동 클릭.
        self._auto_start = auto_start

    def is_auto_start(self) -> bool:
        """v0.1.43: auto-start 모드 여부 — 본체 재시작 흐름에서 launcher 가
        spawn 됐을 때 True. UI 가 START 자동 클릭 결정에 사용."""
        return self._auto_start

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

    # v0.1.19: 직전 다운된 launcher.new 가 있으면 swap → 새 launcher 재시작.
    # 본 함수는 swap 성공 시 sys.exit(0) — 도달 X.
    apply_pending_launcher_update()

    # v0.1.17: 이전 layout (launcher 옆 Aurora.exe) → _aurora/ 자동 이전.
    _migrate_legacy_layout()

    # v0.1.19: 백그라운드 launcher 자기 update check + 다운 (다음 시작 시 apply).
    start_background_launcher_check()

    # v0.1.43: 본체 /relaunch 흐름 — env 또는 sys.argv 로 auto-start 모드 결정.
    auto_start = (
        os.environ.get(LAUNCHER_AUTO_START_ENV) == "1"
        or "--auto-start" in sys.argv
    )
    api = LauncherApi(auto_start=auto_start)
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
