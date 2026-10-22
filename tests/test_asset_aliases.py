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
        for bad in (
            [],
            [["<!DOCTYPE html>"], ["error"]],
            [["<!DOCTYPE html>", DRIVE]],
            [["Access denied"]],
        ):
            try:
                am.refresh(td, bad, "graphics")
            except ValueError:
                pass  # Expected invalid Sheet response must retain the previous bytes.
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
                pass  # Injected replacement failure must leave the old file intact.
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


def t_director_labels_include_internal_assets_crew_labels_do_not():
    # #955: internal (OBS-only) graphics are operated by the director, so the Director
    # Panel and Companion show the league's alias for them too. The crew-facing map
    # keeps them out, so an internal alias never reaches /console crew pages.
    import test_console_gate as gate
    import asset_metadata as am
    import tempfile
    import json

    with tempfile.TemporaryDirectory() as td:
        graphics, media = Path(td) / "graphics", Path(td) / "media"
        graphics.mkdir()
        media.mkdir()
        for name in ("Standings", "Overlay"):
            (graphics / (name + ".png")).write_bytes(b"real-graphic-fixture")
        (media / "trailer.mp4").write_bytes(b"clip")
        am.refresh(str(graphics), [["Name", "Alias", "URL", "Internal"],
                                   ["Standings", "Sponsor board", DRIVE, "FALSE"],
                                   ["Overlay", "League frame", DRIVE, "TRUE"]], "graphics")
        am.refresh(str(media), [["Name", "Alias", "URL", "Internal"],
                                ["Trailer Video", "Season film", "", "TRUE"]], "media")
        director = {"Standings": "Sponsor board", "Overlay": "League frame",
                    "Trailer Video": "Season film"}
        assert gate.m.asset_labels(str(graphics), director=True) == director
        assert gate.m.asset_labels(str(graphics)) == {"Standings": "Sponsor board"}

        server = gate._serve(graphics_dir=str(graphics))
        port = server.server_address[1]
        try:
            for path, token in (("/obs/asset-labels", None),
                                ("/console/obs/asset-labels", gate._tok("bob"))):
                code, body = gate._get(port, path, token)
                assert code == 200 and json.loads(body) == director, (path, code, body)
            for crew in ("alice", "dave"):       # commentator, race_control desk
                code, body = gate._get(port, "/console/obs/asset-labels", gate._tok(crew))
                assert code == 403 and "League frame" not in body, (crew, code, body)
            code, body = gate._get(port, "/console/graphics", gate._tok("alice"))
            assert code == 200 and "League frame" not in body, body
            # The Director Panel's take list gets the director map; the crew take
            # view (/console/cockpit/graphic-takes) keeps the crew map.
            seen = {}
            real_view = gate.m.graphic_takes_view

            def spy(*args, labels=None, **kwargs):
                seen["director" if args[3] is None else "crew"] = labels
                return {"graphics": []}
            gate.m.graphic_takes_view = spy
            try:
                gate._get(port, "/obs/graphics")
                gate._get(port, "/console/cockpit/graphic-takes", gate._tok("alice"))
            finally:
                gate.m.graphic_takes_view = real_view
            assert seen.get("director") == director, seen
            assert seen.get("crew") == {"Standings": "Sponsor board"}, seen
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


def t_legacy_internal_header_with_unrecognized_name_caption_stays_private():
    import asset_metadata as am
    import test_console_gate as gate
    import tempfile

    rows = [
        ["Asset Name", "URL", "Internal", "Alias"],
        ["Private graphic", DRIVE, "TRUE", "private-caption-sentinel"],
    ]
    assert graphics.internal_from_csv(rows) == {"Private graphic"}
    for metadata_only in (False, True):
        from unittest.mock import patch
        import csv, io

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "Private graphic.png").write_bytes(b"cached-private-image")
            am.write(td, {"internal": ["Private graphic"]})
            out = io.StringIO()
            csv.writer(out).writerows(rows)
            argv = ["tool", "--out", td, "--sheet-id", "fixture"] + (
                ["--metadata-only"] if metadata_only else []
            )
            with (
                patch.object(sys, "argv", argv),
                patch.object(graphics.m, "load_dotenv"),
                patch.object(
                    graphics.m, "fetch_assets_csv", return_value=out.getvalue()
                ),
                patch.object(graphics.m, "download"),
                patch.object(graphics.m, "reset_unlinked_graphics", return_value=[]),
                patch.object(graphics.m, "seed_missing_graphics", return_value=[]),
            ):
                graphics.m.main()
            assert am.read(td)["internal"] == ["Private graphic"]
            assert gate.m.list_graphics(td) == []
            assert (
                am.display_name(td, "Private graphic", public=True) == "Private graphic"
            )


