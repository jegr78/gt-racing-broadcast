"""Script-free SVG charts rendered exclusively from immutable telemetry inputs."""

import html
import math
import statistics
import track_map_labels
import ai_driver_review as driver

COLORS = ("#22d3ee", "#fb7185", "#a78bfa", "#fbbf24", "#34d399")


def esc(value):
    return html.escape(str(value), quote=True)


def number(value, digits=1):
    return (
        f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else str(round(value))
    )


def lap_label(lap, de=False):
    return ("Runde " if de else "Lap ") + str(lap["lap"])


def _svg(title, content, width=640, height=260):
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}"><title>{esc(title)}</title>{content}</svg>'


def _break_indices(points):
    """Do not connect across unusually sparse distance or receiver-time samples."""
    breaks = set()
    for key, minimum in (("d", 50), ("t", 0.5)):
        steps = [
            (i, right[key] - left[key])
            for i, (left, right) in enumerate(zip(points, points[1:], strict=False), 1)
            if driver.finite(left.get(key)) and driver.finite(right.get(key))
        ]
        positive = [delta for _, delta in steps if driver.finite(delta) and delta > 0]
        if not positive:
            continue
        threshold = max(minimum, 5 * statistics.median(positive))
        breaks.update(
            i
            for i, delta in steps
            if not driver.finite(delta) or delta > threshold or delta < 0
        )
    return breaks


def _path(points, x, y, key, max_points=1500):
    # Preserve gaps. Chart windows are normally short enough to retain every sample.
    parts, open_path = [], False
    stride = max(1, math.ceil(len(points) / max_points))
    breaks = _break_indices(points)
    for i, point in enumerate(points):
        if i in breaks:
            open_path = False
        if not driver.finite(point.get(key)) or not driver.finite(point.get("d")):
            open_path = False
            continue
        if i % stride and i != len(points) - 1:
            continue
        parts.append(
            ("L" if open_path else "M") + f"{x(point['d']):.2f},{y(point[key]):.2f}"
        )
        open_path = True
    return " ".join(parts)


def trace_chart(title, series, start, end, unit, de=False, bounds=None):
    values = [
        p.get(key)
        for _label, points, key, _color, _dash in series
        for p in points
        if start <= p["d"] <= end and driver.finite(p.get(key))
    ]
    if not values:
        return (
            "<figure><h3>"
            + esc(title)
            + "</h3><p>"
            + (
                "In diesem Paket nicht erfasst."
                if de
                else "Not captured in this package."
            )
            + "</p></figure>"
        )
    low, high = bounds or (min(values), max(values))
    if low == high:
        low -= 1
        high += 1
    x = lambda d: 62 + (d - start) / max(1, end - start) * 550
    y = lambda v: 208 - (v - low) / (high - low) * 178
    parts = []
    for i in range(5):
        value = low + (high - low) * i / 4
        py = y(value)
        parts.append(
            f'<path d="M62,{py:.1f}H612" class="gridline"/><text x="54" y="{py + 5:.1f}" text-anchor="end">{esc(number(value).replace(".", ",") if de else number(value))}</text>'
        )
        distance = start + (end - start) * i / 4
        parts.append(
            f'<text x="{x(distance):.1f}" y="232" text-anchor="middle">{round(distance)}</text>'
        )
    parts.append(
        f'<text x="62" y="18">{esc(unit)}</text><text x="612" y="252" text-anchor="end">'
        + ("Distanz (m)" if de else "Distance (m)")
        + "</text>"
    )
    legend = []
    unavailable = []
    for label, points, key, color, dash in series:
        window = [p for p in points if start <= p["d"] <= end]
        path = _path(window, x, y, key)
        if not path:
            unavailable.append(label)
            continue
        parts.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.4"'
            + (' stroke-dasharray="7 5"' if dash else "")
            + "/>"
        )
        legend.append(
            f'<span><i style="background:{color}"></i>{esc(label)}'
            + (" · - -" if dash else "")
            + "</span>"
        )
    return (
        "<figure><h3>"
        + esc(title)
        + "</h3>"
        + _svg(title, "".join(parts))
        + '<figcaption class="legend">'
        + "".join(legend)
        + (
            (" · Nicht erfasst: " if de else " · Not captured: ")
            + esc(", ".join(unavailable))
            if unavailable
            else ""
        )
        + "</figcaption></figure>"
    )


