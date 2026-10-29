"""Stacked waveform marks for Liner.

Every layer reads the same tone, shifted a little in time, and is drawn over the layers
behind it, so each crest hides what sits behind it. Back layers are dimmer, which gives
the stack depth. Options: `perspective` makes the front layers swing wider than the back
ones; `morph` turns a pure sine on the left into the full tone on the right; `bridge`
draws an arc across the back trace between two of its crests, with a node at each end.

Without arguments, writes every candidate and a preview page next to this script.
With `--publish NAME --logo-dir PATH`, writes that candidate as the project's mark and
both lockups.
"""

import argparse
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
BG = "#151C30"
FG = "#EEF1FF"
ACCENT = "#FFB547"
INK = "#151C30"
FONT = "'Segoe UI', Inter, Helvetica, Arial, sans-serif"
LEFT, RIGHT = 22.0, 106.0
FILL_BOTTOM = 130.0
SAMPLES = 120
TEXT_X = 150

# (multiple of the base frequency, amplitude, phase in radians)
HARMONICS = [(1, 1.00, 0.4), (2, 0.52, 1.3), (3, 0.28, 2.1), (5, 0.14, 0.6), (7, 0.07, 1.9)]
PEAK = sum(amplitude for _, amplitude, _ in HARMONICS)

VARIANTS = {
    "stack-13": dict(count=13, top=40.0, bottom=96.0, height=24.0, stroke=1.2, shift=0.035, accent_front=False),
    "depth-15-bridge": dict(
        count=15,
        top=36.0,
        bottom=86.0,
        height=22.0,
        stroke=1.2,
        shift=0.035,
        accent_front=False,
        perspective=0.4,
        bridge=True,
    ),
    "morph-15": dict(
        count=15,
        top=36.0,
        bottom=86.0,
        height=22.0,
        stroke=1.2,
        shift=0.035,
        accent_front=False,
        perspective=0.4,
        morph=True,
    ),
    "morph-15-bridge": dict(
        count=15,
        top=36.0,
        bottom=86.0,
        height=22.0,
        stroke=1.2,
        shift=0.035,
        accent_front=False,
        perspective=0.4,
        morph=True,
        bridge=True,
    ),
    "stack-21": dict(count=21, top=34.0, bottom=100.0, height=16.0, stroke=0.9, shift=0.02, accent_front=False),
    "stack-9-accent": dict(count=9, top=42.0, bottom=98.0, height=30.0, stroke=1.8, shift=0.06, accent_front=True),
}
PREVIEW = ["stack-13", "depth-15-bridge", "morph-15", "morph-15-bridge"]


def signal(t: float) -> float:
    """One fixed, slightly irregular tone, scaled to run from -1 to 1."""
    return sum(a * math.sin(2 * math.pi * f * t + p) for f, a, p in HARMONICS) / PEAK


def envelope(x: float) -> float:
    """Flattens the trace at both ends of the tile."""
    return math.sin(math.pi * x) ** 0.7


def tone(x: float, t: float, morph: bool) -> float:
    """The signal at phase t. With `morph`, a pure sine on the left turns into the full tone on the right."""
    if not morph:
        return signal(t)
    complexity = x**1.3
    return (1 - complexity) * math.sin(2 * math.pi * t) + complexity * signal(t)


def trace(
    layer: int,
    baseline: float,
    height: float,
    shift: float,
    morph: bool = False,
) -> list[tuple[float, float]]:
    points = []
    for k in range(SAMPLES + 1):
        x = k / SAMPLES
        y = baseline - height * envelope(x) * tone(x, x + layer * shift, morph)
        points.append((LEFT + x * (RIGHT - LEFT), y))
    return points


def crest(points: list[tuple[float, float]], lo: float, hi: float) -> tuple[float, float]:
    """The highest point of a trace between two x positions."""
    return min((p for p in points if lo <= p[0] <= hi), key=lambda p: p[1])


