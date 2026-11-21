#!/usr/bin/env python3
"""Simulation mode keeps Driver Radio and the AI agents local: cloud keys blank, no agent
CLI, no model download, and every Python worker behind the network guard.
Run: python3 tests/test_simulation_radio.py"""
import contextlib, importlib.util, os, sys, tempfile, threading, types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import simulation as sim  # noqa: E402
import ai_agents  # noqa: E402
import driver_radio_models  # noqa: E402
import driver_radio_process  # noqa: E402
import driver_radio_voice_process  # noqa: E402

ON = {"RACECAST_SIMULATION": "1"}


@contextlib.contextmanager
def _env(**env):
    saved = dict(os.environ)
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear(); os.environ.update(saved)


def t_cloud_keys_are_blank():
    env = dict(ON, RACECAST_GCLOUD_TTS_KEY="tts", RACECAST_GCLOUD_STT_KEY="stt",
               RACECAST_TS_API_KEY="tskey")
    env.update(sim.env_overrides(env))
    for key in ("RACECAST_GCLOUD_TTS_KEY", "RACECAST_GCLOUD_STT_KEY", "RACECAST_TS_API_KEY"):
        assert env[key] == "", f"{key} would reach a billed cloud API in simulation"


def t_python_workers_run_behind_the_guard():
    argv = [sys.executable, "/x/driver_radio_tts_worker.py", "a"]
    assert sim.guard_python_argv(argv, {}) == argv
    guarded = sim.guard_python_argv(argv, ON)
    assert guarded[1] == "-c" and "install_network_guard" in guarded[2] and guarded[-2:] == \
        ["/x/driver_radio_tts_worker.py", "a"], guarded
    native = ["/usr/bin/whisper-cli", "-m", "model"]
    assert sim.guard_python_argv(native, ON) == native, "a native tool is not a Python script"


def _captured_popen(seen):
    def popen(argv, *a, **k):
        seen.append((list(argv), k.get("env")))
        raise OSError("blocked by the test")
    return popen


def t_radio_process_and_voice_worker_spawn_guarded():
    script = [sys.executable, os.path.join(ROOT, "src", "scripts", "driver_radio_tts_worker.py")]
    for module, start in (
            (driver_radio_process, lambda: driver_radio_process.run_process(
                ai_agents.Invocation(script, tempfile.gettempdir(), "", 5, "speech"), b"{}",
                threading.Event(), 5, os.environ)),
            (driver_radio_voice_process, lambda: driver_radio_voice_process.Owned(
                script, b"{}\n", ai_agents.execution_env(os.environ)))):
        seen = []
        orig = module.subprocess.Popen
        module.subprocess.Popen = _captured_popen(seen)
        try:
            with _env(**ON), contextlib.suppress(Exception):
                start()
        finally:
            module.subprocess.Popen = orig
        assert seen, f"{module.__name__} spawned nothing"
        argv, env = seen[0]
        assert argv[1] == "-c" and "install_network_guard" in argv[2], \
            f"{module.__name__} started a worker outside the guard: {argv}"
        assert sim.active(env or {}), f"{module.__name__} dropped RACECAST_SIMULATION: {env}"


def t_agent_clis_are_off():
    ran = []
    adapter = ai_agents.Adapter({"id": "sim", "name": "Sim", "provider": "codex",
                                 "executable": sys.executable},
                                which=lambda name: sys.executable)
    with _env(**ON):
        out = adapter.probe(run=lambda *a, **k: ran.append(a))
        assert ran == [] and out["status"] == "simulation", out
        try:
            adapter.invocation("p", "o", "s", dict(out, status="ready"))
        except ai_agents.AgentError as exc:
            assert exc.code == "simulation", exc.code
        else:
            raise AssertionError("an agent CLI invocation was built in simulation")


def t_model_downloads_are_off():
    opened = []
    with _env(**ON):
        try:
            driver_radio_models.download("https://huggingface.co/x", "0" * 64, 10,
                                         open_url=lambda *a, **k: opened.append(a))
        except ValueError as exc:
            assert sim.SKIPPED in str(exc), exc
        else:
            raise AssertionError("a model download ran in simulation")
    assert opened == []


def _racecast():
    spec = importlib.util.spec_from_file_location("racecast_radio", os.path.join(ROOT, "src", "racecast.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def t_frozen_worker_entrypoints_install_the_guard():
    m = _racecast()
    guarded = []
    fake = types.ModuleType("driver_radio_models")
    fake.main = lambda argv: 0
    saved = (sim.install_network_guard, sys.modules.get("driver_radio_models"))
    sim.install_network_guard = lambda environ=None: guarded.append(True) or True
    sys.modules["driver_radio_models"] = fake
    try:
        with _env(**ON):
            m.main(["_radio-model-check", "x"])
    finally:
        sim.install_network_guard = saved[0]
        sys.modules["driver_radio_models"] = saved[1]
    assert guarded, "a frozen radio worker ran without the network guard"


def t_tts_key_is_never_read_from_the_machine_env():
    m = _racecast()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, ".env")
        with open(path, "w") as fh:
            fh.write("RACECAST_GCLOUD_TTS_KEY=billed\n")
        orig = m._env_file
        m._env_file = lambda: path
        try:
            with _env(**ON, RACECAST_GCLOUD_TTS_KEY=""):
                assert m._radio_tts_key() == "", "the billed key was read from .env"
            os.environ.pop("RACECAST_SIMULATION", None)
            assert m._radio_tts_key() == "billed", "fixture sanity"
        finally:
            m._env_file = orig


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