def line_chart(lap, reference, start, end, de=False, overview=False, definition=None):
    own = [p for p in lap.get("trace", []) if overview or start <= p["d"] <= end]
    other = (
        [p for p in reference.get("trace", []) if overview or start <= p["d"] <= end]
        if reference
        else []
    )
    rows = [
        p
        for p in own + other
        if driver.finite(p.get("x")) and driver.finite(p.get("z"))
    ]
    title = (
        ("Fahrlinie · Übersicht" if overview else "Fahrlinie · Detail")
        if de
        else ("Racing line · overview" if overview else "Racing line · detail")
    )
    if len(rows) < 2:
        return (
            "<figure><h3>"
            + esc(title)
            + "</h3><p>"
            + ("Position nicht erfasst." if de else "Position not captured.")
            + "</p></figure>"
        )
    xmin, xmax = min(p["x"] for p in rows), max(p["x"] for p in rows)
    zmin, zmax = min(p["z"] for p in rows), max(p["z"] for p in rows)
    height = 360 if overview else 250
    scale = min(550 / max(1, xmax - xmin), (height - 35) / max(1, zmax - zmin))
    x = lambda v: 320 + (v - (xmin + xmax) / 2) * scale
    y = lambda v: height / 2 + (v - (zmin + zmax) / 2) * scale
    parts = []
    for points, color, dash in (
        (other, "#a8b3c7", True),
        (own, "#536780" if overview else COLORS[0], False),
    ):
        segments, active = [], False
        stride = max(1, math.ceil(len(points) / 1500))
        breaks = _break_indices(points)
        for i, p in enumerate(points):
            if i in breaks:
                active = False
            if not all(driver.finite(p.get(k)) for k in ("x", "z")):
                active = False
                continue
            if i % stride and i != len(points) - 1:
                continue
            segments.append(
                ("L" if active else "M") + f"{x(p['x']):.2f},{y(p['z']):.2f}"
            )
            active = True
        if segments:
            path = " ".join(segments)
            parts.append(
                f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"'
                + (' stroke-dasharray="7 5"' if dash else "")
                + "/>"
            )
    if overview:
        for point in own:
            if start <= point["d"] <= end and all(
                driver.finite(point.get(k)) for k in ("x", "z")
            ):
                parts.append(
                    f'<circle cx="{x(point["x"]):.2f}" cy="{y(point["z"]):.2f}" r="1.8" fill="{COLORS[0]}"/>'
                )
    corners = (definition or {}).get("corners", [])
    if not overview:
        located = {
            row.get("id")
            for row in lap.get("derived", {}).get("corner_metrics", [])
            if driver.finite(row.get("apex_m")) and start <= row["apex_m"] <= end
        }
        corners = [corner for corner in corners if corner.get("id") in located]
    labels = track_map_labels.render(
        corners,
        lambda point: (x(point["x"]), y(point["z"])),
        640,
        height,
        reviewed=(definition or {}).get("reviewed", False),
    )
    parts.append(labels)
    caption = (
        lap_label(lap, de)
        + (" · türkis" if de else " · cyan")
        + (
            " / "
            + lap_label(reference, de)
            + (" · grau gestrichelt. " if de else " · dashed grey. ")
            if reference
            else ". "
        )
    )
    caption += (
        "Gemessene Positionen, gleicher Maßstab für x/z. Keine Ideallinie."
        if de
        else "Measured positions, equal x/z scale. No ideal line."
    )
    caption += " " + (
        (
            "Kurvennummern aus gespeicherten Markierungen."
            if de
            else "Turn numbers use saved markers."
        )
        if labels
        else (
            "Keine gespeicherten Kurvennummern für diesen Kartenausschnitt."
            if de
            else "No saved turn numbers for this map window."
        )
    )
    return (
        "<figure><h3>"
        + esc(title)
        + "</h3>"
        + _svg(title, "".join(parts), height=height)
        + "<figcaption>"
        + esc(caption)
        + "</figcaption></figure>"
    )


