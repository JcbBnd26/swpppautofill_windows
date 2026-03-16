"""Convert SVG icon to ICO using Pillow drawing (no external deps)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
ico_path = ROOT / "assets" / "app_icon.ico"


def draw_icon(size: int) -> Image.Image:
    """Draw a simplified version of the SWPPP clipboard icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 512  # scale factor

    # Orange rounded-rect background
    d.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=int(96 * s),
        fill=(232, 130, 12, 255),
    )

    # Clipboard body (cream)
    d.rounded_rectangle(
        [int(136 * s), int(100 * s), int(376 * s), int(420 * s)],
        radius=int(16 * s),
        fill=(255, 248, 238, 255),
        outline=(122, 68, 0, 255),
        width=max(1, int(3 * s)),
    )

    # Clipboard clip (brown bar)
    d.rounded_rectangle(
        [int(206 * s), int(78 * s), int(306 * s), int(122 * s)],
        radius=int(8 * s),
        fill=(122, 68, 0, 255),
    )
    # Clip inner (orange)
    d.rounded_rectangle(
        [int(222 * s), int(86 * s), int(290 * s), int(114 * s)],
        radius=int(6 * s),
        fill=(232, 130, 12, 255),
    )

    # Checklist rows
    for y_off in [158, 200, 242, 284]:
        # Checkbox (orange)
        cb_y = int(y_off * s)
        cb_x = int(168 * s)
        cb_size = int(20 * s)
        d.rounded_rectangle(
            [cb_x, cb_y, cb_x + cb_size, cb_y + cb_size],
            radius=max(1, int(4 * s)),
            fill=(232, 130, 12, 255),
            outline=(122, 68, 0, 255),
            width=max(1, int(1.5 * s)),
        )
        # Checkmark (cream)
        lw = max(1, int(2.5 * s))
        mx = cb_x + int(4 * s)
        my = cb_y + int(10 * s)
        d.line(
            [
                (mx, my),
                (mx + int(4 * s), my + int(6 * s)),
                (mx + int(14 * s), my - int(6 * s)),
            ],
            fill=(255, 248, 238, 255),
            width=lw,
        )
        # Text line (tan)
        lx = int(204 * s)
        ly = cb_y + int(4 * s)
        lw2 = max(1, int(10 * s))
        line_len = int(136 * s) if y_off % 84 == 0 else int(108 * s)
        d.rounded_rectangle(
            [lx, ly, lx + line_len, ly + lw2],
            radius=max(1, int(4 * s)),
            fill=(212, 160, 84, 128),
        )

    # Water drops (blue)
    for dx, dy, r in [(380, 280, 28), (410, 340, 22), (360, 360, 18)]:
        cx, cy, cr = int(dx * s), int(dy * s), int(r * s)
        d.ellipse(
            [cx - cr, cy - cr, cx + cr, cy + cr],
            fill=(100, 180, 240, 200),
            outline=(60, 130, 200, 255),
            width=max(1, int(2 * s)),
        )

    return img


sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
imgs = [draw_icon(s[0]) for s in sizes]
imgs[-1].save(str(ico_path), format="ICO", sizes=sizes, append_images=imgs[:-1])
print(f"Created {ico_path}")
