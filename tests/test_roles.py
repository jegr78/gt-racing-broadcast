#!/usr/bin/env python3
"""Stdlib unit checks for the Crew roster + role resolution (#216 phase 1).
Run: python3 tests/test_roles.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_crew_truthy_allowlist():
    for yes in ("x", "X", " yes ", "TRUE", "1", "y", "✓"):
        assert m._crew_truthy(yes), yes
    for no in ("", " ", "0", "no", "false", "-", "maybe"):
        assert not m._crew_truthy(no), no


def t_parse_header_mode_locates_columns_by_name():
    text = "Name,Director,Producer\nAlice,X,X\nBob,x,\nCarol,,\n"
    rows = m.CrewSource._parse_rows(text)
    assert rows == [("Alice", True, True),
                    ("Bob", True, False),
                    ("Carol", False, False)], rows


def t_parse_header_mode_columns_may_move_and_extras_ignored():
    # Producer left of Director, plus an unrelated Contact column.
    text = "Name,Contact,Producer,Director\nAlice,@a,X,\n"
    rows = m.CrewSource._parse_rows(text)
    assert rows == [("Alice", False, True)], rows


def t_parse_skips_blank_name_rows():
    text = "Name,Director,Producer\n,X,\nBob,X,\n"
    rows = m.CrewSource._parse_rows(text)
    assert rows == [("Bob", True, False)], rows


def t_parse_positional_fallback_no_header():
    # No recognized name header -> col0=name, col1=director, col2=producer.
    text = "Alice,X,X\nBob,x,\n"
    rows = m.CrewSource._parse_rows(text)
    assert rows == [("Alice", True, True), ("Bob", True, False)], rows


def t_parse_positional_fallback_skips_headerlike_first_row():
    # A header-like first row, where col1 and col2 are header words, is dropped even
    # when the name header itself is unrecognized.
    text = "Person?,Director,Producer\nAlice,X,\n"
    rows = m.CrewSource._parse_rows(text)
    assert rows == [("Alice", True, False)], rows


def t_parse_empty_returns_none():
    assert m.CrewSource._parse_rows("") is None
    assert m.CrewSource._parse_rows("\n") is None


def t_crewsource_no_url_refresh_is_false_and_get_empty():
    src = m.CrewSource(csv_url="")
    assert src.refresh() is False
    assert src.get() == []


def t_crewsource_get_returns_snapshot_copy():
    src = m.CrewSource(csv_url="")
    src.rows = [("Alice", True, False, False, False, "")]   # canonical 6-tuple store
    snap = src.get()                                 # get() projects to 3-tuples
    assert snap == [("Alice", True, False)]
    snap.append(("X", False, False))       # mutating the snapshot must not leak
    assert src.get() == [("Alice", True, False)]


def t_crewsource_get_full_returns_six_tuples():
    # The canonical store is (name, dir, prod, commentator, race_control, discord). (#244)
    src = m.CrewSource(csv_url="")
    src.rows = [("Alice", True, False, True, False, "alice_d")]
    full = src.get_full()
    assert full == [("Alice", True, False, True, False, "alice_d")]
    full.append(("X", False, False, False, False, ""))   # snapshot copy, no leak
    assert src.get_full() == [("Alice", True, False, True, False, "alice_d")]


def t_resolve_commentator_from_schedule_only():
    roles = m.resolve_roles([], {"alice"}, "alice")
    assert roles == {"commentator"}, roles


def t_resolve_director_and_producer_from_crew():
    crew = [("Alice", True, True), ("Bob", True, False)]
    # Alice is producer, and producer additively implies director and race_control.
    assert m.resolve_roles(crew, set(), "alice") == {
        "director", "producer", "race_control"}
    assert m.resolve_roles(crew, set(), "bob") == {"director"}


def t_resolve_producer_implies_director_and_race_control():
    # A pure producer, with no Director or Race Control crew flag and not in the
    # schedule, still oversees the whole event: producer grants director for control
    # and race_control for read-only monitoring, so they can steer the broadcast from
    # the /console pages.
    crew = [("Alice", False, True)]
    assert m.resolve_roles(crew, set(), "alice") == {
        "producer", "director", "race_control"}
    # A producer who is also in the schedule keeps commentator as well.
    assert m.resolve_roles(crew, {"alice"}, "alice") == {
        "producer", "director", "race_control", "commentator"}
    assert m.resolve_roles([("Bob", True, False)], set(), "bob") == {"director"}, \
        "a non-producer is unaffected; no implication leaks to plain directors"


def t_resolve_multi_role_union_commentator_plus_director():
    crew = [("Alice", True, False)]
    assert m.resolve_roles(crew, {"alice"}, "alice") == {"commentator", "director"}


def t_resolve_name_normalized_via_asset_key():
    # "Alice O'Brien" normalizes to the same key the token carries.
    subject = m.asset_key("Alice O'Brien")
    crew = [("Alice O'Brien", False, True)]
    # producer implies director and race_control
    assert m.resolve_roles(crew, set(), subject) == {
        "producer", "director", "race_control"}


def t_resolve_unknown_subject_is_empty():
    crew = [("Alice", True, True)]
    assert m.resolve_roles(crew, {"alice"}, "stranger") == set()


def t_schedule_keys_normalizes_and_skips_blank():
    rows = [("https://youtu.be/a", "Alice", "1", 2),
            ("", "Bob O'Brien", "2", 3),
            ("https://youtu.be/c", "", "3", 4)]   # blank streamer -> skipped
    assert m.schedule_keys(rows) == {"alice", m.asset_key("Bob O'Brien")}


def t_schedule_keys_empty():
    assert m.schedule_keys([]) == set()


# The live HTTP surface, /crew/data.

def _crew_client(crew_rows):
    """make_handler over a real loopback server, wired with a fake crew_source or
    None. Returns (server, get)."""
    import threading as _t, json as _json
    from urllib.request import urlopen

    class _Feed:
        def __init__(self, idx): self.idx = idx

    class _Source:
        def get_rows(self): return []
        def health(self): return {"count": 0}

    class _Relay:
        def __init__(self):
            self.source = _Source(); self.mode = "race"
            self.feeds = {"A": _Feed(0), "B": _Feed(1)}

    class _Crew:
        def __init__(self, rows): self._rows = rows
        def get_full(self): return list(self._rows)

    crew = _Crew(crew_rows) if crew_rows is not None else None
    handler = m.make_handler(_Relay(), crew_source=crew)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path):
        with urlopen(base + path, timeout=5) as r:
            return r.status, _json.loads(r.read().decode())
    return srv, get


def t_crew_data_endpoint_returns_rows():
    srv, get = _crew_client([("Alice", True, True, False, False, "alice_d"),
                             ("Bob", True, False, True, True, "")])
    try:
        status, body = get("/crew/data")
        assert status == 200, status
        assert body == {"rows": [
            {"name": "Alice", "director": True, "producer": True,
             "commentator": False, "race_control": False, "discord": "alice_d"},
            {"name": "Bob", "director": True, "producer": False,
             "commentator": True, "race_control": True, "discord": ""}]}, body
    finally:
        srv.shutdown()


def t_crew_data_endpoint_empty_when_disabled():
    srv, get = _crew_client(None)   # no crew_source -> crew disabled
    try:
        status, body = get("/crew/data")
        assert status == 200, status
        assert body == {"rows": []}, body
    finally:
        srv.shutdown()


def t_crew_source_inject_row_edit_append_and_delete():
    cs = m.CrewSource("http://crew")
    cs.rows = [("Alice", True, False, False, False, "")]
    cs.inject_row(2, name="Bob", director=False, producer=True)   # append at len+1
    assert cs.get() == [("Alice", True, False), ("Bob", False, True)]
    cs.inject_row(1, director=False)                              # partial edit in place
    assert cs.get()[0] == ("Alice", False, False)
    cs.delete_row(1)
    assert cs.get() == [("Bob", False, True)]
    cs.delete_row(9)                                             # out of range = no-op
    assert cs.get() == [("Bob", False, True)]


def t_crew_source_inject_row_commentator_discord_partial():
    # commentator, race_control and discord follow the same applied-when-given,
    # kept-when-None rule
    cs = m.CrewSource("http://crew")
    cs.rows = [("Alice", True, False, False, False, "")]
    cs.inject_row(2, name="Bob", commentator=True, discord="Bob.Handle")  # append
    assert cs.get_full()[1] == ("Bob", False, False, True, False, "Bob.Handle")
    cs.inject_row(2, director=True)                              # partial edit keeps the rest
    assert cs.get_full()[1] == ("Bob", True, False, True, False, "Bob.Handle")
    cs.inject_row(1, discord="alice_d")                         # only discord changes
    assert cs.get_full()[0] == ("Alice", True, False, False, False, "alice_d")


def t_crew_source_inject_row_race_control_partial():
    cs = m.CrewSource("http://crew")
    cs.rows = [("Alice", True, False, False, False, "")]
    cs.inject_row(2, name="Bob", race_control=True)             # append, race_control set
    assert cs.get_full()[1] == ("Bob", False, False, False, True, "")
    cs.inject_row(2, commentator=True)                         # partial edit keeps race_control
    assert cs.get_full()[1] == ("Bob", False, False, True, True, "")
    cs.inject_row(1, race_control=True)                        # only race_control changes
    assert cs.get_full()[0] == ("Alice", True, False, False, True, "")


def t_crew_discord_and_commentator_columns():
    csv_text = ("Name,Commentator,Director,Producer,Discord\n"
                "Alice,,x,,alice_d\n"
                "Bob,x,,,Bob.Handle\n"
                "Carol,,,,\n")
    rows = m.CrewSource._parse_rows(csv_text)
    # the get() shape stays (name, is_dir, is_prod)
    assert ("Alice", True, False) in rows, rows
    assert ("Bob", False, False) in rows, rows
    src = m.CrewSource("")          # no URL; inject rows directly
    src.rows = m.CrewSource._parse_full(csv_text)   # canonical 5-tuple store
    dm = src.discord_map()
    assert dm.get("alice_d") == "Alice", dm
    assert dm.get("bob.handle") == "Bob", dm   # lowercased key
    assert src.commentator_keys() == {m.asset_key("Bob")}, src.commentator_keys()


def t_resolve_roles_a1_union_commentator_from_crew_flag():
    crew = [("Alice", True, False)]
    # subject not in the schedule, but in the crew commentator set -> commentator
    roles = m.resolve_roles(crew, set(), m.asset_key("Bob"),
                            crew_commentator_keys={m.asset_key("Bob")})
    assert roles == {"commentator"}, roles
    # the schedule still auto-grants
    roles2 = m.resolve_roles(crew, {m.asset_key("Dan")}, m.asset_key("Dan"))
    assert roles2 == {"commentator"}, roles2
    # director from the crew flag, unioned with commentator from the schedule
    roles3 = m.resolve_roles(crew, {m.asset_key("Alice")}, m.asset_key("Alice"),
                             crew_commentator_keys=set())
    assert roles3 == {"commentator", "director"}, roles3


# The Race Control role (#244).

def t_crew_race_control_column_parsed_and_keys():
    # Header-mode parsing locates the "Race Control" column by name, and the get()
    # 3-tuple (name, dir, prod) is unchanged so existing callers keep working.
    csv_text = ("Name,Commentator,Director,Producer,Race Control,Discord\n"
                "Alice,,x,,x,alice_d\n"
                "Bob,x,,,,Bob.Handle\n"
                "Carol,,,,yes,\n")
    rows = m.CrewSource._parse_rows(csv_text)
    assert ("Alice", True, False) in rows, rows
    src = m.CrewSource("")
    src.rows = m.CrewSource._parse_full(csv_text)   # canonical 6-tuple store
    assert src.race_control_keys() == {m.asset_key("Alice"), m.asset_key("Carol")}, \
        src.race_control_keys()
    # the other column helpers stay correct alongside the new column
    assert src.commentator_keys() == {m.asset_key("Bob")}, src.commentator_keys()
    assert src.discord_map().get("alice_d") == "Alice", src.discord_map()


def t_race_control_header_alias_columns():
    for header in ("Race Control", "Race-Control", "RaceControl", "RC"):
        csv_text = f"Name,{header}\nAlice,x\nBob,\n"
        src = m.CrewSource("")
        src.rows = m.CrewSource._parse_full(csv_text)
        assert src.race_control_keys() == {m.asset_key("Alice")}, (header, src.race_control_keys())


def t_resolve_roles_race_control_union_additive():
    crew = [("Alice", True, False)]   # Alice is a director
    # Alice is also race_control, so she holds both roles.
    roles = m.resolve_roles(crew, set(), m.asset_key("Alice"),
                            crew_race_control_keys={m.asset_key("Alice")})
    assert roles == {"director", "race_control"}, roles
    # A pure race-control desk operator: not in the schedule, no other crew flag.
    roles2 = m.resolve_roles([], set(), m.asset_key("Dana"),
                             crew_race_control_keys={m.asset_key("Dana")})
    assert roles2 == {"race_control"}, roles2
    # Unknown subject stays empty even with a race-control set present.
    assert m.resolve_roles([], set(), "stranger",
                           crew_race_control_keys={m.asset_key("Dana")}) == set()


def t_race_control_keys_positional_fallback_empty():
    # With no Name header the positional fallback parses name, dir and prod only, and
    # nothing can locate a Race Control column, so the set is empty, as for commentator.
    src = m.CrewSource("")
    src.rows = m.CrewSource._parse_full("Alice,x,\nBob,,x\n")
    assert src.race_control_keys() == set(), src.race_control_keys()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