def t_metadata_columns_cannot_become_urls_without_a_known_url_header():
    rows = [
        ["Name", "Alias", "Internal", "Graphic"],
        ["Standings", "https://drive.google.com/file/d/ALIAS/view", "FALSE", DRIVE],
    ]
    assert graphics.m.graphics_from_csv(rows) == {"Standings": DRIVE}
    assert (
        graphics.m.graphics_from_csv([
            ["Name", "Alias", "Internal"],
            ["Standings", DRIVE, "FALSE"],
        ])
        == {}
    )


def t_take_view_does_not_attach_aliases_to_controls_without_rights():
    import test_console_gate as gate
    import types

    relay = types.SimpleNamespace(solo=False)
    flags = types.SimpleNamespace(available=lambda: ["yellow"], get=lambda: "")
    modes = {"commentator": "direct", "race_control": "direct"}

    def view(roles, modes=modes):
        return gate.m.graphic_takes_view(
            relay,
            None,
            modes,
            roles,
            gate.m.graphic_takes.CrewTakes(),
            flags,
            labels={"Standings": "Allowed board", "Flag Yellow": "RC-only caption"},
        )

    crew = view({"commentator"})
    assert "RC-only caption" not in str(crew)
    assert (
        next(r for r in crew["graphics"] if r["source"] == "Standings")["displayName"]
        == "Allowed board"
    )
    assert "RC-only caption" in str(view({"race_control"}))
    assert "Allowed board" not in str(
        view({"commentator"}, {"commentator": "off", "race_control": "direct"})
    )


def t_duplicate_aliases_keep_distinct_canonical_requests_and_bad_cache_falls_back():
    import asset_metadata as am
    import test_console_gate as gate
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for bad in ("not json", "[]", '{"aliases": null}', '{"aliases":{"Standings":7}}'):
            (Path(td) / "manifest.json").write_text(bad, encoding="utf-8")
            assert am.display_name(td, "Standings", "Default") == "Default"
        for name in ("Standings", "Schedule"):
            (Path(td) / (name + ".png")).write_bytes(b"fixture-graphic")
        am.refresh(td, [
            ["Name", "URL", "Alias"],
            ["Standings", "", "Same human label"],
            ["Schedule", "", "Same human label"],
        ], "graphics")
        rows = gate.m.list_graphics(td)
        assert {r["name"] for r in rows} == {"Standings", "Schedule"}
        assert {r["displayName"] for r in rows} == {"Same human label"}
        requests = gate.m.graphic_takes.Requests()
        first, _ = requests.add("Standings", "alice", "Alice", 1, "Same human label")
        second, merged = requests.add("Schedule", "alice", "Alice", 2, "Same human label")
        assert not merged and first["id"] != second["id"]
        assert second["source"] == "Schedule" and first["source"] == "Standings"


def t_authorized_take_errors_keep_alias_and_canonical_identity():
    import test_console_gate as gate
    from unittest.mock import patch

    definitions = gate.m.graphic_takes.definitions(False)
    entry = gate.m.graphic_takes.find(definitions, "Standings")
    for obs, status, error in (
        (None, 503, "obs unavailable"),
        (gate._TakeFakeObs(scene="Intermission"), 409, "is not on air"),
    ):
        data, code = gate.m._take_graphic(
            obs, entry, definitions, "Alice", "Standings", True,
            gate.m.graphic_takes.CrewTakes(), None, None, "Community board",
        )
        assert code == status and error in data["error"], data
        assert data.get("source") == "Standings", data
        assert data.get("displayName") == "Community board", data
    fake = gate._TakeFakeObs()
    with patch.object(fake, "read_obs_state", return_value=(None, "fixture offline")):
        data, code = gate.m._take_graphic(
            fake, entry, definitions, "Alice", "Standings", True,
            gate.m.graphic_takes.CrewTakes(), None, None, "Community board",
        )
        assert code == 503 and data["error"] == "fixture offline", data
        assert data.get("displayName") == "Community board", data


