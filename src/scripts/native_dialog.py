"""Show a fatal startup message to a user with no terminal: the windowed
`racecast-ui` binary has no console to print to. Pure command builders plus a
fully injected dispatcher, so tests pass fakes and nothing touches the system.
Used by src/racecast_ui.py. Tests: tests/test_native_dialog.py."""
import subprocess
import sys

TITLE = "racecast Control Center"


def osascript_argv(message):
    """macOS: an `osascript -e 'display dialog ...'` argv. Double quotes in the
    message are neutralised so it cannot break out of the AppleScript string."""
    safe = message.replace('"', "'")
    return ["osascript", "-e",
            f'display dialog "{safe}" buttons {{"OK"}} default button "OK" '
            f'with icon stop with title "{TITLE}"']


def _win_msgbox(message):
    """Windows: a modal MessageBox via user32 (0x10 = MB_ICONERROR).
    ctypes is imported lazily because ctypes.windll only exists on Windows, so a
    module-level import would read as cross-platform when it is not."""
    import ctypes  # noqa: PLC0415
    ctypes.windll.user32.MessageBoxW(0, message, TITLE, 0x10)


def notify(message, platform=sys.platform, run=subprocess.call, msgbox=None):
    """Surface `message` natively for the current OS: darwin -> osascript,
    win32 -> MessageBoxW, anything else -> stderr, the only safe fallback.
    `run` and `msgbox` are injected for tests."""
    if platform == "darwin":
        run(osascript_argv(message))
    elif platform.startswith("win"):
        (msgbox or _win_msgbox)(message)
    else:
        print(message, file=sys.stderr)
