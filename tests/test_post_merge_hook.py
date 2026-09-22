"""Unit tests for .claude/hooks/post_merge_reminder.py: which command strings
count as a merge. Stdlib only; runnable as a script (repo convention).

The hook injects "a merge to main just completed" into the transcript, so a
false positive tells Claude to do a security follow-up for a merge that never
happened.
"""
import importlib.util
import io
import json
import os
import sys

_HOOK = os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks",
                     "post_merge_reminder.py")
_spec = importlib.util.spec_from_file_location("post_merge_reminder", _HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def _fires(command):
    """Run main() against a payload carrying `command`; True if it emitted."""
    stdin, stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps({"tool_input": {"command": command}}))
    sys.stdout = io.StringIO()
    try:
        assert hook.main() == 0
        return "hookSpecificOutput" in sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = stdin, stdout


def t_fires_on_a_bare_merge():
    assert _fires("gh pr merge 19 --squash --delete-branch")


def t_fires_after_a_shell_separator():
    assert _fires("cd /repo && gh pr merge 19 --squash")
    assert _fires("set -e; gh pr merge 19")
    assert _fires("echo starting\ngh pr merge 19")


def t_does_not_fire_when_the_phrase_is_merely_quoted():
    assert not _fires('echo "{\\"command\\":\\"gh pr merge 19\\"}" | cat')
    assert not _fires('grep -rn "gh pr merge" .claude/')


def t_does_not_fire_on_an_unrelated_command():
    assert not _fires("ls -la")
    assert not _fires("git merge main")


def t_does_not_fire_on_a_heredoc_body():
    # Shell syntax does not apply inside a heredoc, so anchoring to a separator
    # cannot help here.
    assert not _fires("git commit -F - <<'MSG'\nSee `cd x && gh pr merge`.\nMSG")
    assert not _fires('gh pr create --body-file - <<"BODY"\n'
                      "set -e; gh pr merge 1\nBODY")


def t_still_fires_for_a_command_after_the_heredoc_ends():
    assert _fires("cat <<'EOF'\nnothing to see\nEOF\ngh pr merge 19"), \
        "only the heredoc BODY is dropped, not the rest of the command"


def t_an_arithmetic_shift_does_not_open_a_heredoc():
    assert _fires("python3 -c 'print(1 << 3)'\ngh pr merge 19")


def t_survives_a_payload_it_cannot_read():
    stdin, stdout = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO("not json"), io.StringIO()
    try:
        assert hook.main() == 0
        assert sys.stdout.getvalue() == ""
    finally:
        sys.stdin, sys.stdout = stdin, stdout
    assert not _fires("")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("all post_merge_hook tests passed")