def t_concurrent_metadata_refresh_keeps_both_downloaders_entries():
    import asset_metadata as am
    import tempfile, threading
    from unittest.mock import patch

    entered, release, second_read = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    errors = []
    original_dump, original_read = am.json.dump, am.read

    def dump(*args, **kwargs):
        if threading.current_thread().name == "alias-graphics":
            entered.set()
            assert release.wait(3), "Fixture writer was not released"
        return original_dump(*args, **kwargs)

    def read(*args, **kwargs):
        if threading.current_thread().name == "alias-media":
            second_read.set()
        return original_read(*args, **kwargs)

    with tempfile.TemporaryDirectory() as td:

        def refresh(kind, name, alias):
            try:
                am.refresh(td, [["Name", "URL", "Alias"], [name, "", alias]], kind)
            except Exception as exc:
                errors.append(exc)

        with (
            patch.object(am.json, "dump", side_effect=dump),
            patch.object(am, "read", side_effect=read),
        ):
            first = threading.Thread(
                name="alias-graphics",
                target=refresh,
                args=("graphics", "Standings", "Board"),
            )
            second = threading.Thread(
                name="alias-media",
                target=refresh,
                args=("media", "Trailer Video", "Film"),
            )
            first.start()
            try:
                assert entered.wait(2), "First writer did not reach its atomic write"
                second.start()
                assert not second_read.wait(0.05), (
                    "Concurrent refresh read a stale manifest before the first writer committed"
                )
            finally:
                release.set()
                first.join(3)
                if second.ident is not None:
                    second.join(3)
            assert not first.is_alive() and not second.is_alive() and not errors, errors
        assert am.read(td)["aliases"] == {"Standings": "Board", "Trailer Video": "Film"}



def _run_main(module, argv, csv_text, **patches):
    """Run a downloader main() with a fixture Sheet; return (exit code, stdout)."""
    import contextlib
    import io
    from unittest.mock import patch

    out = io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(sys, "argv", ["tool"] + argv))
        stack.enter_context(patch.object(module, "load_dotenv"))
        stack.enter_context(patch.dict(module.os.environ, {}, clear=True))
        stack.enter_context(
            patch.object(module, "fetch_assets_csv", return_value=csv_text))
        for name, value in patches.items():
            stack.enter_context(patch.object(module, name, value))
        stack.enter_context(contextlib.redirect_stdout(out))
        try:
            module.main()
        except SystemExit as exc:
            code = exc.code
        else:
            code = 0
    return code, out.getvalue()


def t_graphics_download_continues_when_metadata_write_fails():
    import asset_metadata as am
    import tempfile
    from unittest.mock import MagicMock, patch

    with tempfile.TemporaryDirectory() as td:
        am.write(td, {"aliases": {"Standings": "Old board"}})
        before = (Path(td) / "manifest.json").read_bytes()
        download = MagicMock()
        csv_text = "Name,Alias,URL\nStandings,New board," + DRIVE + "\n"
        with patch.object(am.atomic_replace, "replace",
                          side_effect=PermissionError(13, "fixture: open")):
            code, out = _run_main(
                graphics.m, ["--out", td, "--sheet-id", "fixture"], csv_text,
                download=download,
                reset_unlinked_graphics=MagicMock(return_value=[]),
                seed_missing_graphics=MagicMock(return_value=[]))
        assert code in (0, None), (code, out)
        assert download.call_count == 1, out
        assert "WARNING" in out and "metadata" in out, out
        assert (Path(td) / "manifest.json").read_bytes() == before
        assert not [p for p in Path(td).iterdir() if p.suffix == ".tmp"]


def t_graphics_unusable_sheet_response_changes_nothing():
    import asset_metadata as am
    import tempfile
    from unittest.mock import MagicMock

    for csv_text in ("", "<!DOCTYPE html><html><body>Sign in</body></html>\n"):
        with tempfile.TemporaryDirectory() as td:
            am.write(td, {"aliases": {"Standings": "Old board"}})
            before = (Path(td) / "manifest.json").read_bytes()
            download, reset = MagicMock(), MagicMock(return_value=[])
            code, out = _run_main(
                graphics.m, ["--out", td, "--sheet-id", "fixture"], csv_text,
                download=download, reset_unlinked_graphics=reset,
                seed_missing_graphics=MagicMock(return_value=[]))
            assert code not in (0, None), (csv_text, out)
            assert download.call_count == 0 and reset.call_count == 0, out
            assert (Path(td) / "manifest.json").read_bytes() == before


