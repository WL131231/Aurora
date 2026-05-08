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
import ssl
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
# v0.1.66: SSL context — frozen --onefile 환경에서 certifi cacert.pem 명시
# ============================================================
# Why: ChoYoon Claude #133 환기 — 사용자 (huihu) launcher.log 본문
# `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate`.
# PyInstaller frozen 환경에서 ssl 모듈이 Windows system CA store path 깨짐 →
# urllib HTTPS 핸드셰이크 fail.
# v0.1.63 fix 2 (`--collect-data certifi`) 는 `cacert.pem` 을 bundle 에 박지만
# 코드 측 `certifi.where()` 명시 사용 X → ssl 모듈이 인지 X. 본 PR fix 본질.
try:
    import certifi
    _SSL_CONTEXT: ssl.SSLContext = ssl.create_default_context(cafile=certifi.where())
    _SSL_CTX_SOURCE: str = f"certifi {certifi.where()}"
except ImportError:
    # certifi 미설치 (dev 일부 환경) — system default fallback.
    _SSL_CONTEXT = ssl.create_default_context()
    _SSL_CTX_SOURCE = "system default"

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

LAUNCHER_KILL_PARENT_PID_ENV = "AURORA_KILL_PARENT_PID"
"""v0.1.61 신규 — launcher 가 시작 즉시 taskkill /F 할 본체 PID.
본체 /relaunch 엔드포인트가 새 launcher Popen 시 자기 PID 박음. launcher 는
별개 process group 이라 부모-자식 묶임 X → 본체 안 죽는 케이스 무조건 해결.
v0.1.42~v0.1.58 (8회) 본체 자기 죽이기 (os._exit / ExitProcess / cmd taskkill)
모두 일부 환경에서 실패 → 외부 launcher 가 죽이는 게 가장 robust."""

# v0.1.63: GitHub API fetch 마지막 에러 — UI / log 진단용. ChoYoon Claude #133 fix 3.
# fetch_latest_release 가 실패 시 본 변수에 박음. LauncherApi.check_update 가 UI 에 노출.
_last_fetch_error: str | None = None


# ============================================================
# v0.1.93: Launcher single-instance mutex — 중복 실행 차단
# ============================================================
# Why: 사용자 보고 (2026-05-08) — Launcher 가 두 개 실행되는 케이스. v0.1.92 측
# body 만 mutex 박힘 (body 측 중복 차단). launcher 자체 측 mutex 자체 X 라
# self-update 측 spawn-then-exit overlap / 사용자 더블클릭 / 어디 stale process
# 등 시 두 launcher window 동시 표시 가능. body mutex 와 별개 name 박음
# (launcher + body 동시 실행은 정상 흐름 — 같은 mutex 박으면 launcher 살아있는
# 동안 body spawn 차단되는 본질).

_LAUNCHER_MUTEX_HANDLE = None  # noqa: N816 — 모듈 lifetime handle 보유 (GC 방지)
_LAUNCHER_MUTEX_NAME = "Aurora-Launcher-SingleInstance-v0.1.93"
_ERROR_ALREADY_EXISTS = 183


