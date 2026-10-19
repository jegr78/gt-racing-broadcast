"""Durable refresh staging removes consumed credentials before provider exchange."""

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
        assert loaded and loaded[1] == {"phase": "refreshing"}, loaded
        assert "fixture-private" not in files.secret.read_text(), (
            "Consumed credentials survived staging"
        )
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
                pass
            else:
                raise AssertionError("Mismatched stage accepted")
            assert files.secret.read_bytes() == before
        files.stage_refresh(public["connectionId"])
        try:
            files.stage_refresh(public["connectionId"])
        except StorageError:
            pass
        else:
            raise AssertionError("Already staged one-use refresh accepted twice")


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