def t_media_explicit_url_survives_unusable_assets_response():
    import asset_metadata as am
    import tempfile
    from unittest.mock import MagicMock

    for csv_text in ("", "<!DOCTYPE html><html><body>Sign in</body></html>\n"):
        with tempfile.TemporaryDirectory() as td:
            am.write(td, {"aliases": {"Intro Video": "Opening titles"}})
            before = (Path(td) / "manifest.json").read_bytes()
            download = MagicMock()
            code, out = _run_main(
                media.m,
                ["--out", td, "--sheet-id", "fixture", "--which", "intro",
                 "--intro-url", "https://youtu.be/abc"],
                csv_text,
                download=download,
                reset_unlinked_media=MagicMock(return_value=[]),
                seed_missing_media=MagicMock(return_value=[]))
            assert code in (0, None), (csv_text, code, out)
            assert [c.args[0] for c in download.call_args_list] == [
                "https://youtu.be/abc"], out
            assert "WARNING" in out, out
            assert (Path(td) / "manifest.json").read_bytes() == before


def t_media_unusable_assets_response_never_resets_clips_to_placeholder():
    import tempfile
    from unittest.mock import MagicMock

    for csv_text in ("", "<!DOCTYPE html><html></html>" + chr(10)):
        with tempfile.TemporaryDirectory() as td:
            reset = MagicMock(return_value=[])
            code, out = _run_main(
                media.m, ["--out", td, "--sheet-id", "fixture", "--which", "all"],
                csv_text, download=MagicMock(), download_music=MagicMock(),
                reset_unlinked_media=reset,
                seed_missing_media=MagicMock(return_value=[]))
            assert reset.call_count == 1, out
            assert reset.call_args.args[1] == set(), reset.call_args
            assert reset.call_args.kwargs.get("want_music") is False, reset.call_args


def t_media_metadata_only_still_fails_on_unusable_assets_response():
    import asset_metadata as am
    import tempfile
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as td:
        am.write(td, {"aliases": {"Intro Video": "Opening titles"}})
        before = (Path(td) / "manifest.json").read_bytes()
        download = MagicMock()
        code, out = _run_main(
            media.m, ["--out", td, "--sheet-id", "fixture", "--metadata-only"],
            "<!DOCTYPE html><html></html>\n", download=download)
        assert code not in (0, None), out
        assert download.call_count == 0
        assert (Path(td) / "manifest.json").read_bytes() == before


def t_gallery_reads_each_manifest_once_per_call():
    import asset_metadata as am
    import test_console_gate as gate
    import tempfile
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as td:
        graphics_dir, media_dir = Path(td) / "graphics", Path(td) / "media"
        graphics_dir.mkdir()
        media_dir.mkdir()
        for name in ("Standings", "Schedule", "Race Results", "Grid"):
            (graphics_dir / (name + ".png")).write_bytes(b"real-graphic-fixture")
        for filename in am.MEDIA_NAMES:
            (media_dir / filename).write_bytes(b"clip")
        am.write(graphics_dir, {"aliases": {"Standings": "Board"},
                                "internal": ["Grid"]})
        am.write(media_dir, {"aliases": {"Intro Video": "Opening titles"}})
        real_read = am.read
        calls = []

        def counting(directory):
            calls.append(str(directory))
            return real_read(directory)

        with patch.object(am, "read", counting):
            rows = gate.m.list_graphics(str(graphics_dir))
            assert len(calls) == 1, calls
            assert [r["name"] for r in rows] == ["Race Results", "Schedule",
                                                 "Standings"], rows
            assert rows[2]["displayName"] == "Board", rows
            calls.clear()
            labels = gate.m.asset_labels(str(graphics_dir), str(media_dir))
            assert len(calls) == 2, calls
            assert labels == {"Standings": "Board",
                              "Intro Video": "Opening titles"}, labels


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