def trend(package, de=False):
    laps = package.get("laps", [])
    valid = [l for l in laps if driver.finite(l.get("metrics", {}).get("time_s"))]
    if not valid:
        return ""
    xmin, xmax = min(l["lap"] for l in valid), max(l["lap"] for l in valid)
    low, high = (
        min(l["metrics"]["time_s"] for l in valid) - 0.3,
        max(l["metrics"]["time_s"] for l in valid) + 0.3,
    )
    x = lambda v: 65 + (v - xmin) / max(1, xmax - xmin) * 540
    y = lambda v: 205 - (v - low) / max(0.1, high - low) * 170
    parts, previous = [], None
    for i in range(5):
        value = low + (high - low) * i / 4
        parts.append(
            f'<path d="M65,{y(value):.1f}H605" class="gridline"/><text x="55" y="{y(value) + 5:.1f}" text-anchor="end">{number(value, 2).replace(".", ",") if de else number(value, 2)}</text>'
        )
    for i, lap in enumerate(valid):
        context = lap.get("context", {})
        eligible = context.get("comparison_eligible", False)
        color = "#22d3ee" if eligible else "#fbbf24"
        value = lap["metrics"]["time_s"]
        px, py = x(lap["lap"]), y(value)
        if (
            previous
            and lap["lap"] == previous["lap"] + 1
            and context.get("stint_id") == previous.get("context", {}).get("stint_id")
        ):
            parts.append(
                f'<path d="M{x(previous["lap"]):.1f},{y(previous["metrics"]["time_s"]):.1f}L{px:.1f},{py:.1f}" stroke="#75849d" fill="none"/>'
            )
        label = (
            lap_label(lap, de)
            + " · "
            + (f"{value:.3f}".replace(".", ",") if de else f"{value:.3f}")
            + " s"
            + " · "
            + str(context.get("compound") or "?")
            + " · "
            + (
                {
                    "regular": "regulär",
                    "warmup": "Aufwärmen",
                    "pit": "Boxenrunde",
                    "outlap": "Ausfahrt",
                    "inlap": "Einfahrt",
                    "unknown": "unbekannt",
                }.get(context.get("analysis_role"), "andere Fahrphase")
                if de
                else str(context.get("analysis_role") or "unknown")
            )
        )
        tick = i % max(1, math.ceil(len(valid) / 12)) == 0 or i == len(valid) - 1
        parts.append(
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="{color}"><title>{esc(label)}</title></circle>'
            + (
                f'<text x="{px:.1f}" y="229" text-anchor="middle">{lap["lap"]}</text>'
                if tick
                else ""
            )
        )
        previous = lap
    parts.append(
        '<text x="65" y="18">s</text><text x="605" y="252" text-anchor="end">'
        + ("Runde" if de else "Lap")
        + "</text>"
    )
    title = "Rundenzeit-Verlauf" if de else "Lap-time trend"
    caption = (
        "Ausgewählte Runden. Türkis: für Vergleiche freigegeben; gelb: andere Fahrphase. Lücken und Stintwechsel werden nicht verbunden."
        if de
        else "Selected laps. Cyan: comparison eligible; amber: other driving phase. Gaps and stint changes are not connected."
    )
    groups = []
    for lap in valid:
        context = lap.get("context", {})
        key = (context.get("stint_id"), context.get("compound"))
        if groups and groups[-1][0] == key and lap["lap"] == groups[-1][2] + 1:
            groups[-1] = (key, groups[-1][1], lap["lap"])
        else:
            groups.append((key, lap["lap"], lap["lap"]))
    caption += (
        " "
        + "; ".join(
            ("Runden " if de else "Laps ")
            + str(first)
            + ("–" + str(last) if last != first else "")
            + " · Stint "
            + str(key[0] or "?")
            + " · "
            + str(key[1] or "?")
            for key, first, last in groups
        )
        + "."
    )
    return (
        '<figure class="wide"><h3>'
        + title
        + "</h3>"
        + _svg(title, "".join(parts))
        + "<figcaption>"
        + esc(caption)
        + "</figcaption></figure>"
    )


