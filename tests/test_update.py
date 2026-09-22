#!/usr/bin/env python3
"""Stdlib checks for the `racecast update` decision helpers. Run: python3 tests/test_update.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "update", os.path.join(ROOT, "src", "scripts", "update.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_parse_version_good():
    assert m.parse_version("v0.1.0") == (0, 1, 0)
    assert m.parse_version("v12.34.56") == (12, 34, 56)


def t_parse_version_bad():
    for bad in ("dev", "", None, "0.1.0", "v1.2", "v1.2.3.4", "va.b.c", "v1.2.x"):
        assert m.parse_version(bad) is None, bad


def t_asset_name_per_platform():
    assert m.asset_name("win32") == "racecast-windows.zip"
    assert m.asset_name("darwin") == "racecast-macos.tar.gz"
    # Linux splits by arch (explicit machine -> host-independent in CI and on ARM):
    assert m.asset_name("linux", "x86_64") == "racecast-linux.tar.gz"
    assert m.asset_name("linux", "amd64") == "racecast-linux.tar.gz"
    assert m.asset_name("linux", "aarch64") == "racecast-linux-arm64.tar.gz"
    assert m.asset_name("linux", "arm64") == "racecast-linux-arm64.tar.gz"


REL = {"tag_name": "v0.2.0",
       "assets": [{"name": "racecast-macos.tar.gz", "browser_download_url": "https://x/m"},
                  {"name": "racecast-windows.zip", "browser_download_url": "https://x/w"}]}


def t_classify_dev_refused():
    # repo mode (not frozen): the verb stays git-pull-only
    assert m.classify(REL, "darwin", "dev") == ("dev", None, None)
    assert m.classify(REL, "darwin", "dev", frozen=False) == ("dev", None, None)


def t_classify_frozen_dev_offers_latest():
    # a locally built frozen binary (version 'dev') jumps to the latest release
    assert m.classify(REL, "darwin", "dev", frozen=True) == ("update", "v0.2.0", "https://x/m")
    assert m.classify(REL, "win32", "dev", frozen=True) == ("update", "v0.2.0", "https://x/w")


def t_classify_frozen_dev_building_window():
    assert m.classify(REL, "linux", "dev", frozen=True) == ("building", "v0.2.0", None)


def t_classify_frozen_dev_bad_tag_is_error():
    assert m.classify({"tag_name": "nightly", "assets": []}, "darwin", "dev", frozen=True)[0] == "error"


def t_classify_up_to_date_equal_and_newer_current():
    assert m.classify(REL, "darwin", "v0.2.0") == ("up-to-date", "v0.2.0", None)
    assert m.classify(REL, "darwin", "v0.3.0") == ("up-to-date", "v0.2.0", None)


def t_classify_update_with_url():
    assert m.classify(REL, "darwin", "v0.1.0") == ("update", "v0.2.0", "https://x/m")
    assert m.classify(REL, "win32", "v0.1.0") == ("update", "v0.2.0", "https://x/w")


def t_classify_building_window():
    assert m.classify(REL, "linux", "v0.1.0") == ("building", "v0.2.0", None), \
        "newer release exists but the platform asset is not uploaded yet"


def t_classify_bad_tag_is_error():
    assert m.classify({"tag_name": "nightly", "assets": []}, "darwin", "v0.1.0")[0] == "error"


def t_swap_plan_posix_inplace():
    assert m.swap_plan("darwin", "/app/racecast", "/tmp/new/racecast") == \
        [("replace", "/tmp/new/racecast", "/app/racecast"), ("chmod", "/app/racecast")]


def t_swap_plan_windows_rename_trick():
    # impl must use ntpath so this is computable when the test runs on macOS/Linux
    plan = m.swap_plan("win32", r"C:\racecast\racecast.exe", r"C:\tmp\racecast.exe")
    assert plan == [("rename", r"C:\racecast\racecast.exe", r"C:\racecast\racecast-old.exe"),
                    ("move", r"C:\tmp\racecast.exe", r"C:\racecast\racecast.exe")]


def t_safe_member():
    assert m.safe_member("racecast") and m.safe_member(".env.example")
    assert m.safe_member("sub/racecast")
    assert not m.safe_member("/etc/passwd")
    assert not m.safe_member("..\\racecast.exe")
    assert not m.safe_member("a/../../b")
    assert not m.safe_member("C:\\evil")
    assert not m.safe_member("")


def t_fetch_latest_parses_json():
    import io, json
    body = json.dumps(REL).encode()
    rel = m.fetch_latest(opener=lambda req, timeout: io.BytesIO(body))
    assert rel["tag_name"] == "v0.2.0"


def t_ui_asset_name_per_platform():
    assert m.ui_asset_name("win32") == "racecast-ui.exe"
    assert m.ui_asset_name("darwin") == "racecast-ui.app"
    assert m.ui_asset_name("linux") == "racecast-ui"


# install_ui places the sibling racecast-ui next to the racecast binary.
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def t_install_ui_moves_file():
    import tempfile
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tgt:
        _write(os.path.join(src, "racecast-ui"), "new")
        dst = m.install_ui(src, tgt, "linux")
        assert dst == os.path.join(tgt, "racecast-ui")
        assert os.path.isfile(dst)
        assert not os.path.exists(os.path.join(src, "racecast-ui"))


def t_install_ui_overwrites_existing_file():
    import tempfile
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tgt:
        _write(os.path.join(src, "racecast-ui"), "new")
        _write(os.path.join(tgt, "racecast-ui"), "old")
        m.install_ui(src, tgt, "linux")
        with open(os.path.join(tgt, "racecast-ui"), encoding="utf-8") as fh:
            assert fh.read() == "new"


def t_install_ui_app_bundle_overwrites_dir():
    import tempfile
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tgt:
        _write(os.path.join(src, "racecast-ui.app", "Contents", "MacOS", "racecast-ui"), "new")
        _write(os.path.join(tgt, "racecast-ui.app", "Contents", "MacOS", "racecast-ui"), "old")
        dst = m.install_ui(src, tgt, "darwin")
        assert dst == os.path.join(tgt, "racecast-ui.app")
        assert os.path.isdir(dst)
        inner = os.path.join(dst, "Contents", "MacOS", "racecast-ui")
        with open(inner, encoding="utf-8") as fh:
            assert fh.read() == "new"
        assert not os.path.exists(os.path.join(src, "racecast-ui.app"))


def t_install_ui_missing_returns_none():
    import tempfile
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tgt:
        assert m.install_ui(src, tgt, "linux") is None
        assert os.listdir(tgt) == []


def t_ui_old_path_naming():
    assert m.ui_old_path(r"C:\racecast\racecast-ui.exe") == r"C:\racecast\racecast-ui-old.exe"


def t_install_ui_renames_locked_running_exe_aside_on_windows():
    # The GUI launcher is RUNNING when it self-updates, so on Windows racecast-ui.exe
    # is locked against deletion. install_ui must rename it aside to
    # racecast-ui-old.exe instead of failing, the same trick swap_plan uses for
    # racecast-old.exe; cleanup_old_binary removes it on the next launch.
    import tempfile
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tgt:
        _write(os.path.join(src, "racecast-ui.exe"), "new")
        _write(os.path.join(tgt, "racecast-ui.exe"), "old")

        def locked_remove(_path):
            raise PermissionError("in use by a running process")

        dst = m.install_ui(src, tgt, "win32", remove=locked_remove)
        assert dst == os.path.join(tgt, "racecast-ui.exe")
        with open(dst, encoding="utf-8") as fh:
            assert fh.read() == "new"                       # new UI is in place
        aside = os.path.join(tgt, "racecast-ui-old.exe")
        assert os.path.exists(aside)                        # old one renamed, not deleted
        with open(aside, encoding="utf-8") as fh:
            assert fh.read() == "old"


def t_install_ui_non_windows_remove_failure_propagates():
    # POSIX can unlink a running binary, so a remove failure there is a real error
    # and must not be papered over with the Windows-only rename.
    import tempfile
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tgt:
        _write(os.path.join(src, "racecast-ui"), "new")
        _write(os.path.join(tgt, "racecast-ui"), "old")

        def boom(_path):
            raise OSError("real failure")

        raised = False
        try:
            m.install_ui(src, tgt, "linux", remove=boom)
        except OSError:
            raised = True
        assert raised, "expected the OSError to propagate on non-Windows"


# classify_tag installs exactly one named release, with no semver compare.
TAGREL = {"tag_name": "preview-pr-42",
          "assets": [{"name": "racecast-macos.tar.gz", "browser_download_url": "https://x/m"},
                     {"name": "racecast-windows.zip", "browser_download_url": "https://x/w"}]}


def t_classify_tag_install_when_asset_present():
    assert m.classify_tag(TAGREL, "darwin") == ("install", "preview-pr-42", "https://x/m")
    assert m.classify_tag(TAGREL, "win32") == ("install", "preview-pr-42", "https://x/w")


def t_classify_tag_building_when_platform_asset_missing():
    assert m.classify_tag(TAGREL, "linux") == ("building", "preview-pr-42", None)


def t_classify_tag_error_on_missing_tag():
    assert m.classify_tag({"assets": []}, "darwin") == ("error", "release has no tag_name", None)


def t_fetch_release_by_tag_parses_json():
    import io, json
    body = json.dumps(TAGREL).encode()
    rel = m.fetch_release_by_tag("preview-pr-42", opener=lambda req, timeout: io.BytesIO(body))
    assert rel["tag_name"] == "preview-pr-42"


# classify_prereleases builds the UI's installable-previews list.
RELEASES = [
    {"tag_name": "v1.2.2", "prerelease": False, "name": "1.2.2",
     "assets": [{"name": "racecast-macos.tar.gz", "browser_download_url": "https://x/stable"}]},
    {"tag_name": "preview-pr-42", "prerelease": True, "name": "Preview: PR #42 (abc1234)",
     "target_commitish": "abc1234deadbeef", "published_at": "2026-06-10T08:00:00Z",
     "body": "notes for 42",
     "assets": [{"name": "racecast-macos.tar.gz", "browser_download_url": "https://x/p42"}]},
    {"tag_name": "preview-main", "prerelease": True, "name": "Preview: main (deadbee)",
     "target_commitish": "", "published_at": "2026-06-09T08:00:00Z", "body": "notes main",
     "assets": []},   # still building, no platform asset yet
]


def t_classify_prereleases_filters_stable_and_shapes_rows():
    rows = m.classify_prereleases(RELEASES, "darwin")
    assert [r["tag"] for r in rows] == ["preview-pr-42", "preview-main"]
    r0 = rows[0]
    assert r0["title"] == "Preview: PR #42 (abc1234)"
    assert r0["commit"] == "abc1234deadbeef"
    assert r0["published_at"] == "2026-06-10T08:00:00Z"
    assert r0["notes"] == "notes for 42"
    assert r0["asset_url"] == "https://x/p42"


def t_classify_prereleases_marks_building_with_none_asset():
    rows = m.classify_prereleases(RELEASES, "darwin")
    building = [r for r in rows if r["tag"] == "preview-main"][0]
    assert building["asset_url"] is None


def t_classify_prereleases_commit_falls_back_to_name_sha():
    # No target_commitish -> _commit_of falls back to the SHA tail of `name`
    # (GitHub releases carry `name`/`tag_name`, never a `version` field).
    rel = [{"tag_name": "preview-pr-9", "prerelease": True,
            "name": "preview-pr9-cafef00", "target_commitish": "",
            "assets": []}]
    assert m.classify_prereleases(rel, "linux")[0]["commit"] == "cafef00"


def t_classify_prereleases_commit_rejects_non_sha_tail():
    rel = [{"tag_name": "preview-main", "prerelease": True, "name": "preview-main",
            "target_commitish": "", "assets": []}]
    assert m.classify_prereleases(rel, "linux")[0]["commit"] == ""


def t_classify_prereleases_empty():
    assert m.classify_prereleases([], "darwin") == []


def t_commit_of_rejects_branch_name_target_commitish():
    # GitHub sets target_commitish to a branch name for branch-targeted releases,
    # which is not a commit SHA. Fall back to the SHA embedded in the name instead
    # of showing 'main' as the commit.
    rel = {"target_commitish": "main", "name": "preview-main-cafe123",
           "tag_name": "preview-main"}
    assert m._commit_of(rel) == "cafe123"


def t_commit_of_accepts_full_sha_target_commitish():
    rel = {"target_commitish": "abc1234deadbeefabc1234deadbeefabc1234dee",
           "name": "x", "tag_name": "preview-pr-1"}
    assert m._commit_of(rel) == "abc1234deadbeefabc1234deadbeefabc1234dee"


def t_find_asset_url():
    rel = {"assets": [{"name": "racecast-macos.tar.gz", "browser_download_url": "https://x/m"},
                      {"name": "racecast-windows.zip", "browser_download_url": "https://x/w"}]}
    assert m._find_asset_url(rel, "darwin") == "https://x/m"
    assert m._find_asset_url(rel, "win32") == "https://x/w"
    assert m._find_asset_url(rel, "linux") is None


def t_expected_digest_reads_sha256():
    rel = {"assets": [{"name": "racecast-macos.tar.gz", "digest": "sha256:" + "a" * 64},
                      {"name": "racecast-windows.zip", "digest": "SHA256:" + "B" * 64}]}
    assert m.expected_digest(rel, "darwin") == "a" * 64
    assert m.expected_digest(rel, "win32") == "b" * 64          # case-normalized


def t_expected_digest_missing_or_malformed_is_none():
    base = "racecast-macos.tar.gz"
    assert m.expected_digest({"assets": [{"name": base}]}, "darwin") is None          # no field
    assert m.expected_digest({"assets": [{"name": base, "digest": "md5:abc"}]},
                             "darwin") is None                                        # wrong algo
    assert m.expected_digest({"assets": [{"name": base, "digest": "sha256:xyz"}]},
                             "darwin") is None                                        # not 64 hex
    assert m.expected_digest({"assets": []}, "darwin") is None                        # no asset


def t_archive_sha256_matches_hashlib():
    import hashlib, tempfile, os as _os
    fd, p = tempfile.mkstemp(); _os.write(fd, b"racecast-bytes"); _os.close(fd)
    try:
        assert m.archive_sha256(p) == hashlib.sha256(b"racecast-bytes").hexdigest()
    finally:
        _os.unlink(p)


def t_download_rejects_non_https():
    raised = False
    try:
        m.download("http://example.com/x.tar.gz", "/tmp/x")     # plain HTTP -> refused
    except ValueError:
        raised = True
    assert raised


# Extraction hardening: symlink members and decompression caps. (#99)
def _raises_value_error(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def t_check_extract_budget_ok():
    m._check_extract_budget([10, 20, 30])           # well under caps -> no raise


def t_check_extract_budget_member_cap():
    over = [1] * (m.MAX_EXTRACT_MEMBERS + 1)
    assert _raises_value_error(lambda: m._check_extract_budget(over))


def t_check_extract_budget_byte_cap():
    assert _raises_value_error(
        lambda: m._check_extract_budget([m.MAX_EXTRACT_BYTES + 1]))


def t_check_extract_budget_tolerates_none_sizes():
    m._check_extract_budget([None, None, 5])        # tar dirs report size 0/None


def t_extract_binary_drops_tar_symlink_member():
    """A tar symlink member must never be recreated (it could let a following
    member escape dest_dir on pre-3.12 interpreters). Regular files still extract."""
    import io, tarfile, tempfile, os as _os
    d = tempfile.mkdtemp()
    arch = _os.path.join(d, "racecast-linux.tar.gz")
    payload = b"#!/bin/sh\n"
    with tarfile.open(arch, "w:gz") as tf:
        ti = tarfile.TarInfo("racecast"); ti.size = len(payload)
        tf.addfile(ti, io.BytesIO(payload))
        link = tarfile.TarInfo("evil"); link.type = tarfile.SYMTYPE
        link.linkname = "../../escape-target"
        tf.addfile(link)
    out = _os.path.join(d, "out")
    path = m.extract_binary(arch, out)
    assert path == _os.path.join(out, "racecast")
    assert _os.path.isfile(_os.path.join(out, "racecast"))
    assert not _os.path.lexists(_os.path.join(out, "evil"))   # link member dropped


def t_extract_binary_enforces_byte_cap_tar():
    import io, tarfile, tempfile, os as _os
    d = tempfile.mkdtemp()
    arch = _os.path.join(d, "racecast-linux.tar.gz")
    payload = b"x" * 1000
    with tarfile.open(arch, "w:gz") as tf:
        ti = tarfile.TarInfo("racecast"); ti.size = len(payload)
        tf.addfile(ti, io.BytesIO(payload))
    orig = m.MAX_EXTRACT_BYTES
    m.MAX_EXTRACT_BYTES = 100
    try:
        assert _raises_value_error(lambda: m.extract_binary(arch, _os.path.join(d, "out")))
    finally:
        m.MAX_EXTRACT_BYTES = orig


def t_extract_binary_enforces_byte_cap_zip():
    import tempfile, zipfile, os as _os
    d = tempfile.mkdtemp()
    arch = _os.path.join(d, "racecast-windows.zip")
    with zipfile.ZipFile(arch, "w") as zf:
        zf.writestr("racecast.exe", b"y" * 1000)
    orig = m.MAX_EXTRACT_BYTES
    m.MAX_EXTRACT_BYTES = 100
    try:
        assert _raises_value_error(lambda: m.extract_binary(arch, _os.path.join(d, "out")))
    finally:
        m.MAX_EXTRACT_BYTES = orig


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