def _coords(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _bridge(top: float, height: float, shift: float, morph: bool) -> list[str]:
    """An arc across the back trace, joining two of its crests with a node at each end."""
    back = trace(0, top, height, shift, morph)
    span = RIGHT - LEFT
    a = crest(back, LEFT + 0.18 * span, LEFT + 0.42 * span)
    b = crest(back, LEFT + 0.58 * span, LEFT + 0.82 * span)
    control_x = (a[0] + b[0]) / 2
    control_y = min(a[1], b[1]) - 8
    return [
        f'  <path d="M{a[0]:.1f} {a[1]:.1f} Q{control_x:.1f} {control_y:.1f} {b[0]:.1f} {b[1]:.1f}" '
        f'fill="none" stroke="{ACCENT}" stroke-width="1.2" stroke-linecap="round"/>',
        f'  <circle cx="{a[0]:.1f}" cy="{a[1]:.1f}" r="2.4" fill="{ACCENT}"/>',
        f'  <circle cx="{b[0]:.1f}" cy="{b[1]:.1f}" r="2.4" fill="{ACCENT}"/>',
    ]


def stacked_svg(
    count: int,
    top: float,
    bottom: float,
    height: float,
    stroke: float,
    shift: float,
    accent_front: bool,
    perspective: float = 0.0,
    morph: bool = False,
    bridge: bool = False,
    wordmark: str | None = None,
) -> str:
    width = 128 if wordmark is None else 352
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 128" role="img" aria-labelledby="title">',
        '  <title id="title">Liner</title>',
        f'  <rect width="128" height="128" rx="28" fill="{BG}"/>',
    ]
    for layer in range(count):
        share = layer / (count - 1)
        swing = height * (1 + perspective * share)
        points = trace(layer, top + (bottom - top) * share, swing, shift, morph)
        outline = _coords(points)
        below = f"{RIGHT:.1f},{FILL_BOTTOM:.1f} {LEFT:.1f},{FILL_BOTTOM:.1f}"
        lines.append(f'  <polygon points="{outline} {below}" fill="{BG}"/>')
        color = ACCENT if accent_front and layer == count - 1 else FG
        opacity = 0.35 + 0.65 * share
        lines.append(
            f'  <polyline points="{outline}" fill="none" stroke="{color}" stroke-width="{stroke}" '
            f'stroke-linejoin="round" stroke-linecap="round" opacity="{opacity:.2f}"/>'
        )
    if bridge:
        lines.extend(_bridge(top, height, shift, morph))
    if wordmark is not None:
        lines.append(
            f'  <text x="{TEXT_X}" y="86" font-family="{FONT}" font-size="80" font-weight="500" '
            f'letter-spacing="2" fill="{wordmark}">liner</text>'
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def preview_html(folder: Path, names: list[str]) -> str:
    rows = []
    for name in names:
        src = (folder / f"{name}.svg").as_uri()
        rows.append(
            f'<div class="row"><div class="name">{name}</div>'
            f'<img src="{src}" width="128" height="128" alt="">'
            f'<img src="{src}" width="48" height="48" alt="">'
            f'<img src="{src}" width="24" height="24" alt=""></div>'
        )
    body = "\n".join(rows)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "body{margin:0;padding:20px;font-family:system-ui,sans-serif;background:#fff;width:880px}"
        ".row{display:flex;align-items:center;gap:28px;margin-bottom:16px}"
        ".name{width:150px;font-size:14px;font-weight:600}"
        ".dark{background:#0d1117;padding:16px;border-radius:8px;color:#c9d1d9}"
        ".dark .name{color:#c9d1d9}"
        "</style></head><body>"
        f"{body}<div class=\"dark\">{body}</div></body></html>"
    )


def publish(name: str, logo_dir: Path) -> None:
    settings = VARIANTS[name]
    (logo_dir / "liner-mark.svg").write_text(stacked_svg(**settings), encoding="utf-8")
    (logo_dir / "liner-logo-light.svg").write_text(stacked_svg(**settings, wordmark=INK), encoding="utf-8")
    (logo_dir / "liner-logo-dark.svg").write_text(stacked_svg(**settings, wordmark=FG), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", choices=list(VARIANTS))
    parser.add_argument("--logo-dir", type=Path)
    args = parser.parse_args()
    if args.publish:
        publish(args.publish, args.logo_dir)
        print(f"published {args.publish} to {args.logo_dir}")
        return
    for name, settings in VARIANTS.items():
        (HERE / f"{name}.svg").write_text(stacked_svg(**settings), encoding="utf-8")
    (HERE / "stack-preview.html").write_text(preview_html(HERE, PREVIEW), encoding="utf-8")
    print("wrote", ", ".join(VARIANTS))


if __name__ == "__main__":
    main()
