"""First-time setup wizard logic behind `racecast init`.

Pure building blocks wired by racecast.py: the ordered step plan, done-detection
predicates (every probe is injected, so tests never touch the system), the gate
pause (interactive vs non-TTY checkpoint-and-exit), the wizard loop, and the
closing manual-next-steps text. The wizard only orchestrates the existing
one-shots; it owns no install/download logic.
Spec: docs/superpowers/specs/2026-06-06-racecast-init-design.md.
Tests: tests/test_init.py."""

STEP_ORDER = ("profile", "env", "install-tools", "install-apps", "cookies",
              "graphics", "media", "setup", "export-companion", "preflight")
INSTALL_STEPS = ("install-tools", "install-apps")
STEP_LABELS = {
    "profile": "profile (league)",
    "env": ".env (machine)",
    "install-tools": "install-tools",
    "install-apps": "install-apps",
    "cookies": "cookies",
    "graphics": "graphics",
    "media": "media",
    "setup": "setup (OBS collection)",
    "export-companion": "export companion",
    "preflight": "preflight",
}

# Per-step UI execution kind, consumed by the Control Center wizard
# (racecast.init_plan_data). Three kinds:
#   "job"    -> the UI runs it through the existing job machine (/api/op/<op>),
#               streaming live output; "op" is the ui_ops.OPS name.
#   "gate"   -> a manual, probe-verified checkpoint the UI re-checks
#               (POST /api/init/step/<key>); no subprocess.
#   "action" -> a quick in-process action the UI runs structured
#               (POST /api/init/step/<key>).
# "instruction" (optional) is the operator-facing text shown before the step;
# "{browser}" is substituted by the wizard for the cookies step.
STEP_KINDS = {
    "profile": {"kind": "gate", "op": None,
                "instruction": "Create or select a league profile and set its "
                               "SHEET_ID (profiles/<name>/profile.env). Then re-check. "
                               "For a zero-config smoke test, run "
                               "`racecast profile use demo`. The shipped demo "
                               "league points at a public read-only Sheet."},
    "env": {"kind": "action", "op": None},
    "install-tools": {"kind": "job", "op": "install-tools"},
    "install-apps": {"kind": "job", "op": "install-apps"},
    "cookies": {"kind": "job", "op": "cookies",
                "instruction": "Log in to YouTube in {browser} first. The "
                               "cookie export reads that browser's session."},
    "graphics": {"kind": "job", "op": "graphics"},
    "media": {"kind": "job", "op": "media"},
    "setup": {"kind": "job", "op": "setup"},
    "export-companion": {"kind": "action", "op": None},
    "preflight": {"kind": "job", "op": "preflight"},
}

_USAGE = "usage: racecast init [--browser NAME] [--skip-installs] [--force]"


def parse_init_args(rest):
    """argv after `init` -> {"browser", "skip_installs", "force"}.
    Raises ValueError (with usage text) on anything unknown."""
    opts = {"browser": "firefox", "skip_installs": False, "force": False}
    toks = list(rest)
    while toks:
        tok = toks.pop(0)
        if tok == "--browser" and toks:
            opts["browser"] = toks.pop(0)
        elif tok.startswith("--browser="):
            opts["browser"] = tok.split("=", 1)[1]
        elif tok == "--skip-installs":
            opts["skip_installs"] = True
        elif tok == "--force":
            opts["force"] = True
        else:
            raise ValueError(_USAGE)
    if not opts["browser"]:
        raise ValueError(_USAGE)
    return opts


def build_plan(skip_installs=False):
    """Ordered step keys for this run (--skip-installs drops steps 2-3)."""
    return [k for k in STEP_ORDER
            if not (skip_installs and k in INSTALL_STEPS)]


# Done-detection: each predicate returns the skip-reason string when the step
# is already done, or None when it must run. All probes are injected.

