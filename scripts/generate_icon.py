"""Aurora 다이아몬드 로고 .ico 파일 생성 — Pillow 만 사용 (외부 SVG 도구 X).

다이아몬드 모양 (네 꼭짓점 정사각형 회전) + purple → indigo → cyan 그라디언트.
``website/favicon.svg`` 와 동일 디자인을 PIL 로 픽셀 단위 렌더링.

사용법:
    python scripts/generate_icon.py
    → assets/aurora.ico  (16/32/48/64/128/256 멀티 사이즈)

산출 후엔 자주 재생성할 일 없음. ``build_exe.py`` 가 ``--icon`` 으로 참조.

담당: 정용우
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
ICO_PATH = ASSETS_DIR / "aurora.ico"

# 그라디언트 색 (웹사이트 logoGrad 와 동일)
COLOR_START = (168, 85, 247)   # #a855f7 purple
COLOR_MID = (99, 102, 241)     # #6366f1 indigo
COLOR_END = (34, 211, 238)     # #22d3ee cyan


def _interp(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """선형 보간."""
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _gradient_color(t: float) -> tuple[int, int, int]:
    """t ∈ [0, 1] 위치의 3단 그라디언트 색."""
    if t <= 0.5:
        return _interp(COLOR_START, COLOR_MID, t * 2)
    return _interp(COLOR_MID, COLOR_END, (t - 0.5) * 2)


def render_diamond(size: int = 256) -> Image.Image:
    """다이아몬드 + 그라디언트 PNG (RGBA) 렌더링.

    - 외곽 stroke (얇은 다이아몬드 테두리)
    - 내부 작은 다이아몬드 (반투명 fill)
    """
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
            d = abs(x - cx) + abs(y - cy)  # 다이아몬드 거리(L1)
            if d > half:
                continue

            # 그라디언트 위치 (좌상단=0, 우하단=1)
            t = ((x + y) / (2.0 * (size - 1)))
            r, g, b = _gradient_color(t)

            if d > half - stroke_w:
                # 외곽 테두리
                pixels[x, y] = (r, g, b, 255)
            elif d <= inner_half:
                # 내부 작은 다이아몬드 (반투명)
                pixels[x, y] = (r, g, b, 153)

    return img


def main() -> None:
    ASSETS_DIR.mkdir(exist_ok=True)
    base = render_diamond(256)
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(ICO_PATH, format="ICO", sizes=sizes)
    print(f"생성 완료: {ICO_PATH} ({len(sizes)} 사이즈 멀티 ICO)")


if __name__ == "__main__":
    main()
