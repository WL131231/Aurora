"""Android APK 자동 업데이트 모듈 (Phase C-2).

흐름:
    1. aurora_bridge.start() 가 _load_env() 직후 apk_updater.start() 호출
    2. 백그라운드 스레드 — 시작 시 1회 + 12시간 주기로 GitHub Releases 체크
    3. 새 버전 APK 발견 → AURORA_DATA_DIR/update/Aurora-android.apk 다운로드
    4. api.py 의 GET /update/apk-status 가 상태 노출
    5. UI "재시작하기" 클릭 → window.Android.installApk(path) 호출 (app.js)
    6. MainActivity.UpdateBridge 가 FileProvider Intent 로 시스템 설치 다이얼로그 기동

AURORA_PLATFORM != android 환경 (데스크탑 / CI) 에서는 start() 가 noop.

담당: 정용우
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from aurora import __version__
from aurora.interfaces import release_check

logger = logging.getLogger(__name__)

APK_ASSET_NAME = "Aurora-android.apk"
CHECK_INTERVAL_SEC = 12 * 3600  # 12시간 주기 (배터리·데이터 절약)

_state: dict = {
    "has_update": False,
    "apk_path": None,    # str | None — 다운 완료된 APK 절대 경로
    "latest_tag": None,  # str | None — 다운된 버전 태그 (예: "v0.1.58")
}


def get_status() -> dict:
    """현재 APK 업데이트 상태 반환 — api.py /update/apk-status 에서 사용."""
    return dict(_state)


def _apk_dir() -> Path:
    """APK 다운로드 디렉토리 — AURORA_DATA_DIR/update/."""
    data_dir = os.environ.get("AURORA_DATA_DIR", "")
    return Path(data_dir) / "update" if data_dir else Path("/tmp/aurora_update")  # noqa: S108


def _check_and_download() -> None:
    """GitHub 최신 릴리스 체크 + APK 다운로드.

    새 버전 없음 또는 네트워크 실패 시 조용히 skip.
    """
    release = release_check.fetch_latest()
    if release is None:
        return
    tag = release.get("tag_name", "")
    if not tag:
        return

    # 버전 비교 — 현재 번들 버전보다 낮거나 같으면 skip
    try:
        if release_check._parse_version(tag) <= release_check._parse_version(__version__):
            return
    except (ValueError, TypeError):
        return

    # 릴리스 assets 에서 APK URL 탐색
    apk_url: str | None = None
    for asset in release.get("assets", []):
        if asset.get("name") == APK_ASSET_NAME:
            apk_url = asset.get("browser_download_url")
            break
    if not apk_url:
        logger.debug("릴리스 %s 에 %s 없음 — skip", tag, APK_ASSET_NAME)
        return

    # 이미 같은 태그로 다운 완료된 파일이면 state 만 갱신하고 skip
    apk_dir = _apk_dir()
    apk_path = apk_dir / APK_ASSET_NAME
    tag_file = apk_dir / f"{APK_ASSET_NAME}.tag"
    if apk_path.exists() and tag_file.exists():
        try:
            if tag_file.read_text(encoding="utf-8").strip() == tag:
                _state.update(has_update=True, apk_path=str(apk_path), latest_tag=tag)
                logger.info("APK %s 이미 다운 완료 — 재다운 skip", tag)
                return
        except OSError:
            pass

    # 다운로드 (임시 .tmp 파일 → 완료 후 rename, 중단 시 .tmp 정리)
    apk_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = apk_path.with_suffix(".tmp")
    logger.info("APK %s 다운로드 시작...", tag)
    try:
        urllib.request.urlretrieve(apk_url, str(tmp_path))  # noqa: S310
        tmp_path.rename(apk_path)
        tag_file.write_text(tag, encoding="utf-8")
        _state.update(has_update=True, apk_path=str(apk_path), latest_tag=tag)
        logger.info("APK %s 다운로드 완료: %s", tag, apk_path)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.warning("APK 다운로드 실패: %s", e)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _update_loop() -> None:
    """시작 시 1회 즉시 체크 + 12시간 주기 반복."""
    _check_and_download()
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        _check_and_download()


def start() -> None:
    """백그라운드 APK 업데이트 스레드 시작.

    Args:
        없음. AURORA_DATA_DIR 은 aurora_bridge._load_env() 이후 주입 완료 상태.

    AURORA_PLATFORM != android 환경 (데스크탑 / CI / 테스트) 에서는 noop.
    """
    if os.environ.get("AURORA_PLATFORM") != "android":
        return
    t = threading.Thread(target=_update_loop, daemon=True, name="apk-updater")
    t.start()
    logger.info("APK 업데이트 폴링 시작 (12시간 주기)")
