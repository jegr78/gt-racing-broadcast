"""Driver review inputs calculated from frozen traces; no ideal-driver assumptions."""

import math


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def channels(trace, corners):
    """Sample-based corner metrics. Thresholds describe inputs, never optimal targets."""
    output = []
    for corner in corners:
        start, end = corner.get("entry_m"), corner.get("exit_m")
        if not all(finite(v) for v in (start, end)) or start >= end:
            continue
        points = [p for p in trace if start <= p["d"] <= end]
        if len(points) < 2:
            continue
        row = dict(
            id=corner.get("id"),
            number=corner.get("number"),
            start_m=start,
            end_m=end,
            samples=len(points),
            brake_threshold_pct=5,
            throttle_threshold_pct=95,
        )
        speed = [p for p in points if finite(p.get("speed_kmh"))]
        slowest = (
            min(speed, key=lambda p: p["speed_kmh"])
            if len(speed) == len(points)
            else None
        )
        throttle = [p["throttle"] for p in points if finite(p.get("throttle"))]
        steering = [abs(p["steer_deg"]) for p in points if finite(p.get("steer_deg"))]
        row["mean_throttle_pct"] = (
            sum(throttle) / len(throttle) if len(throttle) == len(points) else None
        )
        row["mean_abs_steering_deg"] = (
            sum(steering) / len(steering) if len(steering) == len(points) else None
        )
        row["peak_abs_steering_deg"] = (
            max(steering) if len(steering) == len(points) else None
        )
        row["min_speed_m"] = slowest["d"] if slowest else None
        powered = [
            p
            for p in points
            if slowest
            and p["d"] >= slowest["d"]
            and finite(p.get("throttle"))
            and p["throttle"] >= 95
        ]
        row["full_throttle_after_min_m"] = (
            powered[0]["d"] if powered and len(throttle) == len(points) else None
        )
        # Do not label an entry/exit sample as a detected pedal transition.
        braked = [p for p in points if finite(p.get("brake")) and p["brake"] >= 5]
        brake_complete = all(finite(p.get("brake")) for p in points)
        row["brake_first_m"] = (
            braked[0]["d"]
            if braked and brake_complete and points[0]["brake"] < 5
            else None
        )
        row["brake_last_m"] = (
            braked[-1]["d"]
            if braked and brake_complete and points[-1]["brake"] < 5
            else None
        )
        output.append(row)
    return output


def pair_allowed(package, lap, reference):
    """Only declared compatible pairs; known stint changes are not a driving diagnosis."""
    if not lap or not reference or lap["id"] == reference["id"]:
        return False
    if not any(
        {c["lap_id"], c["reference_id"]} == {lap["id"], reference["id"]}
        for c in package.get("comparisons", [])
    ):
        return False
    own, ref = lap.get("context", {}), reference.get("context", {})
    if not own.get("comparison_eligible") or not ref.get("comparison_eligible"):
        return False
    if lap.get("car_id") != reference.get("car_id"):
        return False
    same_session = (
        bool(lap.get("recording_id"))
        and lap.get("recording_id") == reference.get("recording_id")
        and lap.get("session") == reference.get("session")
    )
    if (
        not (lap.get("track_id") and lap.get("track_id") == reference.get("track_id"))
        and not same_session
    ):
        return False
    if own.get("compound") != ref.get("compound"):
        return False
    if (
        same_session
        and own.get("stint_id")
        and ref.get("stint_id")
        and own["stint_id"] != ref["stint_id"]
    ):
        return False
    if (
        own.get("strategy")
        and ref.get("strategy")
        and own["strategy"] != ref["strategy"]
    ):
        return False
    return True


