"""Asset metadata never changes canonical download or control identities."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_graphics as graphics
import test_media as media

DRIVE = "https://drive.google.com/file/d/FIXTURE/view"


def t_alias_before_url_does_not_replace_graphic_link():
    rows = [
        ["Name", "Alias", "URL", "Internal"],
        ["Standings", "Meisterschaft – Übersicht", DRIVE, "FALSE"],
    ]
    assert graphics.m.graphics_from_csv(rows) == {"Standings": DRIVE}


def t_alias_is_never_a_media_identifier_or_url():
    rows = [
        ["Name", "Alias", "URL"],
        ["Standings", "Intro Video", DRIVE],
        ["Trailer Video", "https://example.invalid/alias", "https://youtu.be/fixture"],
        ["Outro Video", "https://example.invalid/not-a-download", ""],
    ]
    assert media.m.media_urls_from_csv(rows) == {"trailer": "https://youtu.be/fixture"}
    assert (
        media.m.music_url_from_csv([
            ["Asset", "Alias", "Link"],
            ["Standings", "Intermission Music", DRIVE],
        ])
        is None
    )


def t_metadata_refresh_preserves_unknown_fields_and_clears_removed_alias():
    import asset_metadata as am
    import tempfile
    import json

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "manifest.json"
        p.write_text(
            json.dumps({"custom": {"keep": True}, "internal": ["Old"]}),
            encoding="utf-8",
        )
        am.refresh(
            td,
            [
                ["Name", "Alias", "URL", "Internal"],
                ["Standings", "  Tabelle ä  ", DRIVE, "TRUE"],
                ["Trailer Video", "Film", "https://youtu.be/fixture", "FALSE"],
            ],
            "graphics",
        )
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data == {
            "custom": {"keep": True},
            "internal": ["Standings"],
            "aliases": {"Standings": "Tabelle ä"},
        }
        assert am.display_name(td, "Standings", "STANDINGS") == "Tabelle ä"
        assert am.display_name(td, "Standings", "STANDINGS", public=True) == "STANDINGS"
        before = p.read_bytes()
        for bad in ([], [["<!DOCTYPE html>"], ["error"]], [["Access denied"]]):
            try:
                am.refresh(td, bad, "graphics")
            except ValueError:
                pass
            else:
                raise AssertionError("Unrecognized Sheet response accepted")
            assert p.read_bytes() == before
        am.refresh(
            td, [["Name", "URL", "Alias"], ["Standings", DRIVE, "  "]], "graphics"
        )
        assert am.display_name(td, "Standings", "STANDINGS") == "STANDINGS"
        assert json.loads(p.read_text(encoding="utf-8"))["custom"] == {"keep": True}


def t_atomic_failure_preserves_previous_metadata_and_cleans_temp():
    import asset_metadata as am
    import tempfile
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as td:
        am.refresh(
            td, [["Name", "Alias", "URL"], ["Standings", "First", DRIVE]], "graphics"
        )
        p = Path(td) / "manifest.json"
        before = p.read_bytes()
        with patch.object(am.os, "replace", side_effect=OSError("fixture")):
            try:
                am.refresh(
                    td,
                    [["Name", "Alias", "URL"], ["Standings", "Second", DRIVE]],
                    "graphics",
                )
            except OSError:
                pass
            else:
                raise AssertionError("Replacement failure swallowed")
        assert p.read_bytes() == before
        assert sorted(x.name for x in Path(td).iterdir()) == ["manifest.json"]


def t_metadata_only_refresh_does_not_download_or_change_asset_bytes():
    import tempfile
    import json
    from unittest.mock import patch

    for module, filename, canonical in (
        (graphics.m, "Standings.png", "Standings"),
        (media.m, "trailer.mp4", "Trailer Video"),
    ):
        with tempfile.TemporaryDirectory() as td:
            asset = Path(td) / filename
            asset.write_bytes(b"existing-byte-sentinel")
            csv_text = (
                "Name,Alias,URL,Internal\n" + canonical + ",Community board,,FALSE\n"
            )
            with (
                patch.object(
                    sys,
                    "argv",
                    ["tool", "--out", td, "--sheet-id", "fixture", "--metadata-only"],
                ),
                patch.object(module, "load_dotenv"),
                patch.dict(module.os.environ, {}, clear=True),
                patch.object(module, "fetch_assets_csv", return_value=csv_text),
            ):
                try:
                    module.main()
                except SystemExit as exc:
                    code = exc.code
                else:
                    code = 0
            assert code == 0, (module.__name__, code)
            assert asset.read_bytes() == b"existing-byte-sentinel"
            assert json.loads((Path(td) / "manifest.json").read_text(encoding="utf-8"))[
                "aliases"
            ] == {canonical: "Community board"}


def t_refresh_preserves_other_downloader_aliases():
    import asset_metadata as am
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        am.refresh(
            td, [["Name", "Alias", "URL"], ["Trailer Video", "Film", ""]], "media"
        )
        am.refresh(
            td, [["Name", "Alias", "URL"], ["Standings", "Board", ""]], "graphics"
        )
        assert am.read(td)["aliases"] == {"Trailer Video": "Film", "Standings": "Board"}
        am.refresh(td, [["Name", "Alias", "URL"], ["Trailer Video", "", ""]], "media")
        assert am.read(td)["aliases"] == {"Standings": "Board"}


def t_live_gallery_alias_refresh_and_private_filtering():
    import test_console_gate as gate
    import asset_metadata as am
    import tempfile
    import json

    with tempfile.TemporaryDirectory() as td:
        for name in ("Standings", "Schedule", "Race Results"):
            (Path(td) / (name + ".png")).write_bytes(b"real-graphic-fixture")
        am.refresh(
            td,
            [
                ["Name", "Alias", "URL", "Internal"],
                ["Standings", "Sponsor board", DRIVE, "FALSE"],
                ["Schedule", "Sponsor board", DRIVE, "FALSE"],
                ["Race Results", "private-alias-sentinel", DRIVE, "TRUE"],
            ],
            "graphics",
        )
        server = gate._serve(graphics_dir=td, graphics_take="request")
        port = server.server_address[1]
        try:
            for who in ("alice", "dave", "bob"):
                code, body = gate._get(port, "/console/graphics", gate._tok(who))
                assert code == 200 and "private-alias-sentinel" not in body, (
                    code,
                    body,
                )
                rows = json.loads(body)["graphics"]
                assert {r["name"]: r.get("displayName") for r in rows} == {
                    "Standings": "Sponsor board",
                    "Schedule": "Sponsor board",
                }
            am.refresh(
                td,
                [["Name", "Alias", "URL"], ["Standings", "Updated board", DRIVE]],
                "graphics",
            )
            code, body = gate._get(port, "/console/graphics", gate._tok("alice"))
            assert code == 200
            rows = {r["name"]: r for r in json.loads(body)["graphics"]}
            assert rows["Standings"]["displayName"] == "Updated board"
            assert rows["Schedule"].get("displayName", "Schedule") == "Schedule"
        finally:
            server.shutdown()
            server.server_close()


def t_request_snapshot_survives_alias_rename_and_deduplicates_source():
    import test_console_gate as gate
    import asset_metadata as am
    import tempfile
    import json

    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "Standings.png").write_bytes(b"real-graphic-fixture")

        def rename(label):
            am.refresh(
                td, [["Name", "Alias", "URL"], ["Standings", label, DRIVE]], "graphics"
            )

        rename("First label")
        server = gate._serve(graphics_dir=td, graphics_take="request")
        port = server.server_address[1]
        try:

            def request(who):
                code, body = gate._post(
                    port,
                    "/console/cockpit/graphic-takes/request",
                    gate._tok(who),
                    body={
                        "source": "Standings",
                        "displayName": "untrusted caller label",
                    },
                )
                assert code == 200, (code, body)
                return json.loads(body)

            first = request("alice")
            assert (
                first["source"] == "Standings" and first["displayName"] == "First label"
            )
            rename("Second label")
            second = request("dave")
            assert (
                second["id"] == first["id"]
                and second["merged"]
                and second["displayName"] == "First label"
            )
            code, body = gate._get(
                port, "/console/obs/graphics/requests", gate._tok("bob")
            )
            assert (
                code == 200
                and json.loads(body)["requests"][0]["displayName"] == "First label"
            )
            code, body = gate._get(
                port, "/console/cockpit/graphic-takes", gate._tok("alice")
            )
            row = next(
                r for r in json.loads(body)["graphics"] if r["source"] == "Standings"
            )
            assert (
                code == 200
                and row["requestDisplayName"] == "First label"
                and row["displayName"] == "Second label"
            )
            code, body = gate._post(
                port,
                f"/console/obs/graphics/request/{first['id']}",
                gate._tok("bob"),
                body={"action": "decline"},
            )
            assert code == 200, body
            code, body = gate._get(
                port, "/console/cockpit/chat/data", gate._tok("alice")
            )
            texts = [row["text"] for row in json.loads(body)["messages"]]
            assert (
                "Director declined First label" in texts
                and "dave asked for First label" in texts
            ), texts
        finally:
            server.shutdown()
            server.server_close()


def t_control_center_keeps_filenames_and_resolves_private_aliases_locally():
    import test_racecast as cli
    import asset_metadata as am
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        g = root / "graphics"
        m = root / "media"
        g.mkdir()
        m.mkdir()
        (g / "Standings.png").write_bytes(b"graphic")
        (m / "trailer.mp4").write_bytes(b"video")
        am.refresh(
            g,
            [
                ["Name", "URL", "Alias", "Internal"],
                ["Standings", DRIVE, "Private board", "TRUE"],
            ],
            "graphics",
        )
        am.refresh(
            m, [["Name", "URL", "Alias"], ["Trailer Video", "", "League film"]], "media"
        )
        result = cli.m.assets_files_data(
            roots={"graphics": str(g), "media": str(m)}, profile="fixture"
        )
        assert result["ok"] and result["profile"] == "fixture"
        assert (
            result["graphics"][0]["name"] == "Standings.png"
            and result["graphics"][0].get("displayName") == "Private board"
        )
        assert (
            result["media"][0]["name"] == "trailer.mp4"
            and result["media"][0].get("displayName") == "League film"
        )


def t_internal_media_alias_stays_private():
    import asset_metadata as am
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        am.refresh(
            td,
            [
                ["Name", "Alias", "URL", "Internal"],
                ["Trailer Video", "private-film-label", "", "TRUE"],
            ],
            "media",
        )
        assert am.display_name(td, "Trailer Video", public=True) == "Trailer Video"
        assert am.display_name(td, "Trailer Video") == "private-film-label"


def t_bundle_and_look_carry_cached_alias_metadata():
    import test_profile_io as bundle
    import test_backup as look
    import asset_metadata as am
    import tempfile

    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as imported:
        sources, _ = bundle._profile(td)
        am.refresh(
            sources["graphics"],
            [["Name", "Alias", "URL"], ["Overlay", "League frame", ""]],
            "graphics",
        )
        am.refresh(
            sources["media"],
            [["Name", "Alias", "URL"], ["Trailer Video", "League film", ""]],
            "media",
        )
        path = bundle.pio.export_profile("iro-gtec", sources, True, td)
        bundle.pio.import_profile(
            path,
            {
                "profiles_root": str(Path(imported) / "profiles"),
                "runtime_root": str(Path(imported) / "runtime"),
            },
        )
        assert (
            am.display_name(Path(imported) / "runtime/iro-gtec/graphics", "Overlay")
            == "League frame"
        )
        assert (
            am.display_name(Path(imported) / "runtime/iro-gtec/media", "Trailer Video")
            == "League film"
        )
        assert (
            am.display_name(Path(imported) / "runtime/other/graphics", "Overlay")
            == "Overlay"
        )
        look_sources = {
            "overlay": str(Path(sources["profile_dir"]) / "overlay"),
            "graphics": sources["graphics"],
            "media": sources["media"],
            "backups": str(Path(td) / "backups"),
        }
        saved = look.ba.create_backup("aliases", look_sources, profile="iro-gtec")
        am.refresh(
            sources["graphics"],
            [["Name", "Alias", "URL"], ["Overlay", "Changed frame", ""]],
            "graphics",
        )
        look.ba.restore_backup(saved, look_sources)
        assert am.display_name(sources["graphics"], "Overlay") == "League frame"


def t_scene_builder_resolves_only_its_actual_background_asset():
    import test_racecast as cli
    import asset_metadata as am
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            graphics_dir = Path(cli.m._runtime_dir()) / "graphics"
            am.refresh(
                graphics_dir,
                [
                    ["Name", "Alias", "URL"],
                    ["Overlay", "Racing frame", ""],
                    ["Intermission", "Break artwork", ""],
                    ["Intermission Music", "Playlist", ""],
                ],
                "graphics",
            )
            hud = cli.m.overlay_slots_data("hud")
            intermission = cli.m.overlay_slots_data("intermission")
            assert hud["ok"] and hud.get("background") == {
                "file": "Overlay.png",
                "displayName": "Racing frame",
            }
            assert intermission["ok"] and intermission.get("background") == {
                "file": "Intermission.png",
                "displayName": "Break artwork",
            }
        finally:
            cli.m._env_base, cli.m._runtime_base_dir = original


def t_headers_are_case_bom_and_order_tolerant_and_alias_is_literal_text():
    import asset_metadata as am
    import tempfile

    for header, row in (
        (
            ["\ufeffAsset", "aLiAs", "Internal", "Link"],
            ["Standings", "<html> literal ä", "FALSE", DRIVE],
        ),
        (
            ["Label", "Link", "Alias", "OBS only"],
            ["Standings", DRIVE, "<html> literal ä", "FALSE"],
        ),
    ):
        assert graphics.m.graphics_from_csv([header, row]) == {"Standings": DRIVE}
        with tempfile.TemporaryDirectory() as td:
            am.refresh(td, [header, row], "graphics")
            assert am.display_name(td, "Standings") == "<html> literal ä"


def t_headerless_media_labels_keep_legacy_moved_columns_during_refresh():
    import asset_metadata as am
    import tempfile

    rows = [
        ["", "Intro Video", "https://youtu.be/fixture"],
        ["note", "", "Intermission Music", "https://example.invalid/music"],
    ]
    assert media.m.media_urls_from_csv(rows) == {"intro": "https://youtu.be/fixture"}
    assert media.m.music_url_from_csv(rows) == "https://example.invalid/music"
    with tempfile.TemporaryDirectory() as td:
        am.refresh(td, rows, "media")
        assert am.read(td)["aliases"] == {}


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