def _acquire_launcher_single_instance_mutex() -> bool:
    """Windows named mutex — Launcher 중복 실행 차단.

    apply_pending_launcher_update() 다음에 박힘 — self-update spawn-then-exit
    측 mutex race 회피 (옛 launcher 가 mutex 잡은 채로 새 launcher spawn 하면
    새 launcher 측 ALREADY_EXISTS 즉시 exit → no launcher 사고).

    Returns:
        ``True`` — primary launcher (mutex 획득). ``False`` — duplicate (exit 권장).
    """
    global _LAUNCHER_MUTEX_HANDLE  # noqa: PLW0603
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, OSError, ImportError):
        return True  # ctypes 미지원 — fallback OK
    _LAUNCHER_MUTEX_HANDLE = kernel32.CreateMutexW(None, True, _LAUNCHER_MUTEX_NAME)
    last_error = kernel32.GetLastError()
    return last_error != _ERROR_ALREADY_EXISTS


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
    """본체 데이터 격리 폴더 — OS 표준 hidden 위치.

    플랫폼:
        - Windows: ``%LOCALAPPDATA%\\Aurora``
        - macOS:   ``~/Library/Application Support/Aurora`` (v0.1.67)
        - Linux 또는 LOCALAPPDATA 미설정: launcher 옆 ``_aurora/`` fallback (dev)

    Why: launcher 옆 폴더가 보이지 않도록. v0.1.67 — macOS 표준 위치 분기 박음
    (이전엔 .app 번들 안 옆 fallback → AppTranslocation 임시 위치 등 위험).
    ChoYoon Claude #133 7번째 cycle fix A.
    """
    sys_name = platform.system()
    if sys_name == "Windows":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / AURORA_LOCALAPPDATA_NAME
    elif sys_name == "Darwin":
        return Path.home() / "Library" / "Application Support" / AURORA_LOCALAPPDATA_NAME
    return _launcher_dir() / AURORA_DATA_DIR


def _body_artifact_name() -> str:
    """v0.1.67: 플랫폼별 GitHub release asset 이름 — release.yml 정합.

    ChoYoon Claude #133 8번째 cycle fix D — macOS launcher 가 Windows .exe 다운로드
    하던 결함 (find_aurora_exe_url Windows hardcoded → Exec format error).

    Linux = release.yml 빌드 X (사용자 영향 0) — CI/dev fallback 으로 Windows asset.
    """
    if platform.system() == "Darwin":
        return "Aurora-macOS.zip"  # release.yml L51 정합
    return "Aurora-windows.exe"  # Windows + Linux fallback


def _body_local_target() -> Path:
    """v0.1.67: 플랫폼별 로컬 본체 path. Linux = Windows fallback (CI/dev 만)."""
    data_dir = _aurora_data_dir()
    if platform.system() == "Darwin":
        return data_dir / "Aurora.app"  # zip 풀어서 박힘
    return data_dir / "Aurora.exe"  # Windows + Linux fallback


