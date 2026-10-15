"""Deterministic validation, profile-owned history and safe report/input exports."""

import copy
import hashlib
import io
import json
import math
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import ai_driver_review
import ai_report_text
import ai_jobs
import ai_package


class ReportError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _fail(message):
    raise ReportError("invalid_report", message)


def _schema(value, schema, depth=0):
    if depth > 32:
        _fail("Result nesting exceeds the bound")
    expected = schema["type"]
    types = expected if isinstance(expected, list) else [expected]
    valid = any(
        (t == "object" and isinstance(value, dict))
        or (t == "array" and isinstance(value, list))
        or (t == "string" and isinstance(value, str))
        or (t == "integer" and type(value) is int)
        or (t == "number" and type(value) in (int, float) and math.isfinite(value))
        or (t == "null" and value is None)
        for t in types
    )
    if not valid:
        _fail("Result field has an invalid type")
    if "enum" in schema and value not in schema["enum"]:
        _fail("Result field is outside the allowed values")
    if isinstance(value, str) and (
        not value.strip()
        or len(value) > 8000
        or any(ord(c) < 32 and c not in "\n\t" for c in value)
    ):
        _fail("Result text is empty, too long or contains control characters")
    if isinstance(value, dict):
        if set(value) != set(schema["properties"]):
            _fail("Result object has missing or extra fields")
        for k, v in value.items():
            _schema(v, schema["properties"][k], depth + 1)
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 128):
            _fail("Result list has invalid length")
        for item in value:
            _schema(item, schema["items"], depth + 1)


def _related(fact_id, lap_id, package):
    if fact_id.startswith("selection/"):
        return True
    if fact_id.startswith("comparison/"):
        try:
            comparison = package["comparisons"][int(fact_id.split("/")[1])]
        except (ValueError, IndexError, KeyError):
            return False
        return lap_id in (comparison["lap_id"], comparison["reference_id"])
    if fact_id.startswith(lap_id + "/"):
        return True
    other = next(
        (
            lap["id"]
            for lap in package["laps"] + package["references"]
            if fact_id.startswith(lap["id"] + "/")
        ),
        None,
    )
    if not other:
        return False
    reachable = {lap_id}
    pending = [lap_id]
    while pending:
        current = pending.pop()
        for comparison in package["comparisons"]:
            pair = {comparison["lap_id"], comparison["reference_id"]}
            if current in pair:
                for neighbour in pair - reachable:
                    reachable.add(neighbour)
                    pending.append(neighbour)
    return other in reachable


def fact_label(fact_id, package=None):
    labels = {
        "time_s": "Lap time (s)",
        "gt7_time_s": "GT7 lap time (s)",
        "relay_time_s": "Receiver lap time (s)",
        "fuel_used_l": "Fuel used (L)",
        "fuel_start_l": "Starting fuel (L)",
        "fuel_end_l": "Ending fuel (L)",
        "top_speed_kmh": "Top speed (km/h)",
        "tyre_avg_c": "Mean tyre temperature (C)",
        "length_m": "Lap length (m)",
        "delta_s": "Observed time difference (s)",
        "mean_time_s": "Mean selected lap time (s)",
        "best_time_s": "Best selected lap time (s)",
        "population_stddev_s": "Population standard deviation (s)",
        "selected_gt7_total_s": "Sum of selected GT7 lap times (s)",
        "usable_laps": "Usable lap count",
        "excluded_laps": "Excluded lap count",
    }
    de = ai_report_text.german(package or {})
    labels.update(ai_report_text.DE_FACTS if de else ai_report_text.EN_FACTS)
    short = re.sub(r"^[0-9a-f]+/session/[0-9]+/lap/[0-9]+/", "", fact_id)
    label = labels.get(
        short,
        labels.get(fact_id.split("/")[-1], short.replace("/", " · ").replace("_", " ")),
    )
    if de and fact_id.split("/")[-1] not in labels:
        label = "Messwert · " + short
    if fact_id.endswith("/time_s") and "/derived/" in fact_id:
        label = "Abschnittszeit (s)" if de else "Section time (s)"
    if "/corner_metrics/" in fact_id:
        try:
            index = int(fact_id.split("/corner_metrics/", 1)[1].split("/")[0])
            label = ("Kurvenabschnitt " if de else "Corner section ") + str(index + 1) + " · " + label
        except ValueError:
            pass  # Unknown historic field keeps its general label.
    match = re.match(r"^([0-9a-f]+/session/([0-9]+)/lap/([0-9]+))/", fact_id)
    if match:
        lap = next(
            (
                lap
                for lap in (package or {}).get("laps", [])
                + (package or {}).get("references", [])
                if lap["id"] == match.group(1)
            ),
            {},
        )
        return (
            lap.get("rec", "Ausgewählte Aufzeichnung" if de else "Selected recording")
            + " · S"
            + match.group(2)
            + (" Runde " if de else " lap ")
            + match.group(3)
            + " · "
            + label
        )
    return label


