"""aiohttp Android stub wheel 재생성 스크립트.

python-telegram-bot 이 WSMsgType 을 import 하므로 stub 에 정의 필요.
실행: python scripts/build_aiohttp_stub.py
"""

import hashlib
import io
import zipfile
from pathlib import Path

PKG = "aiohttp"
VERSION = "3.13.5"
OUT = Path(__file__).resolve().parents[1] / "android/app/stubs" / f"{PKG}-{VERSION}-py3-none-any.whl"

INIT_PY = '''\
# aiohttp Android stub — WSMsgType + 최소 no-op 구현
# Why: python-telegram-bot 이 aiohttp.WSMsgType 을 import.
#      Chaquopy ARM64 wheel 없어 C 확장 빌드 불가 → 더미로 의존성 해결.
from enum import IntEnum


class WSMsgType(IntEnum):
    """WebSocket 메시지 타입 — python-telegram-bot BotInstance 경고 해소용."""
    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA
    CLOSING = 256
    CLOSED = 257
    ERROR = 258
'''

METADATA = f"""\
Metadata-Version: 2.1
Name: {PKG}
Version: {VERSION}
Summary: aiohttp Android stub (no C extensions)
"""

WHEEL_META = """\
Wheel-Version: 1.0
Generator: aurora-stub-builder
Root-Is-Purelib: true
Tag: py3-none-any
"""


def sha256_b64(data: bytes) -> str:
    import base64
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def build() -> None:
    buf = io.BytesIO()
    files: list[tuple[str, bytes]] = [
        (f"{PKG}/__init__.py", INIT_PY.encode()),
        (f"{PKG}-{VERSION}.dist-info/METADATA", METADATA.encode()),
        (f"{PKG}-{VERSION}.dist-info/WHEEL", WHEEL_META.encode()),
    ]

    record_lines = []
    for name, data in files:
        record_lines.append(f"{name},{sha256_b64(data)},{len(data)}")
    record_lines.append(f"{PKG}-{VERSION}.dist-info/RECORD,,")
    record_data = "\n".join(record_lines).encode()

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
        zf.writestr(f"{PKG}-{VERSION}.dist-info/RECORD", record_data)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(buf.getvalue())
    print(f"생성 완료: {OUT}  ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
