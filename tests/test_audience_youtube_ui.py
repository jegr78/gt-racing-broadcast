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


ROOT = Path(__file__).resolve().parent.parent


def t_youtube_dialog_offers_follow_mode():
    page = (ROOT / "src/ui/control-center.html").read_text(encoding="utf-8")
    for needle in (
        'id="youtube-mode-follow"',
        'id="youtube-mode-video"',
        'id="youtube-follow-channel"',
        "Follow the live broadcast of the Sheet channel",
        "Fixed video",
        "Each new live broadcast has a new video ID: enter it, verify and "
        "activate before going live.",
        "HUD-Overlays#youtube-data-api-key",
        "Follow mode needs the Sheet Channel tab",
    ):
        assert needle in page, needle
    start = page.index("async function youtubeAction(")
    action = page[start : page.index("\n}\n", start)]
    assert "body.mode" in action and "body.followChannel" in action, action


def t_wiki_documents_the_api_key_steps():
    wiki = (ROOT / "src/docs/wiki/HUD-Overlays.md").read_text(encoding="utf-8")
    for needle in (
        "### YouTube Data API key",
        "YouTube Data API v3",
        "Create credentials",
        "new video ID",
    ):
        assert needle in wiki, needle


if __name__ == "__main__":
    t_automatic_capture_uses_fresh_cache_and_rejects_profile_change_during_rpc()
    t_provider_preflight_rejects_absent_process_and_display_name_as_profile_slug()
    t_rounded_values_show_the_number_without_an_approximate_marker()
    t_youtube_dialog_offers_follow_mode()
    t_wiki_documents_the_api_key_steps()
    print("PASS automatic cached baseline and provider preflight callbacks")
