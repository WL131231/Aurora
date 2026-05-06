"""Aurora Launcher 검정 다이아몬드 .ico — 본체와 구분되는 모노크롬 버전.

본체 (assets/aurora.ico) = purple→indigo→cyan 그라디언트
런처 (assets/aurora-launcher.ico) = 검정 다이아몬드 (사용자 요청 v0.1.14)
    - 외곽 stroke 만 흐릿한 회색
    - 내부 다이아몬드 검정 fill

사용법:
    python scripts/generate_launcher_icon.py
    → assets/aurora-launcher.ico  (16/32/48/64/128/256 멀티)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
ICO_PATH = ASSETS_DIR / "aurora-launcher.ico"
ICNS_PATH = ASSETS_DIR / "aurora-launcher.icns"   # v0.1.69: macOS 아이콘
PNG_PATH = ASSETS_DIR / "aurora-launcher.png"     # v0.1.69: Linux 아이콘

# 검정 모노크롬 — 외곽은 회색, 내부는 검정
STROKE_COLOR = (90, 90, 100)        # 외곽 회색 (구분 가능)
FILL_COLOR = (15, 15, 20)           # 내부 검정 (작업표시줄 검정 배경에서 살짝 보임)


def render_diamond(size: int = 256) -> Image.Image:
    """검정 다이아몬드 PNG (RGBA) 렌더링."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = img.load()
    if pixels is None:
        return img

    cx = cy = size // 2
    margin = max(2, size // 32)
    half = size // 2 - margin
    inner_half = half // 2
    stroke_w = max(1, size // 64)

    for y in range(size):
        for x in range(size):
            d = abs(x - cx) + abs(y - cy)
            if d > half:
                continue
            if d > half - stroke_w:
                pixels[x, y] = (*STROKE_COLOR, 255)
            elif d <= inner_half:
                pixels[x, y] = (*FILL_COLOR, 230)
            else:
                # 외곽~내부 사이 반투명 검정 (그림자 효과)
                pixels[x, y] = (*FILL_COLOR, 60)
    return img


def main() -> None:
    """v0.1.69: ICO (Windows) + ICNS (macOS) + PNG (Linux) 동시 생성.

    ChoYoon Claude #133 P2 본질 (사용자 huihu 제안 2 — 아이콘 박음).
    """
    ASSETS_DIR.mkdir(exist_ok=True)
    # 큰 base (1024) 박아 ICNS 의 1024x1024 사이즈 까지 커버.
    base = render_diamond(1024)

    # Windows .ico — 멀티 사이즈
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(ICO_PATH, format="ICO", sizes=ico_sizes)
    print(f"생성 완료: {ICO_PATH} ({len(ico_sizes)} 사이즈 멀티 ICO)")

    # macOS .icns — Pillow native ICNS (Pillow >= 9.5 박힘)
    try:
        base.save(ICNS_PATH, format="ICNS")
        print(f"생성 완료: {ICNS_PATH}")
    except (OSError, ValueError) as e:
        print(f"[warn] ICNS 생성 skip: {e}")

    # Linux .png — 256x256 단일
    base.resize((256, 256)).save(PNG_PATH, format="PNG")
    print(f"생성 완료: {PNG_PATH}")


if __name__ == "__main__":
    main()
