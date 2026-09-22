#!/usr/bin/env python3
"""Second entrypoint: the windowed Control Center launcher (the `racecast-ui`
binary). Producers double-click it — there is no terminal. It runs the same
server as `racecast ui` via racecast.run_ui(), but a fatal startup error (port taken /
bind failure) is shown in a NATIVE dialog instead of being written to a console
that does not exist. Jobs still spawn the sibling `racecast` binary (see
racecast._rc_job_executable). Spec: docs/superpowers/specs/2026-06-07-control-center-design.md."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)            # import the sibling racecast module

import racecast                         # noqa: E402 — after the path insert
import native_dialog                    # noqa: E402 — from scripts/ (racecast added it to sys.path)


def _fatal(message):
    """Show the message natively, then exit non-zero (no console to print to)."""
    native_dialog.notify(message)
    raise SystemExit(1)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Exactly the same startup as the `racecast` CLI — one shared _bootstrap, so the
    # windowed launcher can never again skip a step the CLI runs (it once shipped
    # without the tool-PATH fix #46, then without the active-profile env injection
    # #54). _bootstrap handles _app_home/_real_executable (resolving a macOS .app out
    # of any App-Translocation mount, #22), the .env + example-profile seeding, the
    # frozen env + SSL certs, the tool PATH, and the active profile's league env.
    try:
        argv = racecast._bootstrap(argv)
    except ValueError as exc:
        _fatal(f"racecast: {exc}")
    try:
        racecast.run_ui(argv, fail=_fatal,
                   open_browser="--no-browser" not in argv)
    except SystemExit as exc:
        # belt-and-suspenders: a string exit code means a fatal message slipped
        # through as text — surface it natively too.
        if isinstance(exc.code, str):
            _fatal(exc.code)
        raise


if __name__ == "__main__":
    main()