def render(package, report):
    de = str(package.get("language", "")).lower().startswith("de")
    laps = {
        lap["id"]: lap
        for lap in package.get("laps", []) + package.get("references", [])
    }
    chosen = []
    for finding in report.get("findings", []):
        lap, ref = laps.get(finding["lap_id"]), laps.get(finding.get("reference_id"))
        if driver.pair_allowed(package, lap, ref) and driver.finite(
            finding.get("location_m")
        ):
            chosen.append(
                dict(
                    lap_id=lap["id"],
                    reference_id=ref["id"],
                    location_m=finding["location_m"],
                )
            )
    chosen.extend(driver.focus(package, limit=3))
    if not chosen:
        for comparison in package.get("comparisons", []):
            lap, ref = (
                laps.get(comparison["lap_id"]),
                laps.get(comparison["reference_id"]),
            )
            if driver.pair_allowed(package, lap, ref):
                chosen.append(
                    dict(
                        lap_id=lap["id"],
                        reference_id=ref["id"],
                        location_m=lap["trace"][-1]["d"] / 2,
                    )
                )
                break
    if not chosen and package.get("laps"):
        lap = package["laps"][0]
        chosen.append(
            dict(
                lap_id=lap["id"],
                reference_id=None,
                location_m=lap["trace"][-1]["d"] / 2,
            )
        )
    output = [
        '<section id="charts"><h2>'
        + (
            "Daten hinter den Fahrhinweisen"
            if de
            else "Telemetry behind the driving review"
        )
        + "</h2>",
        trend(package, de),
    ]
    seen = set()
    for item in chosen:
        lap, ref = laps[item["lap_id"]], laps.get(item["reference_id"])
        distance = item["location_m"]
        start = max(0, distance - 180)
        end = min(
            lap["trace"][-1]["d"],
            ref["trace"][-1]["d"] if ref else lap["trace"][-1]["d"],
            distance + 180,
        )
        if start >= end:
            continue
        key = (lap["id"], ref["id"] if ref else None, round(distance / 200))
        if key in seen:
            continue
        seen.add(key)
        heading = (
            lap_label(lap, de)
            + (
                " / " + ("Referenz " if de else "reference ") + lap_label(ref, de)
                if ref
                else ""
            )
            + " · "
            + str(round(start))
            + "–"
            + str(round(end))
            + " m"
        )
        heading = driver.passage(package, lap, distance) + " · " + heading
        definition = package.get("track_definition_snapshots", {}).get(
            lap.get("derived", {}).get("definition_fingerprint")
        )
        identity = lap.get("rec", "") + " · S" + str(lap.get("session", ""))
        if ref:
            identity += (
                " / "
                + ("Referenz: " if de else "Reference: ")
                + ref.get("rec", "")
                + " · S"
                + str(ref.get("session", ""))
            )
        output += [
            '<section class="trace-review"><h3>'
            + esc(heading)
            + "</h3><p>"
            + esc(identity)
            + "</p><p>"
            + (
                "Gleicher Vergleichskontext, verbleibende Unterschiede bei Tanklast und Reifenalter beachten. Eine Zeitdifferenz ist kein garantierter Gewinn."
                if de
                else "Compatible comparison context; fuel load and tyre age can still differ. An observed difference is not guaranteed recoverable time."
            )
            + '</p><div class="chart-grid">',
            line_chart(lap, ref, start, end, de, overview=True, definition=definition),
            line_chart(lap, ref, start, end, de, definition=definition),
        ]
        if not ref:
            output.append(
                "<p>"
                + (
                    "Einzelrunde ohne passende Referenz."
                    if de
                    else "Single lap without a compatible reference."
                )
                + "</p>"
            )
        for title, key, unit, bounds in (
            (("Geschwindigkeit" if de else "Speed"), "speed_kmh", "km/h", None),
            (("Gasstellung" if de else "Throttle"), "throttle", "%", (0, 100)),
            (("Bremsbetätigung" if de else "Brake input"), "brake", "%", (0, 100)),
            (("Lenkwinkel" if de else "Steering angle"), "steer_deg", "deg", None),
        ):
            output.append(
                trace_chart(
                    title,
                    [
                        (lap_label(lap, de), lap["trace"], key, COLORS[0], False),
                        (
                            ("Referenz " if de else "Reference ") + lap_label(ref, de),
                            ref["trace"],
                            key,
                            "#a8b3c7",
                            True,
                        ),
                    ]
                    if ref
                    else [(lap_label(lap, de), lap["trace"], key, COLORS[0], False)],
                    start,
                    end,
                    unit,
                    de,
                    bounds,
                )
            )
        output.append("</div></section>")
        if len(seen) >= 3:
            break
    if not seen:
        output.append(
            "<p>"
            + (
                "Für diese Auswahl liegt kein freigegebenes Rundenpaar vor. Es wird keine Vergleichslinie erfunden."
                if de
                else "No eligible lap pair is available for this selection. No comparison trace is invented."
            )
            + "</p>"
        )
    output.append("</section>")
    return "\n".join(output)