def validate(result, package):
    """Validate measurements/references; narrative interpretations remain unproven."""
    _schema(result, ai_package.result_schema(package))
    try:
        laps = ai_package.lap_map(package["laps"] + package["references"])
    except ai_package.PackageError as e:
        _fail(str(e))
    v2 = package.get("report_schema_version", 1) == 2
    priorities = [e["priority"] for e in result["exercises"]]
    if (not v2 and sorted(priorities) != [1, 2, 3]) or len(priorities) != len(set(priorities)):
        _fail("Exercises require distinct priorities 1, 2 and 3")
    report = copy.deepcopy(result)
    for item in report["findings"] + report["exercises"]:
        lap = laps.get(item["lap_id"])
        if lap is None:
            _fail("Result references a nonexistent selected lap")
        reference = laps.get(item.get("reference_id"))
        if v2 and item.get("reference_id") is not None and not ai_driver_review.pair_allowed(package, lap, reference):
            _fail("Result references an undeclared or incompatible comparison")
        if v2 and item.get("topic") not in (None, "consistency") and item["location_m"] is None:
            _fail("Technique findings require a location on the lap")
        location = item["location_m"]
        end = lap["trace"][-1]["d"]
        if location is not None and not 0 <= location <= end:
            _fail("Result location leaves the selected lap trace")
        for evidence in item["evidence"]:
            fact = package["facts"].get(evidence["fact_id"])
            if fact is None:
                _fail("Unknown evidence fact ID: " + evidence["fact_id"][:160])
            if evidence["value"] != fact["value"]:
                _fail("Evidence value differs from the frozen package fact: " + evidence["fact_id"][:160])
            related = _related(evidence["fact_id"], item["lap_id"], package)
            if v2:
                fact_id = evidence["fact_id"]
                related = fact_id.startswith(lap["id"] + "/") or bool(reference and fact_id.startswith(reference["id"] + "/"))
                if item.get("topic") == "consistency" and fact_id.startswith("selection/"):
                    related = True
                if fact_id.startswith("comparison/") and reference:
                    try:
                        pair = package["comparisons"][int(fact_id.split("/")[1])]
                        related = {pair["lap_id"], pair["reference_id"]} == {lap["id"], reference["id"]}
                    except (ValueError, IndexError, KeyError):
                        related = False
            if not related:
                _fail("Evidence refers to a lap outside this item's declared comparison: " + evidence["fact_id"][:160])
            evidence["provenance"] = fact["provenance"]
        item["telemetry"] = dict(
            rec=lap["rec"],
            session=lap["session"],
            lap=lap["lap"],
            location_m=location,
            recording_id=lap["recording_id"],
        )
    report["findings"].sort(key=lambda f: f["priority"])
    report["exercises"].sort(key=lambda e: e["priority"])
    report.update(
        format="racecast-ai-report",
        language=package["language"],
        package_fingerprint=package["fingerprint"],
    )
    report["limitations"] = list(
        dict.fromkeys(package["limitations"] + report["limitations"])
    )
    return report


