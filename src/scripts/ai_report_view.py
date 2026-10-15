"""Portable, localized driver reports with deterministic SVG telemetry charts."""

import html
import json
from urllib.parse import urlencode

import ai_report_charts as charts
import ai_report_text as text

STRINGS = {
    "title": ("Fahrerbericht", "Driver review"),
    "intro": (
        "Fahrhinweise aus deinen ausgewählten Runden. Messwerte sind geprüft; Erklärungen und mögliche Ursachen bleiben Hypothesen.",
        "Driving feedback from your selected laps. Cited measurements are checked; interpretations and possible causes remain hypotheses.",
    ),
    "findings": ("Deine nächsten Fahrthemen", "Your next driving priorities"),
    "exercises": ("Übungen für den nächsten Stint", "Practice for the next stint"),
    "observation": ("Beobachtung", "Observation"),
    "interpretation": ("Einordnung", "Interpretation"),
    "action": ("Nächster Versuch", "Next attempt"),
    "check": ("So prüfst du es", "How to check"),
    "causes": ("Mögliche Ursachen", "Possible causes"),
    "facts": ("Zitierte Messwerte", "Cited measurements"),
    "telemetry": ("Runde in der Telemetrie öffnen", "Open lap in telemetry"),
    "technical": (
        "Datenqualität und technische Hinweise",
        "Data quality and technical notes",
    ),
    "limitations": ("Grenzen der Auswertung", "Limits of this review"),
    "provenance": (
        "Datenherkunft und technische Kennungen",
        "Provenance and technical identifiers",
    ),
    "legacy": (
        "Historischer Bericht im bisherigen Format. Die ursprünglichen Aussagen bleiben erhalten; die Diagramme stammen aus dem gespeicherten Datenpaket.",
        "Historical report in the previous format. Original findings are preserved; charts use the saved input package.",
    ),
    "empty": (
        "Die Daten tragen derzeit keinen konkreten Fahrhinweis. Prüfe die Grenzen der Auswertung.",
        "The data do not support a specific driving finding. Check the limits of this review.",
    ),
    "priority": ("Priorität", "Priority"),
    "selected": ("Verwendbare Runden", "Usable laps"),
    "best": ("Beste ausgewählte Runde", "Best selected lap"),
    "spread": ("Streuung der Auswahl", "Selection spread"),
    "stats": (
        "Diese Kennzahlen beschreiben die gesamte Auswahl und können verschiedene Fahrphasen enthalten.",
        "These figures describe the full selection and may include different driving phases.",
    ),
    "model": ("Angefragtes Modell", "Requested model"),
    "session": ("Session", "Session"),
    "measured": (
        "gemessen oder deterministisch berechnet",
        "measured or deterministically calculated",
    ),
}