def focus(package, limit=4):
    """Bounded, inspectable corner comparisons with verbatim frozen evidence IDs."""
    laps = {l["id"]: l for l in package.get("laps", []) + package.get("references", [])}
    candidates = []
    for comparison in package.get("comparisons", []):
        lap, ref = (laps.get(comparison[key]) for key in ("lap_id", "reference_id"))
        if not pair_allowed(package, lap, ref):
            continue
        own, other = lap.get("derived", {}), ref.get("derived", {})
        if not own.get("definition_fingerprint") or own.get(
            "definition_fingerprint"
        ) != other.get("definition_fingerprint"):
            continue
        refs = {
            row.get("id"): (i, row)
            for i, row in enumerate(other.get("corner_metrics", []))
        }
        for i, row in enumerate(own.get("corner_metrics", [])):
            match = refs.get(row.get("id"))
            if (
                not match
                or not finite(row.get("time_s"))
                or not finite(match[1].get("time_s"))
            ):
                continue
            difference = row["time_s"] - match[1]["time_s"]
            if not math.isfinite(difference) or difference <= 0:
                continue
            reference_i, reference_row = match
            if not all(finite(row.get(k)) for k in ("entry_m", "exit_m", "apex_m")):
                continue
            if not all(
                finite(reference_row.get(k)) for k in ("entry_m", "exit_m", "apex_m")
            ):
                continue
            if any(
                abs(row[k] - reference_row[k]) > 1
                for k in ("entry_m", "exit_m", "apex_m")
            ):
                continue
            facts = {}
            for current, corner_i in ((lap, i), (ref, reference_i)):
                prefix = (
                    current["id"] + "/derived/corner_metrics/" + str(corner_i) + "/"
                )
                for key in (
                    "time_s",
                    "entry_speed_kmh",
                    "exit_speed_kmh",
                    "min_speed_kmh",
                    "max_brake_pct",
                ):
                    fact = package["facts"].get(prefix + key)
                    if fact:
                        facts[prefix + key] = fact["value"]
                for channel_i, channel in enumerate(
                    current.get("derived", {}).get("driver_channels", [])
                ):
                    if channel.get("id") != row.get("id"):
                        continue
                    prefix = (
                        current["id"]
                        + "/derived/driver_channels/"
                        + str(channel_i)
                        + "/"
                    )
                    for key in (
                        "brake_first_m",
                        "brake_last_m",
                        "full_throttle_after_min_m",
                        "mean_throttle_pct",
                        "mean_abs_steering_deg",
                        "peak_abs_steering_deg",
                    ):
                        fact = package["facts"].get(prefix + key)
                        if fact:
                            facts[prefix + key] = fact["value"]
            candidate = dict(
                lap_id=lap["id"],
                reference_id=ref["id"],
                corner=row.get("number"),
                start_m=row["entry_m"],
                end_m=row["exit_m"],
                location_m=row["apex_m"],
                context=dict(lap=lap["context"], reference=ref["context"]),
                evidence=facts,
            )
            candidates.append((difference, candidate))
    output, seen = [], set()
    for _difference, candidate in sorted(
        candidates, key=lambda item: item[0], reverse=True
    ):
        # Keep distinct locations, rather than repeating the same corner for many laps.
        key = (candidate["corner"], round(candidate["start_m"]))
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
        if len(output) >= limit:
            break
    return output


def passage(package, lap, distance):
    """Identify the passage using this lap's saved marker snapshot, never a live catalogue."""
    if not finite(distance):
        return ""
    derived = lap.get("derived", {})
    definition = package.get("track_definition_snapshots", {}).get(
        derived.get("definition_fingerprint"), {}
    )
    numbers = {
        corner["id"]: corner.get("number") for corner in definition.get("corners", [])
    }
    rows = sorted(
        (
            row
            for row in derived.get("corner_metrics", [])
            if row.get("id") in numbers
            and row.get("number") == numbers[row["id"]]
            and finite(row.get("apex_m"))
        ),
        key=lambda row: row["apex_m"],
    )
    prefix = "T" if definition.get("reviewed") else "P"
    metre = str(round(distance)) + " m"
    inside = [
        row
        for row in rows
        if finite(row.get("entry_m"))
        and finite(row.get("exit_m"))
        and row["entry_m"] <= distance <= row["exit_m"]
    ]
    if inside:
        corner = min(inside, key=lambda row: abs(row["apex_m"] - distance))
        return prefix + str(corner["number"]) + " · " + metre
    before = next((row for row in reversed(rows) if row["apex_m"] < distance), None)
    after = next((row for row in rows if row["apex_m"] > distance), None)
    if before and after:
        return (
            prefix
            + str(before["number"])
            + "–"
            + prefix
            + str(after["number"])
            + " · "
            + metre
        )
    return metre