def _relevance(package):
    doc = copy.deepcopy(package)
    doc.pop("fingerprint", None)

    def prune(value):
        if isinstance(value, dict):
            if value.get("snapshot_kind") == "selected-effective-context-v1":
                value.pop("revision", None)
            for nested in value.values():
                prune(nested)
        elif isinstance(value, list):
            for nested in value:
                prune(nested)

    prune(doc)
    return ai_package.fingerprint(doc)


def stale(package, source_factory, enrich_source=None):
    try:
        selection = package["selection"]
        source = source_factory(selection["recording"])
        refs = [
            (source_factory(r["rec"]), r["session"], r["lap"])
            for r in package["references"]
        ]
        current = ai_package.build(
            source,
            selection["session"],
            laps=selection["laps"],
            references=refs,
            template=package["template"],
            goal=package["goal"],
            questions=package["questions"],
            language=package["language"],
            enrich_source=enrich_source,
        )
        return dict(
            stale=_relevance(package) != _relevance(current),
            reason="Relevant source, context or reference definitions changed"
            if _relevance(package) != _relevance(current)
            else None,
        )
    except (ValueError, OSError, KeyError, ai_package.gt7_recording.RecordingError):
        return dict(
            stale=True,
            reason="Current source/context/reference selection is unavailable; the historical snapshot is retained",
        )


def _nonfinite(value):
    raise ValueError("Non-finite artifact JSON")


def _read(path, root, *, max_bytes=ai_jobs.MAX_OUTPUT_BYTES):
    path = Path(path)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ReportError(
            "invalid_artifact", "Analysis artifact leaves the active profile"
        )
    try:
        if path.name == "run.json":
            with ai_jobs._state_io(path):
                raw = ai_jobs.regular_output_bytes(path, max_bytes=max_bytes)
            return json.loads(raw, parse_constant=_nonfinite)
        return json.loads(
            ai_jobs.regular_output_bytes(path, max_bytes=max_bytes),
            parse_constant=_nonfinite,
        )
    except (ValueError, OSError, ai_jobs.JobError) as e:
        raise ReportError(
            "invalid_artifact", "Analysis artifact is not valid bounded local JSON"
        ) from e


def read_package(directory, root):
    package = _read(
        Path(directory) / "package/detail.json", root,
        max_bytes=ai_package.MAX_DETAIL_BYTES,
    )
    if not isinstance(package, dict):
        raise ReportError("invalid_artifact", "Input package is not an object")
    expected = copy.deepcopy(package)
    digest = expected.pop("fingerprint", None)
    if ai_package.fingerprint(expected) != digest:
        raise ReportError("invalid_artifact", "Saved input package fingerprint changed")
    return package


def verify_input_files(directory, root, run, required=False):
    hashes = run.get("input_file_fingerprints")
    if not hashes and not required:
        return  # Legacy incomplete runs retain their original package.
    names = {"detail.json", "summary.md", "manifest.json", "result-schema.json"}
    if not isinstance(hashes, dict) or set(hashes) != names:
        raise ReportError(
            "invalid_artifact", "Run omitted its immutable input/prompt identities"
        )
    for name, digest in hashes.items():
        path = Path(directory) / "package" / name
        if not path.resolve().is_relative_to(Path(root).resolve()):
            raise ReportError(
                "invalid_artifact", "Input file leaves the active profile"
            )
        try:
            limit = ai_package.MAX_DETAIL_BYTES if name == "detail.json" else ai_jobs.MAX_OUTPUT_BYTES
            actual = hashlib.sha256(ai_jobs.regular_output_bytes(path, max_bytes=limit)).hexdigest()
        except (OSError, ai_jobs.JobError) as e:
            raise ReportError("invalid_artifact", "Input file is unavailable") from e
        if actual != digest:
            raise ReportError(
                "invalid_artifact", "Immutable input or prompt file changed"
            )


