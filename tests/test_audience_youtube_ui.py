"""Profile-safe Control Center callbacks consume relay cache only."""

from pathlib import Path
import tempfile
from unittest.mock import patch
import test_racecast as cli
import audience_widgets as aw

m = cli.m


def t_automatic_capture_uses_fresh_cache_and_rejects_profile_change_during_rpc():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            store = aw.Store(Path(td) / "profiles/demo/audience.json")
            goal = store.create({
                "label": "Likes",
                "source": "youtube",
                "metric": "likes",
                "mode": "net",
            })
            sample = aw.Sample(
                18,
                "available",
                100,
                identity="youtube:video:abcdefghijk:likes",
                age_seconds=0,
            )
            with patch.object(
                m, "_audience_provider_sample", return_value=sample, create=True
            ):
                result = m.audience_write_data({
                    "expectedProfile": "demo",
                    "action": "capture",
                    "id": goal["id"],
                })
            assert result["ok"] and result["widget"]["baseline"]["value"] == 18, result
            with (
                patch.object(
                    m, "_audience_provider_sample", return_value=sample, create=True
                ),
                patch.object(m, "_active_profile_name", side_effect=["demo", "other"]),
            ):
                result = m.audience_write_data({
                    "expectedProfile": "demo",
                    "action": "capture",
                    "id": goal["id"],
                })
            assert not result["ok"] and "profile" in result["error"].lower(), result
        finally:
            m._env_base, m._runtime_base_dir = original


def t_provider_preflight_rejects_absent_process_and_display_name_as_profile_slug():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            for pid, stamp in ((None, "demo"), (123, "Demo Racing")):
                with (
                    patch.object(m.sv, "read_pid", return_value=pid),
                    patch.object(m, "_running_relay_profile", return_value=stamp),
                    patch.object(
                        m.http_util,
                        "open_url",
                        side_effect=AssertionError(
                            "Rejected preflight contacted a port"
                        ),
                    ),
                ):
                    result = m.audience_youtube_data()
                    assert not result["ok"] and "profile" in result["error"].lower(), (
                        result
                    )
        finally:
            m._env_base, m._runtime_base_dir = original


def t_rounded_values_show_the_number_without_an_approximate_marker():
    # Producer decision: subscribers show the plain number. The rounding stays in the
    # precision metadata (no exact net goals), never as a "≈" in front of the value.
    root = Path(__file__).resolve().parent.parent / "src" / "ui"
    for name in ("audience.js", "control-center.html"):
        text = (root / name).read_text(encoding="utf-8")
        assert "≈" not in text and "\u2248" not in text, name


if __name__ == "__main__":
    t_automatic_capture_uses_fresh_cache_and_rejects_profile_change_during_rpc()
    t_provider_preflight_rejects_absent_process_and_display_name_as_profile_slug()
    t_rounded_values_show_the_number_without_an_approximate_marker()
    print("PASS automatic cached baseline and provider preflight callbacks")