def render(run, package, kind, origin, fact_label):
    report = run["report"]
    de = text.german(package)
    tr = lambda key: STRINGS[key][0 if de else 1]
    esc = lambda value: html.escape(str(value), quote=True)
    value = lambda v: text.display_value(v, package)
    provenance = {
        k: v for k, v in run.items() if k not in ("report", "diagnostics", "ok")
    }
    provenance["input"] = {
        k: package.get(k)
        for k in (
            "version",
            "schema_version",
            "report_schema_version",
            "calculation_version",
            "source",
            "context",
            "reference_provenance",
            "track_definition_snapshots",
            "shift_reference_snapshots",
            "template",
            "template_version",
            "goal",
            "questions",
            "language",
            "limitations",
        )
    }

    def link(item):
        return (
            (origin or "")
            + "/telemetry?"
            + urlencode(
                dict(
                    {k: v for k, v in item["telemetry"].items() if v is not None},
                    profile=run["profile"],
                )
            )
            + "#telemetry"
        )

    def label(evidence):
        return fact_label(evidence["fact_id"], package)

    # Legacy content is preserved. System boilerplate receives the current translation.
    limits = [text.limitation(v, package) for v in report.get("limitations", [])]
    if kind == "markdown":
        from ai_reports import _markdown as md

        out = [
            "# Racecast · " + tr("title"),
            "",
            tr("intro"),
            "",
            md(report.get("summary", tr("legacy"))),
            "",
        ]
        for section in ("findings", "exercises"):
            if section == "exercises" and not report[section]:
                continue
            out += ["## " + tr(section), ""]
            for item in report[section]:
                out += [
                    "### "
                    + tr("priority")
                    + " "
                    + str(item["priority"])
                    + " · "
                    + md(item.get("title", item.get("action"))),
                    "",
                ]
                for field in ("observation", "interpretation", "action", "check"):
                    if item.get(field) and not (
                        section == "exercises" and field == "action"
                    ):
                        out += [tr(field) + ": " + md(item[field]), ""]
                if item.get("possible_causes"):
                    out += [
                        tr("causes") + ": " + md("; ".join(item["possible_causes"])),
                        "",
                    ]
                out += [
                    "[" + tr("telemetry") + "](" + link(item) + ")",
                    "",
                    tr("facts") + ":",
                    "",
                ]
                out += [
                    "- " + md(label(e)) + ": " + md(value(e["value"]))
                    for e in item["evidence"]
                ]
                out += [""]
        out += ["## " + tr("technical"), ""] + [
            "- " + md(v) for v in report.get("technical_notes", [])
        ]
        out += ["", "## " + tr("limitations"), ""] + ["- " + md(v) for v in limits]
        out += [
            "",
            "## " + tr("provenance"),
            "",
            "```json",
            json.dumps(provenance, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        return "\n".join(out)
    css = """
:root{color-scheme:dark;--bg:#101827;--panel:#192437;--line:#34435a;--text:#e8eef7;--muted:#afbed1;--cyan:#22d3ee}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:36px 28px 80px}h1{font-size:clamp(30px,4vw,46px);line-height:1.15;margin:12px 0 20px}h2{margin:40px 0 16px;font-size:25px}h3{line-height:1.4;margin:0 0 12px}p{margin:10px 0}a{color:var(--cyan)}.eyebrow,.muted,figcaption{color:var(--muted)}.lead{font-size:20px;max-width:960px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:28px 0 12px}.metric,article,figure{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px}.metric strong{display:block;font-size:28px}.metric span{color:var(--muted);font-size:14px}article{margin:16px 0;border-left:4px solid var(--cyan)}.practice{border-left-color:#a78bfa}.priority{color:var(--cyan);font-size:13px;font-weight:700;letter-spacing:.04em}.step{display:grid;grid-template-columns:160px 1fr;gap:18px;margin:14px 0}.step strong{color:var(--muted);font-size:14px}.step p{margin:0}.action{padding:14px;background:#113843;border-radius:6px}.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}figure{margin:0;min-width:0}figure.wide{margin:20px 0}figure h3{font-size:17px}svg{display:block;width:100%;height:auto;overflow:visible}svg text{fill:var(--muted);font:12px system-ui}.gridline{stroke:var(--line);stroke-width:1}figcaption{font-size:13px;line-height:1.5;margin-top:12px}.legend{display:flex;flex-wrap:wrap;gap:16px}.legend i{display:inline-block;width:16px;height:3px;vertical-align:middle;margin-right:7px}.trace-review{margin:30px 0 44px}.trace-review>p{color:var(--muted);max-width:960px}details{border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:16px 0}summary{cursor:pointer;font-weight:600}li{margin:8px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:480px;overflow:auto}td{padding:8px;border-bottom:1px solid var(--line);vertical-align:top}td:last-child{white-space:nowrap}table{width:100%;font-size:14px}article,details,p,td{overflow-wrap:anywhere}nav{display:flex;gap:22px;flex-wrap:wrap}
@media(max-width:700px){.lead{font-size:17px;line-height:1.55}svg text{font-size:18px}main{padding:24px 14px 50px}.chart-grid{grid-template-columns:1fr}.metrics{gap:8px}.metric{padding:12px}.metric strong{font-size:22px}.metric span{font-size:12px}.step{grid-template-columns:1fr;gap:4px}article{padding:16px}figure{padding:12px}h2{font-size:22px}}
@media print{svg path[stroke="#22d3ee"]{stroke:#006879}svg path[stroke="#a8b3c7"]{stroke:#596579}svg circle[fill="#22d3ee"]{fill:#006879}svg circle[fill="#fbbf24"]{fill:#805300}:root{color-scheme:light;--bg:#fff;--panel:#fff;--text:#111;--muted:#444;--line:#bbb;--cyan:#006879}main{padding:0;max-width:none}body{font-size:10pt}h1{font-size:26pt}h2{font-size:17pt}.lead{font-size:13pt}.action{background:#e9f6f8}.chart-grid{grid-template-columns:1fr 1fr}article,figure{break-inside:avoid}nav,details{display:none}svg text{font-size:12px}a{color:#006879}h2,h3{break-after:avoid}}
"""
    out = [
        '<!doctype html><html lang="'
        + ("de" if de else "en")
        + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>Racecast · '
        + tr("title")
        + "</title><style>"
        + css
        + "</style></head><body><main>",
        '<header><div class="eyebrow">RACECAST · '
        + tr("session")
        + " "
        + esc(package.get("selection", {}).get("session", ""))
        + "</div><h1>"
        + tr("title")
        + '</h1><p class="lead">'
        + esc(report.get("summary", tr("legacy")))
        + '</p><p class="muted">'
        + tr("intro")
        + '</p><nav><a href="#findings">'
        + tr("findings")
        + '</a><a href="#charts">'
        + ("Diagramme" if de else "Charts")
        + "</a>"
        + (
            '<a href="#practice">' + tr("exercises") + "</a>"
            if report["exercises"]
            else ""
        )
        + "</nav></header>",
    ]
    stats = package.get("summary", {})
    out.append('<div class="metrics">')
    for key, title, suffix in (
        ("usable_laps", "selected", ""),
        ("best_time_s", "best", " s"),
        ("population_stddev_s", "spread", " s"),
    ):
        out.append(
            '<div class="metric"><strong>'
            + esc(value(stats.get(key, "?")))
            + suffix
            + "</strong><span>"
            + tr(title)
            + "</span></div>"
        )
    out += ['</div><p class="muted">' + tr("stats") + "</p>"]
    for section, anchor in (("findings", "findings"), ("exercises", "practice")):
        if section == "exercises" and not report[section]:
            continue
        out.append('<section id="' + anchor + '"><h2>' + tr(section) + "</h2>")
        if not report[section] and section == "findings":
            out.append("<p>" + tr("empty") + "</p>")
        for item in report[section]:
            out.append(
                "<article"
                + (' class="practice"' if section == "exercises" else "")
                + '><div class="priority">'
                + tr("priority")
                + " "
                + str(item["priority"])
                + "</div><h3>"
                + esc(item.get("title", item.get("action")))
                + "</h3>"
            )
            for field in ("observation", "interpretation", "action", "check"):
                if item.get(field) and not (
                    section == "exercises" and field == "action"
                ):
                    out.append(
                        '<div class="step'
                        + (" action" if field == "action" else "")
                        + '"><strong>'
                        + tr(field)
                        + "</strong><p>"
                        + esc(item[field])
                        + "</p></div>"
                    )
            if item.get("possible_causes"):
                out.append(
                    '<p class="muted">'
                    + tr("causes")
                    + ": "
                    + esc("; ".join(item["possible_causes"]))
                    + "</p>"
                )
            out.append(
                '<a href="'
                + esc(link(item))
                + '">'
                + tr("telemetry")
                + "</a><details><summary>"
                + tr("facts")
                + "</summary><table>"
            )
            for e in item["evidence"]:
                out.append(
                    '<tr><td title="'
                    + esc(e["fact_id"])
                    + '">'
                    + esc(label(e))
                    + "</td><td>"
                    + esc(value(e["value"]))
                    + "</td></tr>"
                )
            out.append(
                '</table><p class="muted">'
                + tr("measured")
                + "</p></details></article>"
            )
        out.append("</section>")
        if section == "findings":
            out.append(charts.render(package, report))
    out.append("<details><summary>" + tr("technical") + "</summary><ul>")
    out += ["<li>" + esc(v) + "</li>" for v in report.get("technical_notes", [])]
    out += (
        ["</ul><h3>" + tr("limitations") + "</h3><ul>"]
        + ["<li>" + esc(v) + "</li>" for v in limits]
        + ["</ul></details>"]
    )
    out.append(
        "<details><summary>"
        + tr("provenance")
        + "</summary><pre>"
        + esc(json.dumps(provenance, ensure_ascii=False, indent=2))
        + "</pre></details></main></body></html>"
    )
    return "\n".join(out)
