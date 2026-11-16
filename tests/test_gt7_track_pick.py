#!/usr/bin/env python3
"""The event's GT7 layout pick (#1129). Run: python3 tests/test_gt7_track_pick.py"""
import json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))

import gt7_track_pick as tp  # noqa: E402


class _Tracks:
    CAT = {"aaa001": {"id": "aaa001", "track": "Oval", "layout": "Full", "reverse": False},
           "bbb001": {"id": "bbb001", "track": "Ring", "layout": "Full", "reverse": True}}

    def name(self, oid):
        return dict(self.CAT[oid]) if oid in self.CAT else None

    def has_downloaded_line(self, oid):
        return oid == "aaa001"

    def layouts(self):
        return [dict(v) for v in self.CAT.values()]


def t_store_persists_validates_and_clears():
    with tempfile.TemporaryDirectory() as d:
        path = tp.path_in(d)
        st = tp.Store(path, _Tracks())
        assert st.current() is None
        got = st.set("aaa001")
        assert got == {"id": "aaa001", "track": "Oval", "layout": "Full", "reverse": False,
                       "line": True, "warning": None}, got
        with open(path, encoding="utf-8") as fh:
            assert json.load(fh) == {"id": "aaa001"}
        assert tp.Store(path, _Tracks()).current()["id"] == "aaa001", "the pick must survive a restart"
        try:
            st.set("nope00")
        except ValueError as exc:
            assert "unknown" in str(exc)
        else:
            raise AssertionError("an unknown layout must be refused")
        assert st.current()["id"] == "aaa001", "a refused pick must keep the old one"
        assert st.set("") is None and st.current() is None and not os.path.exists(path)


def t_layout_without_line_warns_and_unknown_id_is_no_pick():
    info = tp.describe(_Tracks(), "bbb001")
    assert info["line"] is False
    assert info["warning"].startswith("Ring Full (reverse) has no downloaded racing line."), info
    assert "gt7-data update" in info["warning"], info["warning"]
    with tempfile.TemporaryDirectory() as d:
        path = tp.path_in(d)
        tp.write_id(path, "gone00")
        assert tp.read_id(path) == "gone00"
        assert tp.Store(path, _Tracks()).current() is None, "an id the catalogue lost is no pick"
    assert tp.describe(None, "aaa001") is None


def t_layouts_flag_the_line():
    rows = {r["id"]: r["line"] for r in tp.layouts(_Tracks())}
    assert rows == {"aaa001": True, "bbb001": False}, rows


class _Telemetry:
    def __init__(self):
        self.picks, self.live = [], {"track": None, "recognised": None, "mismatch": False}

    def set_track_pick(self, info):
        self.picks.append(info)

    def track_status(self):
        return self.live


def t_controller_applies_writes_the_sidecar_and_reports_a_mismatch():
    with tempfile.TemporaryDirectory() as d:
        tele = _Telemetry()
        rec = os.path.join(d, "20261116-1000.gt7rec")
        ctl = tp.Controller(tp.Store(tp.path_in(d), _Tracks()), tele, recording=lambda: rec)
        st = ctl.set("aaa001")
        assert tele.picks[-1]["id"] == "aaa001", tele.picks
        assert tp.recording_pick(rec + ".part") == "aaa001", "the open recording must carry the pick"
        assert st["pick"]["id"] == "aaa001" and st["mismatch"] is False and ctl.mismatch_text() is None
        tele.live = {"track": None, "mismatch": True,
                     "recognised": {"id": "bbb001", "track": "Ring", "layout": "Full", "reverse": False}}
        text = ctl.mismatch_text()
        assert text and "Ring Full" in text and "picked Oval Full" in text, text
        ctl.set(None)
        assert tele.picks[-1] is None, tele.picks
        assert tp.recording_pick(rec) is None and not os.path.exists(tp.sidecar_path(rec)), \
            "clearing must remove the open recording's pick"
        assert "layouts" in ctl.status(with_layouts=True) and "layouts" not in ctl.status()


def t_sidecar_sits_beside_the_recording():
    assert tp.sidecar_path("/r/20261116-1000.gt7rec.part") == "/r/20261116-1000.track.json"
    assert tp.sidecar_path("/r/20261116-1000.gt7rec") == "/r/20261116-1000.track.json"
    assert tp.recording_pick(None) is None

def t_a_new_recording_carries_the_pick_through_the_writer_hook():
    import gt7_recording
    with tempfile.TemporaryDirectory() as d:
        ctl = tp.Controller(tp.Store(tp.path_in(d), _Tracks()))
        ctl.store.set("aaa001")
        w = gt7_recording.RecordingWriter(d, "Solo", "dev", flush_s=0.05,
                                          on_open=lambda path, _d, _rid: ctl.assign(path))
        w.put(1_700_000_000.0, "A", b"\0" * 296)
        w.close()
        assert w.path and os.path.exists(w.path), "the writer must have opened a recording"
        assert tp.recording_pick(w.path) == "aaa001", \
            f"the finished recording must carry the pick: {os.listdir(d)}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
