"""Audience value, definition and durable profile-state contracts; no services."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/scripts"))
import audience_widgets as aw

Sample = aw.Sample
normalize = aw.normalize
clean = aw.clean
Store = aw.Store


def widget(**patch):
    return {
        "id": "audience-" + "a" * 32,
        "source": "manual",
        "mode": "total",
        "manualValue": 0,
        "start": 0,
        "target": 100,
        "baseline": None,
        "updatedAt": 0,
        **patch,
    }


def t_real_zero_and_exceeded_target():
    for value, percent in [(0, 0), (150, 100), (-5, 0)]:
        data = normalize(widget(manualValue=value), now=1000)
        assert data["available"], data
        assert data["value"] == value and data["rawValue"] == value, data
        assert data["fill"] == percent, data
        assert data["percentage"] == value, data
        assert data["holdFor"] is None


def t_missing_baseline_is_unavailable_and_capture_is_stable():
    data = normalize(widget(mode="net", manualValue=1000))
    assert (
        not data["available"] and data["value"] is None and data["rawValue"] == 1000
    ), data
    assert "baseline" in data["reason"].lower(), data
    baseline = {"value": 1000, "identity": "manual:audience-" + "a" * 32}
    for now in (0, 1000):
        data = normalize(
            widget(mode="net", manualValue=1002, baseline=baseline), now=now
        )
        assert data["available"] and data["value"] == 2, data
        assert data["percentage"] == 2, data


def t_stale_deadline_and_unavailable_are_distinct():
    goal = widget(source="youtube")
    for status, age, visible in [
        ("available", 0, True),
        ("stale", 20, True),
        ("stale", 121, False),
        ("unavailable", 0, False),
    ]:
        data = normalize(
            goal, Sample(0, status, 100, identity="fixture"), now=100 + age
        )
        assert data["available"] is visible, data
        assert data["rawValue"] == 0, data
        assert data["availability"] == status, data
    data = normalize(goal, Sample(12, "available", 100, identity="fixture"), now=221)
    assert data["availability"] == "stale" and not data["available"], data


def t_missing_rounded_and_other_target_cannot_produce_exact_gain():
    baseline = {"value": 1000, "identity": "old-target"}
    goal = widget(source="youtube", mode="net", baseline=baseline)
    for sample in [
        Sample(),
        Sample(1200, "available", 100, exact=False, identity="old-target"),
        Sample(1200, "available", 100, identity="new-target"),
        Sample(1200, "available", 100, identity="old-target", supports_net=False),
    ]:
        data = normalize(goal, sample, now=100)
        assert not data["available"] and data["value"] is None, data


def t_invalid_automatic_values_never_become_zero():
    for value in (None, float("nan"), float("inf"), True):
        data = normalize(widget(source="twitch"), Sample(value, "available", 0), now=0)
        assert not data["available"] and data["value"] is None, data


def t_missing_target_and_large_finite_range():
    data = normalize(widget(manualValue=18, target=None))
    assert data["available"] and data["percentage"] is None, data
    data = normalize(widget(manualValue=0, start=-1e308, target=1e308))
    assert data["available"] and data["percentage"] == 50, data


def t_expiry_boundary_and_finite_net_arithmetic():
    data = normalize(widget(source="youtube"), Sample(12, "available", 100), now=220)
    assert (
        not data["available"]
        and data["availability"] == "stale"
        and data["holdFor"] == 0
    ), data
    baseline = {"value": -1e308, "identity": "manual:audience-" + "a" * 32}
    data = normalize(widget(mode="net", manualValue=1e308, baseline=baseline), now=0)
    assert not data["available"] and data["value"] is None, data


ID = "audience-" + "a" * 32


def raw(**patch):
    return {"id": ID, "label": " Community challenge ", "manualValue": 18, **patch}


def rejected(value):
    try:
        clean(value)
    except ValueError:
        return
    raise AssertionError("Accepted invalid definition: " + repr(value))


def t_defaults_and_unicode_text_preserved():
    data = clean(raw(label="  Grüße <b>Road</b>  "))
    assert data.get("id") == ID and data.get("label") == "Grüße <b>Road</b>", data
    assert data.get("target") is None and data.get("manualValue") == 18, data
    assert data.get("source") == "manual" and data.get("metric") == "value", data
    assert data.get("baseline") is None and data.get("mode") == "total", data


def t_invalid_numbers_and_ranges():
    for key in ("manualValue", "start", "target"):
        for value in (True, float("nan"), float("inf"), "18"):
            rejected(raw(**{key: value}))
    rejected(raw(start=10, target=10))
    rejected(raw(start=11, target=10))
    assert clean(raw(start=-1e308, target=1e308))["target"] == 1e308


def t_invalid_shape_identity_and_fields():
    for value in (
        None,
        [],
        {},
        raw(id="../x"),
        raw(label="  "),
        raw(unit=3),
        raw(extra="no"),
        raw(label="x" * 121),
    ):
        rejected(value)
    for decimals in (-1, 7, True, 1.5):
        rejected(raw(decimals=decimals))


def t_metric_identity_and_precision_contract():
    assert clean(raw(source="youtube", metric="likes", mode="net"))["metric"] == "likes"
    assert (
        clean(raw(source="twitch", metric="subscriptions", mode="net"))["metric"]
        == "subscriptions"
    )
    for options in (
        {"source": "unknown"},
        {"source": "youtube", "metric": "value"},
        {"source": "youtube", "metric": "subscribers", "mode": "net"},
        {"source": "twitch", "metric": "viewers", "mode": "net"},
    ):
        rejected(raw(**options))


def t_baseline_must_be_explicit_finite_and_targeted():
    baseline = {"value": 18, "identity": "manual:" + ID, "capturedAt": 0}
    assert clean(raw(mode="net", baseline=baseline))["baseline"] == baseline
    for baseline in (
        {"value": True, "identity": "fixture", "capturedAt": 0},
        {"value": 18, "identity": "", "capturedAt": 0},
        {"value": float("inf"), "identity": "fixture", "capturedAt": 0},
    ):
        rejected(raw(mode="net", baseline=baseline))


from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import subprocess
import sys
from unittest.mock import patch


def t_manual_reset_and_baseline_survive_new_instances():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audience.json"
        first = Store(path, clock=lambda: 100)
        goal = first.create({"label": "Hours", "manualValue": 18, "target": 24})
        assert len(first.read()["widgets"]) == 1
        ident = goal["id"]
        second = Store(path, clock=lambda: 101)
        second.update(ident, {"mode": "net"})
        second.reset(ident)
        saved = Store(path).read()["widgets"][0]
        assert saved["manualValue"] == 18 and saved["target"] == 24
        assert (
            saved["baseline"]["value"] == 18 and saved["baseline"]["capturedAt"] == 101
        )
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,sys;sys.path.insert(0,sys.argv[2]);from audience_widgets import Store;print(json.dumps(Store(sys.argv[1]).read()))",
                str(path),
                str(ROOT / "src/scripts"),
            ],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            errors="replace",
            check=True,
        )
        restarted = json.loads(child.stdout)["widgets"][0]
        assert restarted["id"] == ident and restarted["baseline"] == saved["baseline"]
        assert restarted["manualValue"] == 18 and restarted["target"] == 24
        second.update(ident, {"mode": "total"})
        second.reset(ident)
        saved = Store(path).read()["widgets"][0]
        assert (
            saved["manualValue"] == 0
            and saved["target"] == 24
            and saved["baseline"] is None
        )


def t_disk_failure_and_invalid_definition_preserve_exact_file():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audience.json"
        store = Store(path)
        goal = store.create({"label": "Likes", "manualValue": 0, "target": 750})
        before = path.read_bytes()
        for bad in (
            {"target": 0},
            {"manualValue": float("nan")},
            {"id": "audience-" + "b" * 32},
            {"baseline": {"value": 1, "identity": "x", "capturedAt": 0}},
            {"updatedAt": 0},
        ):
            try:
                store.update(goal["id"], bad)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid update accepted")
            assert path.read_bytes() == before
        with patch("os.replace", side_effect=OSError("fixture disk full")):
            try:
                store.update(goal["id"], {"manualValue": 761})
            except OSError:
                pass
            else:
                raise AssertionError("Disk error not reported")
        assert path.read_bytes() == before
        assert not list(path.parent.glob("*.tmp"))


def t_two_store_instances_preserve_other_goal_and_profile():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "a/audience.json"
        a = Store(path)
        b = Store(path)
        one = a.create({"label": "One", "manualValue": 1})
        two = b.create({"label": "Two", "manualValue": 2})
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [
                pool.submit(a.update, one["id"], {"manualValue": 11}),
                pool.submit(b.update, two["id"], {"manualValue": 22}),
            ]
            [j.result() for j in jobs]
        values = {w["id"]: w["manualValue"] for w in Store(path).read()["widgets"]}
        assert values == {one["id"]: 11, two["id"]: 22}, values
        other = Store(Path(td) / "b/audience.json")
        assert other.read() == {"version": 1, "widgets": []}
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def t_source_available_before_baseline_and_cache_is_central():
    goal = widget(source="youtube", metric="likes", mode="net")
    cache = aw.MetricCache(clock=lambda: 100)
    cache.publish(
        "youtube", "likes", Sample(18, "available", 100, identity="video:one")
    )
    for _ in range(3):
        data = normalize(goal, cache.read("youtube", "likes"), now=100)
        assert (
            not data["available"]
            and data["sourceAvailable"]
            and data["sourceIdentity"] == "video:one"
        ), data
    assert cache.read("youtube", "viewers").availability == "unknown"


def t_explicit_capture_delete_and_bounded_definition_count():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "audience.json", clock=lambda: 100)
        goal = store.create({
            "label": "Likes",
            "source": "youtube",
            "metric": "likes",
            "mode": "net",
            "target": 20,
        })
        for sample in (
            Sample(18, "stale", 90, identity="video:one"),
            Sample(18, "available", 100, exact=False),
            Sample(),
        ):
            try:
                store.capture(goal["id"], sample)
            except ValueError:
                pass
            else:
                raise AssertionError("Captured unavailable or inexact source")
        captured = store.capture(
            goal["id"], Sample(18, "available", 100, identity="video:one")
        )
        assert captured["baseline"] == {
            "value": 18,
            "identity": "video:one",
            "capturedAt": 100,
        }
        assert captured["target"] == 20
        store.delete(goal["id"])
        assert store.read()["widgets"] == []
        for index in range(aw.MAX_WIDGETS):
            store.create({"label": str(index)})
        before = store.path.read_bytes()
        try:
            store.create({"label": "one too many"})
        except ValueError:
            pass
        else:
            raise AssertionError("Unbounded widgets accepted")
        assert store.path.read_bytes() == before


def t_html_escaped_unique_and_live_placement_snapshot():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "audience.json")
        one = store.create({"label": "Likes & <b>Subs</b>", "unit": "<img src=x>"})
        two = store.create({"label": "Other"})
        base = '<html><head><style>body{margin:0}</style></head><body><div id="old"></div><script>window.old=true;</script></body></html>'
        assert aw.enhance_html(base, store.read()["widgets"], {}) == base
        body = aw.enhance_html(
            base, store.read()["widgets"], {one["id"]: {}}, live=True
        )
        assert body.count("data-audience-widget") == 1 and two["id"] not in body
        assert (
            "<b>Subs</b>" not in body and "Likes &amp; &lt;b&gt;Subs&lt;/b&gt;" in body
        )
        assert body.count("data-audience-live") == 1 and "hidden" in body
        assert body.index("data-audience-widget") < body.index("data-audience-live")
        from overlay_build import extract_slots

        slots = extract_slots(body)
        assert slots[0]["label"] == one["label"], slots
        assert "presentation" in slots[0]["props"] and "align" not in slots[0]["props"]


def t_second_writer_cannot_read_until_first_write_commits():
    import threading

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "audience.json"
        one = Store(path)
        a = one.create({"label": "A", "manualValue": 1})
        b = one.create({"label": "B", "manualValue": 2})
        two = Store(path)
        entered = threading.Event()
        release = threading.Event()
        second_read = threading.Event()
        write = one._write
        read = two._read

        def blocked(data):
            entered.set()
            assert release.wait(3), "Test writer was not released"
            write(data)

        def observed():
            second_read.set()
            return read()

        with (
            patch.object(one, "_write", side_effect=blocked),
            patch.object(two, "_read", side_effect=observed),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(one.update, a["id"], {"manualValue": 11})
                assert entered.wait(3)
                second = pool.submit(two.update, b["id"], {"manualValue": 22})
                try:
                    assert not second_read.wait(0.1), (
                        "Second writer read an uncommitted snapshot"
                    )
                finally:
                    release.set()
                first.result()
                second.result()
        assert {
            w["label"]: w["manualValue"] for w in Store(path).read()["widgets"]
        } == {"A": 11, "B": 22}


def t_invalid_configuration_keeps_existing_overlay_and_last_good_client_contract():
    with tempfile.TemporaryDirectory() as td:
        directory = Path(td) / "overlay"
        directory.mkdir()
        store = Store(directory.parent / "audience.json")
        goal = store.create({"label": "Likes", "manualValue": 0, "target": 750})
        (directory / "layout-hud.json").write_text(
            json.dumps({"slots": {goal["id"]: {}}}), encoding="utf-8"
        )
        runtime = aw.Runtime(store.path, clock=lambda: 100)
        assert runtime.data()["widgets"][0]["value"] == 0
        store.path.write_text("{broken", encoding="utf-8")
        data = runtime.data()
        assert data == {
            "ok": False,
            "error": "Could not read audience configuration",
        }, data
        base = "<html><style></style><body></body></html>"
        assert aw.live_html(base, str(directory), "hud") == base
        for raw_file in (
            {"version": 1, "widgets": [goal, goal]},
            {"version": 1, "widgets": [{"id": goal["id"]}]},
        ):
            store.path.write_text(json.dumps(raw_file), encoding="utf-8")
            assert runtime.data()["ok"] is False


def t_scene_presentation_flags_and_colors_are_bounded():
    from overlay_build import compile_overlay_css, extract_slots

    goal = clean(raw())
    slots = extract_slots(aw.markup(goal))
    data = {
        "slots": {
            ID: {
                "presentation": "number",
                "showNumber": False,
                "showPercent": False,
                "barColor": "#27d34d",
                "trackColor": "#123456",
                "madeUp": "bad",
            }
        }
    }
    css = compile_overlay_css(data, slots)
    assert "--audience-target: none" in css and "--audience-track: none" in css
    assert "--audience-percent-mode: none" in css and "--audience-percent: none" in css
    assert "--audience-number: none" in css and "--audience-bar-color: #27d34d" in css
    assert "--audience-track-color: #123456" in css and "madeUp" not in css
    for value in ("bar; color:red", "bogus", True, [], {}):
        css = compile_overlay_css(
            {
                "slots": {
                    ID: {
                        "presentation": value,
                        "showPercent": "false",
                        "barColor": "red;display:none",
                    }
                }
            },
            slots,
        )
        assert "--audience-" not in css, css


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
