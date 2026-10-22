"""Durable refresh staging marks the pair as refreshing before provider exchange."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "scripts"))
from audience_connections import StorageError
from audience_twitch_storage import CredentialFiles


def t_phase_is_private_atomic_and_bound_to_the_current_connection():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = CredentialFiles(
            root / "profiles/demo/twitch.json",
            root / "runtime/demo/twitch.json",
            "demo",
        )
        public = files.activate(
            {"target": {"id": "123"}},
            {"phase": "ready", "pair": {"refresh": "fixture-private"}},
        )
        before = files.public.read_bytes()
        files.stage_refresh(public["connectionId"])
        assert files.public.read_bytes() == before
        loaded = files.load()
        assert loaded and loaded[1] == {
            "phase": "refreshing",
            "pair": {"refresh": "fixture-private"},
        }, loaded  # The pair stays so a restart can resume the rotation.
        data = json.loads(files.secret.read_text())
        assert (
            data["profile"] == "demo" and data["connectionId"] == public["connectionId"]
        )
        if sys.platform != "win32":
            assert files.secret.stat().st_mode & 0o777 == 0o600


def t_mismatched_disabled_or_already_staged_pair_is_rejected_before_any_write():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = CredentialFiles(root / "public.json", root / "private.json", "demo")
        public = files.activate({}, {"phase": "ready", "pair": {"refresh": "fixture"}})
        for ident in ("f" * 32, None):
            before = files.secret.read_bytes()
            try:
                files.stage_refresh(ident)
            except StorageError:
                pass  # Rejected staging must leave private credentials unchanged.
            else:
                raise AssertionError("Mismatched stage accepted")
            assert files.secret.read_bytes() == before
        files.stage_refresh(public["connectionId"])
        try:
            files.stage_refresh(public["connectionId"])
        except StorageError:
            pass  # A consumed refresh phase cannot be staged again.
        else:
            raise AssertionError("Already staged one-use refresh accepted twice")


def t_failed_public_commit_rolls_back_to_the_staged_pair():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = CredentialFiles(root / "public.json", root / "private.json", "demo")
        public = files.activate({}, {"phase": "ready", "pair": {"refresh": "fixture"}})
        files.stage_refresh(public["connectionId"])
        original = files.writer

        def failed_public(path, body, **kwargs):
            if Path(path) == files.public:
                raise OSError("fixture-disk-error")
            return original(path, body, **kwargs)

        files.writer = failed_public
        try:
            files.activate({}, {"phase": "ready", "pair": {"refresh": "fixture-new"}})
        except StorageError:
            pass  # The failed public commit must roll the private file back.
        else:
            raise AssertionError("Failed public commit reported success")
        assert files.load()[1] == {
            "phase": "refreshing",
            "pair": {"refresh": "fixture"},
        }, "Rollback lost the staged refreshing phase or its pair"


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
