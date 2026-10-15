"""Safe turn-number callouts for maps using saved world anchors."""

import html
import math


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def render(
    corners,
    project,
    width,
    height,
    *,
    reviewed=False,
    foreground="#e8eef7",
    outline="#192437",
):
    """Separate dense labels into two columns; never infer numbering from geometry."""
    groups = [[], []]
    seen = set()
    for corner in corners:
        number, point = corner.get("number"), corner.get("apex") or {}
        if type(number) is not int or number <= 0 or number in seen:
            continue
        if not all(_finite(point.get(key)) for key in ("x", "z")):
            continue
        x, y = project(point)
        if not all(_finite(value) for value in (x, y)) or not (
            0 <= x <= width and 0 <= y <= height
        ):
            continue
        seen.add(number)
        groups[int(x >= width / 2)].append((y, x, corner))
    parts = []
    for side, rows in enumerate(groups):
        rows.sort(key=lambda row: row[0])
        gap = min(16, (height - 28) / max(1, len(rows) - 1))
        previous = -100
        for index, (y, x, corner) in enumerate(rows):
            label_y = min(
                height - 10 - (len(rows) - index - 1) * gap,
                max(16 + index * gap, y + 4, previous + gap),
            )
            previous = label_y
            label_x = width - 10 if side else 10
            line_x = width - 38 if side else 38
            label = ("T" if reviewed else "P") + str(corner["number"])
            title = html.escape(
                label + (" · " + corner["name"] if corner.get("name") else ""),
                quote=True,
            )
            anchor = "end" if side else "start"
            parts.append(
                f'<g class="turn-marker"><title>{title}</title>'
                f'<path d="M{x:.2f},{y:.2f}L{line_x},{label_y - 4:.2f}" fill="none" stroke="{foreground}" stroke-width=".8" opacity=".65"/>'
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.5" fill="{foreground}"/>'
                f'<text class="turn-label" x="{label_x}" y="{label_y:.2f}" text-anchor="{anchor}" '
                f'fill="{foreground}" stroke="{outline}" stroke-width="3" paint-order="stroke" '
                f'font-family="system-ui" font-size="12" font-weight="600">{label}</text></g>'
            )
    return "".join(parts)
