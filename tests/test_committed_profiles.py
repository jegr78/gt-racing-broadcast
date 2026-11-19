"""The profiles committed to git never carry a credential."""
import os, re, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_KEYS = ("SHEET_PUSH_URL", "CONSOLE_SECRET", "DISCORD_WEBHOOK_URL", "DISCORD_CLIENT_SECRET",
               "TIMING_HOST_API_TOKEN", "TIMING_HOST_PUBLISH_KEY")
FILLED = re.compile(r"^[ \t]*(" + "|".join(SECRET_KEYS) + r")[ \t]*=[ \t]*\S", re.MULTILINE)


def _git(*args):
    return subprocess.run([shutil.which("git"), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def _tracked_profiles():
    """{path: staged text} of the profile.env files git tracks, or None without a checkout.
    The staged text, not the file: a running relay writes CONSOLE_SECRET into the file."""
    if not shutil.which("git") or not os.path.exists(os.path.join(ROOT, ".git")):
        return None
    return {rel: _git("show", f":{rel}") for rel in _git("ls-files", "profiles/*/profile.env").split()}


def _filled(text):
    return [m.group(1) for m in FILLED.finditer(text)]


def t_a_filled_secret_is_detected():
    assert _filled("CONSOLE_SECRET=abc\n# SHEET_PUSH_URL=https://x\nDISCORD_WEBHOOK_URL=\n") == \
        ["CONSOLE_SECRET"], "a filled key counts, a comment and a blank key do not"


def t_committed_profiles_carry_no_secret():
    files = _tracked_profiles()
    if files is None:
        print("  (no git checkout, check skipped)")
        return
    assert files, "the shipped profiles are tracked"
    for rel, text in files.items():
        found = _filled(text)
        assert not found, f"{rel} commits a credential in {found}; a relay run writes CONSOLE_SECRET there"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
