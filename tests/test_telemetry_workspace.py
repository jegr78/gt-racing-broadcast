#!/usr/bin/env python3
"""Local workspace routes and concurrent analysis-profile boundaries."""
import concurrent.futures
import contextlib
import inspect
import importlib.util
import json
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workspace_racecast", ROOT / "src/racecast.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sys.path.insert(0, str(ROOT / "src/ui"))
import ui_server as us
import ui_jobs


def t_launcher_routes_to_telemetry_and_reuses_the_shared_instance():
    route = m.route(["telemetry", "ui", "--no-browser"])
    assert route["verb"] == "ui"
    with patch.object(m, "run_ui") as run:
        m.telemetry_ui_cmd(["--no-browser"])
        assert run.call_args.kwargs["destination"].startswith("/telemetry")
        assert run.call_args.kwargs["open_browser"] is False


def t_launcher_refuses_a_reused_service_without_the_workspace_capability():
    with patch.object(m, "_ui_modules", return_value=(us, None, None)), patch.object(m, "_read_env_file", return_value={}), patch.object(us, "probe_instance", return_value="ours"), patch.object(m.http_util, "get_json", return_value={"app": us.APP_ID}), patch.object(m, "_open_url") as opened:
        try:
            m.run_ui([], destination="/telemetry", fail=lambda message: (_ for _ in ()).throw(ValueError(message)))
        except ValueError as exc:
            assert "restart" in str(exc).lower()
        else:
            raise AssertionError("Old UI service accepted a destination it cannot serve")
        opened.assert_not_called()


@contextlib.contextmanager
def profiles():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("alpha", "beta"):
            directory = root / "profiles" / name
            directory.mkdir(parents=True)
            (directory / "profile.env").write_text("RACECAST_PROFILE_NAME=" + name + "\n", encoding="utf-8")
        machine = root / "runtime"
        machine.mkdir()
        with patch.object(m, "_env_base", return_value=str(root)), patch.object(m, "_runtime_base_dir", return_value=str(machine)), patch.dict(os.environ, {"RACECAST_PROFILE": "alpha"}):
            yield root


def t_profile_scope_is_concurrent_validated_and_restored():
    with profiles():
        barrier = threading.Barrier(2)
        def read(name):
            with m.telemetry_profile_scope(name):
                barrier.wait(timeout=5)
                assert m._active_profile_name() == name
                assert Path(m._telemetry_rec_dir()).parts[-2:] == (name, "telemetry-recordings")
            assert m._active_profile_name() == "alpha"
            return name
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            assert list(pool.map(read, ("alpha", "beta"))) == ["alpha", "beta"]
        for bad in ("", "../beta", "missing"):
            try:
                with m.telemetry_profile_scope(bad):
                    raise AssertionError("invalid analysis profile accepted")
            except ValueError:
                pass
        assert os.environ["RACECAST_PROFILE"] == "alpha"


def t_recording_protection_follows_the_relay_owner_not_a_same_named_archive():
    with profiles(), patch.object(m, "_relay_http_ok", return_value=True), patch.object(m, "_relay_record_status", return_value={"file": "shared-recording"}):
        Path(m._relay_profile_path()).write_text("alpha", encoding="utf-8")
        with m.telemetry_profile_scope("beta"):
            assert m._relay_open_file() is None
        with m.telemetry_profile_scope("alpha"):
            assert m._relay_open_file() == "shared-recording"


def t_profile_jobs_are_independent_but_machine_reference_updates_are_exclusive():
    release = threading.Event()
    class Pipe(io.BytesIO):
        def __iter__(self):
            release.wait(timeout=5)
            return super().__iter__()
    class Process:
        def __init__(self):self.stdout=Pipe()
        def wait(self):return 0
    manager=ui_jobs.JobManager(lambda argv:argv, spawn=lambda _argv:Process())
    try:
        first,error=manager.start("telemetry-index",["--profile","alpha","telemetry","index"])
        second,error=manager.start("telemetry-index",["--profile","beta","telemetry","index"])
        assert first!=second and error is None
        joined,error=manager.start("telemetry-index",["--profile","alpha","telemetry","index"])
        assert joined==first and error
        first,error=manager.start("shift-reference-update",["--profile","alpha","telemetry","shift-update","42"])
        joined,error=manager.start("shift-reference-update",["--profile","beta","telemetry","shift-update","42"])
        assert joined==first and error, "Machine-wide reference cache updates must retain exclusive admission"
    finally:
        release.set()