def _aurora_exe_path() -> Path:
    """본체 절대 경로 — 격리 폴더 안. 플랫폼별 (.exe / .app).

    v0.1.67: 단순 wrapper — 호출자 호환성 위해 유지. 신규 코드는 _body_local_target() 직접.
    """
    return _body_local_target()


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
    v0.1.63: 실패 시 ``_last_fetch_error`` 전역 변수에 박음 — UI 노출 + 진단.
    """
    global _last_fetch_error  # noqa: PLW0603 — 진단 채널
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": LAUNCHER_USER_AGENT,
            },
        )
        with urllib.request.urlopen(  # noqa: S310 — User-Agent + SSL context 박힘
            req, timeout=HTTP_TIMEOUT_SEC, context=_SSL_CONTEXT,
        ) as resp:
            data = json.load(resp)
        _last_fetch_error = None  # 성공 시 reset
        return data
    except urllib.error.HTTPError as e:
        # 403 (rate limit / User-Agent reject) / 404 등 — 명시 로그
        msg = f"HTTP {e.code} {e.reason}"
        logger.warning(
            "update check %s — GitHub API 거부 (User-Agent / rate limit / proxy)", msg,
        )
        _last_fetch_error = msg
        return None
    except urllib.error.URLError as e:
        # DNS / 방화벽 / SSL / 연결 차단 — 명시 로그
        msg = f"URLError: {e.reason}"
        logger.warning("update check 네트워크 실패: %s", e.reason)
        _last_fetch_error = msg
        return None
    except (json.JSONDecodeError, TimeoutError) as e:
        msg = f"{type(e).__name__}: {e}"
        logger.warning("update check 응답 파싱/타임아웃 실패: %s", msg)
        _last_fetch_error = msg
        return None


def find_aurora_exe_url(release: dict) -> str | None:
    """release assets 에서 본체 URL 반환 — 플랫폼별 asset 이름 (v0.1.67).

    Windows = Aurora-windows.exe / macOS = Aurora-macOS.zip.
    ChoYoon Claude #133 fix D — 이전엔 Windows hardcoded 라 macOS launcher 가
    Windows binary 다운로드 → Exec format error → "본체 .exe 미존재" misleading.
    """
    target_name = _body_artifact_name()
    for asset in release.get("assets", []):
        if asset.get("name") == target_name:
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
    v0.1.66: SSL context (certifi cacert) + timeout 명시 — frozen 환경 SSL 핸드셰이크
    + 무한 hang 방지. ChoYoon Claude #133 fix B.
    """
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": LAUNCHER_USER_AGENT},
        )
        with (
            urllib.request.urlopen(  # noqa: S310 — User-Agent + SSL + timeout 박힘
                req, timeout=HTTP_TIMEOUT_SEC, context=_SSL_CONTEXT,
            ) as resp,
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
    """다운로드된 .new → 본체와 swap. 플랫폼별 흐름 (v0.1.67).

    Windows: ``Aurora.exe.new`` → ``Aurora.exe`` 단순 rename.
    macOS:   ``Aurora-macOS.zip`` 풀어서 ``Aurora.app`` 박음 (zip 본질).

    본체 실행 X 상태라 lock 없음 → 안전 swap.

    ChoYoon Claude #133 fix E — macOS asset 은 zip 형식이라 rename 만으론 부족.
    """
    target = _body_local_target()
    sys_name = platform.system()

    # macOS — zip 풀음 흐름. v0.1.73: suffix 검사 → is_zipfile 박음
    # (download target = ``.zip.new`` 본질 fix H 정합 — suffix=".new" 라 옛 분기
    # SKIP 됐던 결함 회피). zipfile.extractall → ditto -x -k 로 변경 (.app 번들의
    # symlink / xattr / executable bit metadata 보존). ChoYoon #133 fix I.
    if sys_name == "Darwin":
        import zipfile
        if not zipfile.is_zipfile(downloaded_new):
            logger.warning("macOS swap: zip 형식 X — %s", downloaded_new)
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # 기존 .app 제거 (깔끔 swap)
            if target.exists():
                shutil.rmtree(target)
            # ditto -x -k = macOS 표준 zip extract. .app 번들 metadata 보존
            # (symlinks / xattr / resource forks / executable bit). 별도 의존성 X.
            result = subprocess.run(  # noqa: S603, S607
                ["ditto", "-x", "-k", str(downloaded_new), str(target.parent)],
                capture_output=True, timeout=60, check=False,
            )
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")
                logger.error(
                    "ditto -xk 실패 (rc=%d): %s", result.returncode, err.strip(),
                )
                return False
            downloaded_new.unlink()
            # quarantine xattr 정리 — urllib 다운은 quarantine X 박지만 안전망
            subprocess.run(  # noqa: S603, S607
                ["xattr", "-cr", str(target)],
                capture_output=True, timeout=10, check=False,
            )
            # .app 디렉토리 +x — Finder 진입 가능
            try:
                target.chmod(0o755)
            except OSError:
                pass
            logger.info("macOS .app swap 완료 (ditto): %s", target)
            return True
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("macOS swap 실패: %s", e)
            return False

    # Windows / 기타 — rename 흐름 (기존)
    old_path = target.with_suffix(target.suffix + ".old")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if old_path.exists():
            old_path.unlink()
        if target.exists():
            target.rename(old_path)
        downloaded_new.rename(target)
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


def _kill_existing_aurora_on_port(port: int = 8765) -> None:
    """v0.1.64: 옛 본체가 API 포트 점유 중이면 taskkill.

    Why: 사용자 제안 — 재시작 = launcher GUI 로 돌아가는 흐름. 본체 자기 죽이기
    의무 X (v0.1.42~v0.1.61 모두 일부 환경 fail). launcher 가 새 본체 spawn 직전
    port 점유 검사 → 점유 PID kill → 새 본체 spawn. 무조건 동작.

    Windows netstat -ano 로 PID 찾기. listening state 만 본체로 간주.
    """
    if platform.system() != "Windows":
        return
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["netstat", "-ano"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return
        port_marker = f":{port} "
        # cp949 / utf-8 fallback (Windows netstat 환경 의존)
        text = result.stdout.decode("cp949", errors="replace")
        my_pid = os.getpid()
        for line in text.splitlines():
            if port_marker not in line or "LISTENING" not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid == my_pid:
                continue
            logger.info("옛 본체 발견 (port %d 점유): PID=%d → taskkill /F", port, pid)
            try:
                kill_result = subprocess.run(  # noqa: S603, S607
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if kill_result.returncode == 0:
                    logger.info("옛 본체 kill OK: PID=%d", pid)
                    # listening 점유 해제 시간 잠깐 대기 (TIME_WAIT 등)
                    time.sleep(0.5)
                else:
                    logger.warning(
                        "옛 본체 kill returncode=%d", kill_result.returncode,
                    )
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning("옛 본체 kill 실패: %s", e)
            return  # 1 PID 만 처리 (보통 1 listener)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("port %d 점유 검사 실패: %s", port, e)


def launch_aurora() -> subprocess.Popen | None:
    """본체 실행 — env 마커 전달로 본체 자기-swap 중복 방지. 플랫폼별 흐름 (v0.1.67).

    Windows: ``Aurora.exe`` 직접 Popen (DETACHED_PROCESS).
    macOS:   ``open Aurora.app`` 명령 (Finder 등록된 앱처럼 실행).

    v0.1.80: Popen 객체 반환 (이전 bool) — LauncherApi 측 본체 process 보관 →
    polling 으로 본체 종료 감지 → launcher webview show 본질. 사용자 제안 패러다임:
    "launcher 가 항상 살아있음 + 본체 spawn 시 hide + 본체 종료 시 show" 본질.

    macOS = `open` 명령 자체가 detach 라 Popen 객체 반환해도 실제 본체 PID X.
    Windows = DETACHED_PROCESS Popen 의 PID 보관 가능 — polling 정합.

    Returns:
        Popen 객체 (Windows 정상 / macOS = open wrapper Popen) /
        None (본체 미존재 또는 실패).
    """
    target = _body_local_target()
    if not target.exists():
        logger.error("본체 미존재: %s", target)
        return None

    # v0.1.64: 옛 본체 (port 점유) 자동 정리 — 사용자 시각 "재시작 = launcher GUI"
    # 흐름. 본체 자기 죽이기 의무 X. 본체 /relaunch 가 launcher 새로 spawn 시
    # KILL_PARENT_PID 도 박지만, 그게 fail 해도 본 단계가 안전망.
    _kill_existing_aurora_on_port()

    env = os.environ.copy()
    env[LAUNCHER_ENV_MARKER] = "1"
    # v0.1.43: launcher 경로 박음 — 본체 /relaunch 가 launcher 다시 spawn 가능.
    # frozen 환경 (sys.executable = launcher.exe) 만 의미. dev 환경은 skip.
    if _is_frozen():
        env[LAUNCHER_PATH_ENV] = sys.executable
    # auto-start env 는 launcher 가 본체 spawn 시 절대 박지 않음 — 새 본체가 자기
    # 다시 재시작 명령 무한 루프 위험 차단.
    env.pop(LAUNCHER_AUTO_START_ENV, None)

    sys_name = platform.system()
    try:
        if sys_name == "Darwin":
            # macOS: `open` 명령 — .app 번들 표준 실행 흐름. Info.plist 처리 + Finder
            # 표준 lifecycle 정합. cwd 박음 X (open 이 .app/Contents/MacOS/ 자동).
            # v0.1.73: stderr 캡처 + 짧은 timeout. ChoYoon #133 fix J — 본체 시작
            # 흐름 진단 자료 박음. open 자체는 비동기 spawn → 정상 = 즉시 종료.
            # fail 시 open 이 stderr 메시지 박음 (e.g. "이 응용 프로그램은 이 Mac
            # 에서 지원되지 않습니다") → launcher.log 측 가시화.
            logger.info("본체 실행 시도 (open .app): %s", target)
            proc = subprocess.Popen(  # noqa: S603, S607 — open 명령 + .app path
                ["open", str(target)],
                env=env,
                close_fds=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr = proc.communicate(timeout=2.0)
                if proc.returncode != 0:
                    err = stderr.decode("utf-8", errors="replace").strip()
                    logger.error(
                        "open 명령 fail (rc=%d): %s", proc.returncode, err,
                    )
                    return None
                logger.info("open 명령 성공 (rc=0)")
            except subprocess.TimeoutExpired:
                # 정상 — open 이 .app 비동기 spawn 후 자체 종료 (보통 빠름)
                logger.info("본체 시작 명령 박힘 (비동기 detach)")
            return proc

        # Windows / 기타 — 직접 Popen (DETACHED_PROCESS)
        DETACHED_PROCESS = 0x00000008  # noqa: N806
        CREATE_NEW_PROCESS_GROUP = 0x00000200  # noqa: N806
        flags = (
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            if sys_name == "Windows"
            else 0
        )
        proc = subprocess.Popen(  # noqa: S603 — 본체 실행, 신뢰 가능
            [str(target)],
            env=env,
            creationflags=flags,
            close_fds=True,
            # v0.1.17: cwd = launcher 옆. 본체가 .env / config_store 등을 launcher
            # 옆에서 찾게 → 사용자가 .env 를 launcher 옆에 두면 인식 OK.
            cwd=str(_launcher_dir()),
        )
        return proc
    except OSError as e:
        logger.error("본체 실행 실패: %s", e)
        return None


# ============================================================
# Pywebview API — JS bridge
# ============================================================


class LauncherApi:
    """Pywebview JS bridge — UI 가 호출하는 백엔드 메서드."""

    def __init__(self, *, auto_start: bool = False) -> None:
        # v0.1.43: 본체 /relaunch 가 자식 launcher spawn 시 env 박음.
        # UI 가 ``is_auto_start()`` 로 확인 후 START 자동 클릭.
        self._auto_start = auto_start
        # v0.1.80: 본체 process 보관 + polling thread 측 본체 종료 감지.
        # 사용자 제안 패러다임 — launcher 항상 살아있음 + 본체 spawn 시 hide
        # + 본체 종료 시 show. 본체 자기 죽이기 의무 자체 X.
        self._aurora_proc: subprocess.Popen | None = None

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
            # v0.1.63: 일반 메시지 + 마지막 fetch 에러 (HTTP 코드 / URLError reason 등)
            # 자세히 노출 → 사용자 진단 가능. ChoYoon Claude #133 fix 3.
            detail = f" [{_last_fetch_error}]" if _last_fetch_error else ""
            return {
                "latest": None, "has_update": False, "url": None,
                "error": f"GitHub Releases 조회 실패{detail}",
            }
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
        """본체 다운로드 + swap. 플랫폼별 download target (v0.1.73).

        Windows: ``Aurora.exe.new`` (rename swap)
        macOS:   ``Aurora-macOS.zip.new`` (zip 형식 보존 — apply_swap 가 ditto 풀음)

        Why: ChoYoon Claude #133 10th cycle fix H — 이전 흐름은 macOS 측에서
        ``Aurora.app.new`` (단일 파일 path) 박음 → apply_swap 의 .zip 분기 SKIP
        → Windows rename 흐름 fall-through → ``Aurora.app`` 가 zip 바이트 단일
        파일로 박힘 (디렉토리 X, 번들 X) → macOS "지원되지 않음" 에러.

        Returns:
            ``{"success": bool, "message": str}``
        """
        if platform.system() == "Darwin":
            # macOS: download target = .zip.new — apply_swap 가 zip 풀어서 .app 박음.
            target = _aurora_data_dir() / "Aurora-macOS.zip.new"
        else:
            target = _aurora_exe_path().with_suffix(_aurora_exe_path().suffix + ".new")
        # 폴더 자동 생성 (첫 다운 시)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not download_to(url, target):
            return {"success": False, "message": "다운로드 실패 (네트워크 확인)"}
        if not apply_swap(target):
            return {"success": False, "message": "swap 실패 (본체 권한 확인)"}
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
        """본체 실행 — v0.1.80 사용자 제안 패러다임:
        launcher 항상 살아있음 + 본체 spawn 시 webview hide + 본체 종료 시 show.
        본체 자기 죽이기 / launcher 가 본체 죽이기 의무 자체 X.
        """
        proc = launch_aurora()
        if proc is None:
            return {"success": False, "message": "본체 .exe 미존재 — 먼저 업데이트"}
        # 본체 process 보관 + polling thread 시작
        self._aurora_proc = proc
        self._start_aurora_polling()
        # launcher webview hide — 본체 GUI 가 사용자 화면 차지
        self._hide_launcher_window()
        return {"success": True, "message": "Aurora 시작됨"}

    def _hide_launcher_window(self) -> None:
        """v0.1.80: launcher webview hide — 백그라운드 살아있음."""
        try:
            import webview  # type: ignore[import-not-found]
            if webview.windows:
                webview.windows[0].hide()
                logger.info("launcher webview hide — 본체 GUI 차지")
        except Exception as e:  # noqa: BLE001
            logger.warning("launcher hide 실패: %s", e)

    def _show_launcher_window(self) -> None:
        """v0.1.80: launcher webview show — 본체 종료 후 사용자 화면 등장."""
        try:
            import webview  # type: ignore[import-not-found]
            if webview.windows:
                webview.windows[0].show()
                logger.info("launcher webview show — 본체 종료 감지 + 등장")
        except Exception as e:  # noqa: BLE001
            logger.warning("launcher show 실패: %s", e)

    def _start_aurora_polling(self) -> None:
        """v0.1.80: 본체 process 종료 감지 polling thread.
        v0.1.83: marker file 검사 추가 — 본체 측 /relaunch 시 marker 박음
        → launcher 가 본체 process.terminate() 호출 (본체 자체 종료 의무 X).
        v0.1.92: marker 발동 종료면 새 본체 자동 spawn — 사용자 시각 \"재시작\"
        한 번 클릭 흐름 (이전 v0.1.83 측 사용자가 START 다시 클릭 본질 X).

        흐름:
            - 본체 정상 종료 (X 클릭 / crash) → launcher show + START 활성
            - 본체 marker 종료 (재시작 요청) → 새 본체 자동 spawn (launcher 그대로 hide)
        """
        marker_path = _aurora_data_dir() / ".relaunch_request"

        def _poll() -> None:
            while True:
                proc = self._aurora_proc
                if proc is None:
                    break
                # v0.1.83: marker 검사 — 본체 측 /relaunch 호출 시 박음
                triggered_by_marker = False
                if marker_path.exists():
                    logger.info(
                        "marker 발견 (%s) → 본체 process.terminate() 호출",
                        marker_path,
                    )
                    try:
                        proc.terminate()
                        # 짧은 대기 후 안 죽으면 kill
                        try:
                            proc.wait(timeout=3.0)
                        except subprocess.TimeoutExpired:
                            logger.warning("terminate 후도 살아있음 → kill")
                            proc.kill()
                    except OSError as e:
                        logger.warning("본체 종료 실패: %s", e)
                    # marker 정리
                    try:
                        marker_path.unlink()
                    except OSError:
                        pass
                    triggered_by_marker = True
                rc = proc.poll()
                if rc is not None:
                    # v0.1.92: marker 발동 종료 = 재시작 요청 → 새 본체 자동 spawn
                    if triggered_by_marker:
                        logger.info(
                            "재시작 요청 (marker) — 새 본체 자동 spawn (rc=%s)", rc,
                        )
                        new_proc = launch_aurora()
                        if new_proc is not None:
                            self._aurora_proc = new_proc
                            logger.info("새 본체 spawn OK — polling 계속")
                            continue  # 새 process 측 polling 계속
                        logger.warning(
                            "새 본체 spawn 실패 — launcher show fallback",
                        )
                    # 본체 정상 종료 (사용자 X 클릭 / crash) 또는 auto-spawn 실패
                    logger.info(
                        "본체 종료 감지 (rc=%s) → launcher show + START 활성", rc,
                    )
                    self._aurora_proc = None
                    self._show_launcher_window()
                    # JS 측 START 버튼 활성 — webview eval
                    try:
                        import webview  # type: ignore[import-not-found]
                        if webview.windows:
                            webview.windows[0].evaluate_js(
                                "document.getElementById('btn-start').disabled = false;"
                                "if (window.setStatus) setStatus('본체 종료됨 — 다시 시작 가능', 'var(--text-2)');",
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.debug("UI START 활성 fail: %s", e)
                    break
                time.sleep(2.0)
        threading.Thread(target=_poll, daemon=True).start()

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


def _setup_file_logging() -> Path | None:
    """v0.1.63: launcher 진단용 file logging — `%LOCALAPPDATA%\\Aurora\\launcher.log`.

    ChoYoon Claude #133 환기 — frozen `--windowed` 빌드는 콘솔 X → stderr 사라짐
    → ``logger.warning`` 출력 위치 X → 사용자 측 root cause 진단 불가.
    file handler 박아두면 manual 다운로드 후 사용자 측 로그 파일에 자동 기록 →
    다음 cycle 정확한 진단 가능.

    Returns:
        log 파일 절대 경로 (성공 시) / None (디렉토리 권한 등 실패 시).
    """
    import logging.handlers

    try:
        log_dir = _aurora_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "launcher.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=1_000_000,  # 1 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        ))
        # root logger 에 박음 — launcher 내부 logger.* 모두 포함.
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        # 이미 file handler 박혔으면 중복 X (자가-update swap 시 main 재진입)
        for existing in list(root_logger.handlers):
            if isinstance(existing, logging.handlers.RotatingFileHandler):
                root_logger.removeHandler(existing)
        root_logger.addHandler(file_handler)
        return log_file
    except OSError:
        return None


def _log_environment_info() -> None:
    """v0.1.63: 시스템 정보 로깅 — proxy / Python / platform / frozen 등.

    사용자 측 root cause (proxy / SSL / certifi) 진단 단서. ChoYoon Claude
    #133 fix 4.
    """
    import platform as _plat

    logger.info("=" * 60)
    logger.info("Aurora-Launcher v%s 시작", __version__)
    logger.info("Python %s", sys.version.replace("\n", " "))
    logger.info("Platform: %s", _plat.platform())
    logger.info("sys.frozen=%s _MEIPASS=%s",
                getattr(sys, "frozen", False),
                getattr(sys, "_MEIPASS", None))
    logger.info("LOCALAPPDATA=%s", os.environ.get("LOCALAPPDATA"))
    logger.info(
        "HTTPS_PROXY=%s HTTP_PROXY=%s",
        os.environ.get("HTTPS_PROXY") or "(unset)",
        os.environ.get("HTTP_PROXY") or "(unset)",
    )
    # v0.1.66: SSL context 출처 — ChoYoon Claude #133 fix C.
    # 다음 cycle launcher.log 에서 "certifi C:\...\_MEI...\certifi\cacert.pem" 박힘
    # → frozen 환경 SSL 핸드셰이크 본질 정합 verify 가능.
    logger.info("SSL context: %s", _SSL_CTX_SOURCE)
    logger.info("=" * 60)


def _kill_parent_if_requested() -> None:
    """v0.1.61: 본체 /relaunch → launcher Popen 시 박은 부모 PID 강제 종료.

    Why: v0.1.42~v0.1.58 본체 자기 죽이기 (os._exit / ExitProcess / cmd
    watchdog) 모두 일부 사용자 환경에서 실패 (PyInstaller frozen + uvicorn +
    threading + webview 복합 hold). launcher 는 별개 process group 이라 부모
    묶임 X → taskkill /F /T 무조건 동작.

    환경변수 ``AURORA_KILL_PARENT_PID`` 있으면 그 PID 강제 종료. 없으면 noop
    (일반 launcher 시작 흐름).
    """
    kill_pid_str = os.environ.get(LAUNCHER_KILL_PARENT_PID_ENV)
    if not kill_pid_str:
        return
    try:
        kill_pid = int(kill_pid_str)
    except ValueError:
        logger.warning("KILL_PARENT_PID env 잘못됨: %s", kill_pid_str)
        return
    logger.info("부모 본체 강제 종료 시도: PID=%d", kill_pid)
    try:
        # /F = 강제, /T = 자식 트리도. Windows 만 (launcher = Windows only).
        result = subprocess.run(  # noqa: S603, S607
            ["taskkill", "/F", "/T", "/PID", str(kill_pid)],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            logger.info("부모 본체 종료 OK: PID=%d", kill_pid)
        else:
            logger.warning(
                "부모 본체 종료 returncode=%d stderr=%s",
                result.returncode, result.stderr.decode("utf-8", errors="replace"),
            )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("부모 본체 종료 실패: %s", e)
    # env 정리 — 다음 launcher self-update swap 시 본 env 잔재 X
    os.environ.pop(LAUNCHER_KILL_PARENT_PID_ENV, None)


def main() -> None:
    """launcher 진입점 — pywebview 윈도우 시작."""
    import webview  # type: ignore[import-not-found]

    # v0.1.63: file logging 박기 가장 우선 — 모든 후속 단계 로그 보존.
    # ChoYoon Claude #133 fix 1 (가장 시급). frozen --windowed 환경 stderr 사라짐
    # 진단 차단 메타 issue 해소 — 사용자 측 manual 다운로드 후 launcher.log 박힘.
    log_file = _setup_file_logging()
    _log_environment_info()
    if log_file is not None:
        logger.info("file logging 활성: %s", log_file)
    else:
        logger.warning("file logging 활성 실패 (디렉토리 권한 등)")

    # v0.1.61: 본체 /relaunch 흐름 — 부모 본체 PID 받아 강제 종료 (자기-launcher 호출).
    # 다른 단계 (apply_pending_launcher_update / migrate / update check) 보다 우선 —
    # 부모 살아있으면 새 본체 spawn 시 포트 충돌 등 위험.
    _kill_parent_if_requested()

    # v0.1.19: 직전 다운된 launcher.new 가 있으면 swap → 새 launcher 재시작.
    # 본 함수는 swap 성공 시 sys.exit(0) — 도달 X.
    apply_pending_launcher_update()

    # v0.1.93: launcher 측 single-instance mutex — 사용자 보고 (2026-05-08)
    # "런처가 2개 실행" 본질. apply_pending_launcher_update() 다음에 박힘 —
    # self-update 측 spawn-then-exit 흐름 측 mutex race 회피.
    if not _acquire_launcher_single_instance_mutex():
        logger.warning(
            "Launcher 이미 실행 중 (Windows named mutex %s) — 중복 실행 차단",
            _LAUNCHER_MUTEX_NAME,
        )
        sys.exit(0)

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