def profile_done(active, sheet_id):
    """The profile step is done when a league profile is active and its SHEET_ID
    is filled in. `active` is the active profile name (or None); `sheet_id` its
    SHEET_ID value (or '')."""
    if active and sheet_id:
        return f"profile '{active}' ready"
    return None


def prompt_value(message, isatty, ask=input):
    """Collect one line at a manual step. Interactive: return the stripped
    answer. Non-TTY (CI/pipe): degrade to checkpoint-and-exit (same contract as
    gate_pause)."""
    if not isatty:
        raise SystemExit(f"{message}\nThen run `racecast init` again.")
    return ask(f"{message}: ").strip()


def tools_done(which, tools):
    """`which` is a shutil.which-like lookup; `tools` the required names."""
    if all(which(t) for t in tools):
        return "all tools on PATH"
    return None


def apps_done(present, apps):
    """`present(app) -> bool` (install_apps.app_present partial)."""
    if all(present(a) for a in apps):
        return "all apps installed"
    return None


def cookies_done(level, detail):
    """level/detail from preflight.cookies_status(): PASS means fresh, with
    logged-in markers found. Anything else (missing/stale/anonymous) runs."""
    return f"yt-cookies.txt {detail}" if level == "PASS" else None


def assets_done(missing, count):
    """`missing` is event.check_assets()' list when the sheet was readable,
    or None when it was not, in which case the step runs and produces the real,
    actionable error itself (spec: probe failure counts as not done)."""
    if missing == []:
        return f"complete ({count} file(s))"
    return None


def setup_done(out_mtime, dep_mtimes):
    """Import JSON freshness: done iff it exists (out_mtime is not None) and
    is strictly newer than every existing dependency (collection template,
    .env). Mtimes are None for absent files."""
    if out_mtime is None:
        return None
    if any(d is not None and d >= out_mtime for d in dep_mtimes):
        return None
    return "import JSON up to date"


def export_done(exists):
    return "config already exported" if exists else None


# Wizard: gates, loop, output. The step dicts are built by racecast.py:
#   {"key": str, "label": str, "done": () -> str|None, "run": () -> int}

def gate_pause(message, isatty, ask=input):
    """A manual gate. Interactive: block until the operator presses Enter.
    Non-TTY (CI/pipe): degrade to checkpoint-and-exit, where SystemExit(str)
    prints the instruction to stderr and exits 1 (Python semantics)."""
    if not isatty:
        raise SystemExit(f"{message}\nThen run `racecast init` again; completed "
                         "steps are skipped.")
    ask(f"{message}. Press Enter to continue: ")


def fmt_step(idx, total, label, verdict):
    return f"[{idx}/{total}] {label} … {verdict}"


def run_wizard(steps, force, echo):
    """Run the plan: skip done steps (unless --force), stop on the first hard
    error. Returns (exit_code, finished), where finished=False means the wizard
    stopped early; a non-zero code from the LAST step (preflight's verdict)
    still counts as finished. Gate SystemExits propagate to the caller."""
    code, total = 0, len(steps)
    for idx, step in enumerate(steps, 1):
        skip = None if force else step["done"]()
        if skip is not None:
            echo(fmt_step(idx, total, step["label"], f"SKIP ({skip})"))
            continue
        echo(fmt_step(idx, total, step["label"], "running"))
        code = step["run"]()
        if code and idx < total:
            echo(f"\nStep '{step['label']}' failed (exit {code}). Fix the "
                 "issue above, then run `racecast init` again; completed steps "
                 "are skipped.")
            return code, False
    return code, True


def manual_next_steps(import_json, companion_cfg):
    """The closing checklist: the things no script can do."""
    return [
        f"Import the OBS scene collection: {import_json} "
        "(OBS: Scene Collection -> Import; do not move the file afterwards).",
        f"Import the Companion button config: {companion_cfg} "
        "(Companion admin GUI: Import / Export -> Import; launch Companion "
        "once first if this is its very first run).",
        "Sign in to Tailscale in the Tailscale app (one-time).",
    ]