@contextlib.contextmanager
def server(ai_busy=False, shutdown_state=None):
    ctx = {"page_path": str(ROOT / "src/ui/control-center.html"), "version": "test",
           "favicon_path": str(ROOT / "src/assets/app-icon.svg"),
           "telemetry_profile_scope": getattr(m, "telemetry_profile_scope", lambda _name: contextlib.nullcontext()),
           "shutdown_state": shutdown_state or (lambda: {"ai_busy": ai_busy}),
           "telemetry_recordings": lambda: {"ok": True, "profile": m._active_profile_name(), "directory": m._telemetry_rec_dir()},
           "ai_request": lambda op, payload: {"ok": True, "profile": m._active_profile_name()},
           "ops": {"telemetry-index": ["telemetry", "index"], "relay-start": ["relay", "start"]},
           "build_argv": lambda name, params: ["telemetry", "index"],
           "jobs": type("Jobs", (), {"start": lambda self, name, argv: ("/".join(argv), None),
                                    "snapshot": lambda self, job_id: {"id": job_id, "profile": "beta", "exit_code": 0}})()}
    srv = us.serve(ctx, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    def request(path, body=None, headers=None):
        req = urllib.request.Request("http://127.0.0.1:" + str(srv.server_address[1]) + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read()
    try:
        yield request
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def t_dedicated_page_and_scoped_requests_do_not_change_broadcast_profile():
    with profiles(), server() as request:
        code, body = request("/telemetry")
        assert code == 200 and b"Telemetry" in body
        for name in ("alpha", "beta", "alpha"):
            code, body = request("/api/telemetry/recordings?profile=" + name)
            assert code == 200 and json.loads(body)["profile"] == name
        code, body = request("/api/ai/status?profile=beta")
        assert code == 200 and json.loads(body)["profile"] == "beta"
        assert m._active_profile_name() == "alpha"


def t_scoped_jobs_pin_the_child_profile_and_refuse_broadcast_operations():
    with profiles(), server() as request:
        code, body = request("/api/op/telemetry-index?profile=beta", {})
        assert code == 200 and json.loads(body)["job_id"] == "--profile/beta/telemetry/index"
        code, body = request("/api/op/relay-start?profile=beta", {})
        assert code == 400 and json.loads(body)["error"]["code"] == "invalid_operation"


def t_job_queries_refuse_another_analysis_profile():
    with profiles(), server() as request:
        code,body=request("/api/jobs/owned?profile=beta")
        assert code==200 and json.loads(body)["profile"]=="beta"
        code,body=request("/api/jobs/owned?profile=alpha")
        assert code==404 and json.loads(body)["error"]["code"]=="profile_mismatch"


def t_unknown_profiles_and_foreign_origins_fail_with_explicit_errors():
    with profiles(), server() as request:
        for name in ("missing", "..%2Fbeta", ""):
            code, body = request("/api/telemetry/recordings?profile=" + name)
            assert code == 400 and json.loads(body)["error"]["code"] == "invalid_profile"
        for path in ("/telemetry", "/api/telemetry/recordings?profile=beta"):
            code, body = request(path, headers={"Host": "foreign.example"})
            assert code == 403 and json.loads(body)["ok"] is False


def t_service_stop_requires_explicit_acknowledgement_of_active_ai_work():
    with server(ai_busy=True) as request:
        for value in (None, False, "true", 1):
            code,body=request('/api/quit',{'confirm':value})
            assert code==409 and json.loads(body)['error']['code']=='active_work'
            assert request('/api/ping')[0]==200, 'Refused shutdown stopped the shared service'
        code,body=request('/api/quit',{'confirm':True})
        assert code==200 and json.loads(body)['ok'] is True


def t_service_stop_survives_a_failing_ai_busy_check():
    # A frozen build updated in place re-reads its archive from the swapped exe, so a
    # lazy import in the busy check raises; quit must still work (confirm first).
    def broken():
        raise ImportError("archive changed since launch")
    with server(shutdown_state=broken) as request:
        code,body=request('/api/quit',{})
        assert code==409 and json.loads(body)['error']['code']=='active_work'
        assert request('/api/ping')[0]==200
        code,body=request('/api/quit',{'confirm':True})
        assert code==200 and json.loads(body)['ok'] is True


def t_ui_imports_the_ai_modules_before_serving():
    source = inspect.getsource(m.run_ui)
    assert source.index("import ai_control") < source.index("serve_forever"),         "Lazy AI imports break quit after an in-place update of the frozen build"


if __name__ == "__main__":
    for name, test in sorted(list(globals().items())):
        if name.startswith("t_") and callable(test):
            test()
            print("PASS", name)