class Store:
    def __init__(
        self, profile_root, profile, machine, source_factory=None, enrich_source=None
    ):
        self.root = Path(profile_root).resolve()
        self.profile = profile
        self.machine = Path(machine).resolve()
        self.base = self.root / "telemetry-analyses"
        self.source_factory = source_factory
        self.enrich_source = enrich_source

    def directory(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch("[0-9a-f]{32}", job_id):
            raise ReportError("invalid_job", "Invalid analysis job ID")
        for path in self.base.glob("*/" + job_id):
            if not re.fullmatch("[0-9a-f]{32}|[0-9a-f]{64}", path.parent.name):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                continue
            doc = _read(path / "run.json", self.root)
            if (
                isinstance(doc, dict)
                and doc.get("id") == job_id
                and doc.get("profile") == self.profile
            ):
                return path
        raise ReportError("not_found", "Analysis run not found in the active profile")

    def get(self, job_id, check_stale=True):
        directory = self.directory(job_id)
        run = _read(directory / "run.json", self.root)
        if run["state"] == "completed":
            verify_input_files(directory, self.root, run, required=True)
            package = read_package(directory, self.root)
            checked = validate(
                _read(directory / "structured-output.json", self.root), package
            )
            if checked != run.get("report"):
                raise ReportError(
                    "invalid_artifact", "Stored report differs from validated output"
                )
            run.update(
                stale(package, self.source_factory, self.enrich_source)
                if check_stale and self.source_factory
                else dict(stale=False, reason=None)
            )
        else:
            run.update(stale=False, reason=None, report=None)
            if run["state"] not in ai_jobs.TERMINAL:
                status = ai_jobs.Runner(self.machine, self.root, self.profile).status(
                    self.profile
                )
                if (
                    not status["busy"]
                    or not status["active"]
                    or status["active"].get("id") != job_id
                ):
                    run.update(
                        state="interrupted",
                        progress="Process ended without a completed report; retained artifacts are incomplete",
                    )
        if run.get("report"):
            run["report"]["limitations"] = [ai_report_text.limitation(v, package) for v in run["report"]["limitations"]]
            for item in run["report"]["findings"] + run["report"]["exercises"]:
                lap = next(lap for lap in package["laps"] + package["references"] if lap["id"] == item["lap_id"])
                item["location_label"] = ai_driver_review.passage(package, lap, item["location_m"])
                for evidence in item["evidence"]:
                    evidence["label"] = fact_label(evidence["fact_id"], package)
                    evidence["display_value"] = ai_report_text.display_value(evidence["value"], package)
                partners = {
                    c["reference_id"]
                    for c in package["comparisons"]
                    if c["lap_id"] == item["lap_id"]
                    and c["reference_id"] != item["lap_id"]
                }
                if run["report"].get("version") == 2:
                    partners = {item.get("reference_id")} - {None}
                item["comparisons"] = [
                    dict(
                        rec=lap["rec"],
                        session=lap["session"],
                        lap=lap["lap"],
                        recording_id=lap["recording_id"],
                    )
                    for lap in ai_package.lap_map(
                        package["laps"] + package["references"]
                    ).values()
                    if lap["id"] in partners
                ]
        diagnostics = []
        if run["state"] in ai_jobs.TERMINAL and run["state"] != "completed":
            for name in ("validation-error.log", "runner-error.log", "stderr.log"):
                path = directory / name
                try:
                    raw = ai_jobs.regular_output_bytes(path, max_bytes=16385, prefix=True)
                except (ai_jobs.JobError, OSError):
                    continue
                if raw:
                    diagnostics.append(dict(name=name, text=raw[:16384].decode("utf-8", "replace")
                                            + ("\n[truncated]" if len(raw) > 16384 else "")))
        return dict(run, diagnostics=diagnostics, ok=True)

    def history(self, rec=None):
        items = []
        for path in self.base.glob("*/*/run.json"):
            try:
                run = self.get(path.parent.name)
                if rec is not None and ai_package.gt7_recording.recording_stem(
                    run["selection"]["recording"]
                ) != ai_package.gt7_recording.recording_stem(rec):
                    continue
                run.pop("report", None)
                items.append(run)
            except (ReportError, KeyError, TypeError):
                continue
        items.sort(key=lambda r: r["created_at"], reverse=True)
        return dict(ok=True, profile=self.profile, runs=items[:200])

    def export(self, job_id, kind, origin=None):
        if origin is not None:
            if not isinstance(origin, str):
                raise ReportError(
                    "invalid_selection",
                    "Export links require the local Control Center origin",
                )
            try:
                parsed = urlparse(origin)
                valid = (
                    parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                    and parsed.port is not None
                    and 0 < parsed.port <= 65535
                    and parsed.username is None
                    and not parsed.path
                    and not parsed.query
                    and not parsed.fragment
                )
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise ReportError(
                    "invalid_selection",
                    "Export links require the local Control Center origin",
                )
        directory = self.directory(job_id)
        if kind == "package":
            verify_input_files(
                directory, self.root, _read(directory / "run.json", self.root)
            )
            read_package(directory, self.root)
            files = {
                name: ai_jobs.regular_output_bytes(
                    directory / "package" / name,
                    max_bytes=ai_package.MAX_DETAIL_BYTES
                    if name == "detail.json" else ai_jobs.MAX_OUTPUT_BYTES,
                )
                for name in (
                    "detail.json",
                    "summary.md",
                    "manifest.json",
                    "result-schema.json",
                )
            }
            return dict(
                filename="racecast-input-" + job_id + ".zip",
                mime="application/zip",
                bytes=_archive(files),
            )
        run = self.get(job_id, check_stale=False)
        if kind not in ("html", "markdown"):
            raise ReportError(
                "invalid_selection", "Choose HTML, Markdown or the input package"
            )
        if run["state"] != "completed":
            raise ReportError(
                "incomplete_report",
                "Only validated completed reports can be exported as reports",
            )
        package = read_package(directory, self.root)
        rendered = render(run, package, kind, origin)
        return dict(
            filename="racecast-analysis-"
            + job_id
            + (".html" if kind == "html" else ".md"),
            mime="text/html; charset=utf-8"
            if kind == "html"
            else "text/markdown; charset=utf-8",
            bytes=rendered.encode("utf-8"),
        )


def _archive(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    return out.getvalue()


def export_package(package, provider):
    with tempfile.TemporaryDirectory(prefix="racecast-ai-export-") as root:
        path = Path(root) / "package"
        ai_package.write(package, path, provider)
        return _archive({
            name: (path / name).read_bytes()
            for name in (
                "detail.json",
                "summary.md",
                "manifest.json",
                "result-schema.json",
            )
        })


def render(run, package, kind, origin=None):
    import ai_report_view
    return ai_report_view.render(run, package, kind, origin, fact_label)


def delete_recording_analyses(profile_root, recording_id, recording_name=None):
    """Remove profile-owned runs by source identity or its immutable recording name."""
    root = Path(profile_root).resolve()
    base = root / "telemetry-analyses"
    if base.is_symlink() or not base.resolve().is_relative_to(root):
        raise ReportError("invalid_artifact", "Refusing redirected analysis deletion")
    identities = set()
    if recording_id is not None:
        if not isinstance(recording_id, str) or not re.fullmatch(
            "[0-9a-f]{32}|[0-9a-f]{64}", recording_id
        ):
            raise ReportError(
                "invalid_artifact", "Cannot identify recording-owned analysis artifacts"
            )
        identities.add(recording_id)
    if recording_name:
        for path in base.glob("*/*/run.json"):
            try:
                run = _read(path, root)
            except ReportError:
                continue
            if (
                isinstance(run, dict)
                and run.get("format") == "racecast-ai-run"
                and (run.get("selection") or {}).get("recording") == recording_name
            ):
                ident = path.parent.parent.name
                if re.fullmatch("[0-9a-f]{32}|[0-9a-f]{64}", ident):
                    identities.add(ident)
    for ident in identities:
        directory = base / ident
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            raise ReportError(
                "invalid_artifact", "Refusing redirected analysis deletion"
            )
        if directory.exists():
            shutil.rmtree(directory)
