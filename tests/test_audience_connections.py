"""Private provider credentials and portable public targets are committed safely."""

import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
import audience_connections as ac

KEY = "fixture-private-key-never-export"


def pair(root, writer=None, owner="demo"):
    return ac.ConnectionFiles(
        root / "profiles/demo/audience-youtube.json",
        root / "runtime/demo/audience-youtube.json",
        owner,
        writer=writer,
    )


def t_activation_pairs_public_targets_with_private_profile_credentials():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = pair(root)
        files.activate({"video": {"id": "abcdefghijk"}}, {"key": KEY})
        loaded = files.load()
        assert loaded and loaded[0]["video"]["id"] == "abcdefghijk", loaded
        assert loaded[1] == {"key": KEY}, loaded
        assert KEY not in files.public.read_text(encoding="utf-8")
        assert pair(root, owner="different-profile").load() is None
        if os.name != "nt":
            assert files.secret.stat().st_mode & 0o777 == 0o600
        assert not list(files.secret.parent.glob(".audience-*.tmp"))


def t_failed_public_commit_restores_old_private_bytes_and_selection():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = pair(root)
        files.activate({"video": {"id": "old"}}, {"key": KEY})
        before = (files.public.read_bytes(), files.secret.read_bytes())

        def fail_public(path, body, private=False):
            if path == files.public:
                raise OSError(KEY)
            ac.write_atomic(path, body, private=private)

        try:
            pair(root, writer=fail_public).activate(
                {"video": {"id": "new"}}, {"key": "new"}
            )
        except ac.StorageError as error:
            assert KEY not in str(error)
        else:
            raise AssertionError("Incomplete connection commit accepted")
        assert (files.public.read_bytes(), files.secret.read_bytes()) == before
        assert files.load()[0]["video"]["id"] == "old"


def t_failed_rollback_cannot_reuse_a_mismatched_key_after_restart():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = pair(root)
        files.activate({"video": {"id": "old"}}, {"key": KEY})
        writes = []

        def fail_public_and_rollback(path, body, private=False):
            writes.append(path)
            if path == files.public or len(writes) > 2:
                raise OSError("fixture disk failure")
            ac.write_atomic(path, body, private=private)

        try:
            pair(root, writer=fail_public_and_rollback).activate(
                {"video": {"id": "new"}}, {"key": "new"}
            )
        except ac.StorageError:
            pass  # The old public connection must never pair with the uncommitted key.
        else:
            raise AssertionError("Failed rollback accepted")
        assert files.read_public()["video"]["id"] == "old"
        assert pair(root).load() is None


def t_partial_disconnect_reports_failure_and_prevents_startup_revival():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = pair(root)
        files.activate({"video": {"id": "old"}}, {"key": KEY})

        def fail_public(path, body, private=False):
            if path == files.public:
                raise OSError("fixture public write failure")
            ac.write_atomic(path, body, private=private)

        try:
            pair(root, writer=fail_public).disconnect()
        except ac.StorageError as error:
            assert error.durable is True
        else:
            raise AssertionError("Partial disconnect was reported as fully saved")
        assert not files.secret.exists() and pair(root).load() is None


def t_profile_export_contains_targets_and_never_runtime_credentials():
    import profile_io
    import zipfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        files = pair(root)
        files.activate({"video": {"id": "abcdefghijk"}}, {"key": KEY})
        (files.public.parent / "profile.env").write_text(
            "NAME=Demo Racing\n", encoding="utf-8"
        )
        dest = profile_io.export_profile(
            "demo",
            {"profile_dir": str(files.public.parent)},
            True,
            str(root / "export.zip"),
        )
        with zipfile.ZipFile(dest) as archive:
            assert (
                json.loads(archive.read("profile/audience-youtube.json"))["video"]["id"]
                == "abcdefghijk"
            )
            assert all(
                KEY.encode() not in archive.read(name) for name in archive.namelist()
            )


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
