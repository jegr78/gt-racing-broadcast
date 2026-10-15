"""Report presentation strings for the supported English and German languages."""

import math

# Source identifiers remain unchanged in the inspectable provenance section.
DE_LIMITATIONS = {
    "Interpretations are hypotheses; deterministic validation cannot establish narrative truth.": "Deutungen und mögliche Ursachen bleiben Hypothesen. Geprüft werden die zitierten Messwerte, nicht die fahrerische Erklärung.",
    "No direct tyre wear, setup, power or weather measurements; unknown channels cannot support diagnoses.": "Reifenverschleiß, Setup, Leistung und Wetter werden nicht direkt gemessen. Daraus lassen sich keine sicheren Diagnosen ableiten.",
    "Selection statistics may mix phases; use contextual comparison eligibility for coaching.": "Die Gesamtauswahl kann verschiedene Fahrphasen enthalten. Fahrhinweise benötigen einen passenden Vergleichskontext.",
    "Trace intervals are diagnostics; do not sum them into guaranteed recoverable time.": "Zeitunterschiede in einzelnen Abschnitten sind Beobachtungen und kein garantierter Zeitgewinn.",
    "No external reference selected; no ideal line or optimal braking claims.": "Es wurde keine externe Referenz gewählt. Die gemessenen Linien und Bremspunkte sind keine Idealvorgaben.",
    "Technically unusable selected laps were excluded.": "Technisch unbrauchbare Runden wurden ausgeschlossen.",
    "Recorder reported dropped packets.": "Der Recorder meldet verlorene Datenpakete.",
    "Unknown track; cross-recording comparison is unavailable.": "Die Strecke ist unbekannt. Ein Vergleich verschiedener Aufzeichnungen ist nicht verfügbar.",
    "Optional shift detail unavailable for selected lap": "Die ergänzende Schaltanalyse ist für eine ausgewählte Runde nicht verfügbar.",
    "Capture quality needs review; inspect lap quality and gaps.": "Die Erfassungsqualität muss anhand der Rundenqualität und Datenlücken geprüft werden.",
    "Race settings unconfirmed": "Die Rennbedingungen wurden nicht bestätigt.",
    "Tyre compound unconfirmed": "Die Reifenmischung wurde nicht bestätigt.",
    "Mixed fuel strategy within lap": "Innerhalb der Runde wurden unterschiedliche Spritsparziele angegeben.",
    "Mixed compounds during service": "Beim Service sind mehrere Reifenmischungen angegeben.",
    "Service time outside recorded lap": "Der angegebene Servicezeitpunkt liegt außerhalb der aufgezeichneten Runde.",
    "Strategy time outside recorded lap": "Der angegebene Strategiezeitpunkt liegt außerhalb der aufgezeichneten Runde.",
    "Manual service has no matching capture inference": "Der manuell angegebene Service wurde in der Aufzeichnung nicht entsprechend erkannt.",
    "Inferred service has no matching confirmed tyre-service decision": "Für den erkannten Service fehlt eine bestätigte Angabe zum Reifenwechsel.",
    "Compound change contradicts no tyre change": "Die geänderte Reifenmischung widerspricht der Angabe, dass kein Reifenwechsel stattfand.",
    "Manual event reference belongs to a different vehicle": "Die manuelle Schaltreferenz gehört zu einem anderen Fahrzeug.",
}
DE_FACTS = {
    "time_s": "Rundenzeit (s)",
    "gt7_time_s": "GT7-Rundenzeit (s)",
    "relay_time_s": "Empfangszeit der Runde (s)",
    "fuel_used_l": "Kraftstoffverbrauch (L)",
    "fuel_start_l": "Kraftstoff zu Rundenbeginn (L)",
    "fuel_end_l": "Kraftstoff am Rundenende (L)",
    "top_speed_kmh": "Höchstgeschwindigkeit (km/h)",
    "tyre_avg_c": "Mittlere Reifentemperatur (°C)",
    "length_m": "Rundenlänge (m)",
    "delta_s": "Beobachteter Zeitunterschied (s)",
    "mean_time_s": "Mittlere Rundenzeit der Auswahl (s)",
    "best_time_s": "Beste Rundenzeit der Auswahl (s)",
    "population_stddev_s": "Streuung der Rundenzeiten (s)",
    "selected_gt7_total_s": "Summe der ausgewählten GT7-Rundenzeiten (s)",
    "usable_laps": "Verwendbare Runden",
    "excluded_laps": "Ausgeschlossene Runden",
    "entry_m": "Abschnittsbeginn (m)",
    "apex_m": "Definierter Kurvenanker (m)",
    "exit_m": "Abschnittsende (m)",
    "entry_speed_kmh": "Geschwindigkeit am Abschnittsbeginn (km/h)",
    "exit_speed_kmh": "Geschwindigkeit am Abschnittsende (km/h)",
    "anchor_speed_kmh": "Geschwindigkeit am Kurvenanker (km/h)",
    "min_speed_kmh": "Mindestgeschwindigkeit (km/h)",
    "max_brake_pct": "Maximale Bremsbetätigung (%)",
    "brake_first_m": "Erste erfasste Bremsbetätigung ab 5 % (m)",
    "brake_last_m": "Letzte erfasste Bremsbetätigung ab 5 % (m)",
    "full_throttle_after_min_m": "Erste Gasstellung ab 95 % nach dem Geschwindigkeitsminimum (m)",
    "mean_throttle_pct": "Mittlere Gasstellung (%)",
    "mean_abs_steering_deg": "Mittlerer absoluter Lenkwinkel (°)",
    "peak_abs_steering_deg": "Größter absoluter Lenkwinkel (°)",
    "min_speed_m": "Position des Geschwindigkeitsminimums (m)",
    "start_m": "Abschnittsbeginn (m)",
    "end_m": "Abschnittsende (m)",
    "rpm": "Drehzahl (U/min)",
}
EN_FACTS = {
    "brake_first_m": "First captured brake input at or above 5% (m)",
    "brake_last_m": "Last captured brake input at or above 5% (m)",
    "full_throttle_after_min_m": "First throttle at or above 95% after minimum speed (m)",
    "mean_throttle_pct": "Mean throttle (%)",
    "mean_abs_steering_deg": "Mean absolute steering angle (deg)",
    "peak_abs_steering_deg": "Peak absolute steering angle (deg)",
    "apex_m": "Defined corner anchor (m)",
}


def german(package):
    return str(package.get("language", "")).lower().startswith("de")


def limitation(value, package):
    return DE_LIMITATIONS.get(value, value) if german(package) else value


def display_value(value, package=None):
    if type(value) in (int, float) and math.isfinite(value):
        text = (
            str(value) if type(value) is int else f"{value:.3f}".rstrip("0").rstrip(".")
        )
        return text.replace(".", ",") if german(package or {}) else text
    return str(value)
