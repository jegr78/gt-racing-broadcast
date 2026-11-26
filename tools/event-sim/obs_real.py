"""A real OBS for a record run (`event_sim.py --record DIR --obs real`, #1310).

OBS runs as a second instance with the Sim profile and the localized demo collection. Its
WebSocket listens on a free port on every interface, so it requires a random per-run
password, given on OBS's command line and written to no file; its stream target is the run's loopback RTMP sink. The run starts and stops its
recording over the WebSocket; the recording lands in DIR/program/.

- Linux: its own configuration directory inside the run's work dir (XDG_CONFIG_HOME), so
  the user's OBS, even a running one, is never read or written.
- macOS: OBS ignores HOME and XDG there. The run refuses while any OBS runs, copies the
  user's configuration folder to a temporary copy, verifies it and renames it to the
  backup, then marks it complete. It writes the Sim files into the folder, and after OBS
  quit restores it from the backup and compares every file by SHA-256. The backup is
  removed only when they match; a left backup refuses the next run, and
  `event_sim.py --restore-obs` restores it.

OBS quits on SIGINT from 32.1 on (Linux saves and closes its main window, macOS quits the
app); the run signals only the PID it started, never its process group, after the
recording stopped. obs-websocket has no quit request."""
import base64
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (HERE, os.path.join(HERE, "fakes"), os.path.join(ROOT, "src", "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import obs_state  # noqa: E402
import redaction  # noqa: E402
import obs_ws  # noqa: E402
import simulation  # noqa: E402

PROFILE = "Sim"
HOST = "127.0.0.1"
CANVAS = (1920, 1080)
FPS = 30
START_TIMEOUT_S = 60
NOT_READY = 207            # obs-websocket's request status while OBS still loads
RECORD_TIMEOUT_S = 20
QUIT_TIMEOUT_S = 30
CONNECT_S = 5
POLL_S = 0.5
SNAPSHOT_S = 0.5           # a check polls every 0.25 s; the mirror asks OBS at most this often
FEED_SHOT_WIDTH = 640      # the stub's feed width: its identity strip reads at full size
SIGNAL_QUIT_FROM = (32, 1)
LINUX_BINARY = "obs"
MAC_BINARY = "/Applications/OBS.app/Contents/MacOS/OBS"
MAC_SUPPORT = "/Library/Application Support"
GRAPHICS_TOKEN, MEDIA_TOKEN = "__RACECAST_GRAPHICS__", "__RACECAST_MEDIA__"
# Capture inputs that would open a device or ask for a permission; they become empty media inputs.
DEVICE_KINDS = frozenset({"coreaudio_input_capture", "coreaudio_output_capture", "sck_audio_capture",
                          "screen_capture", "av_capture_input", "av_capture_input_v2", "macos_avcapture",
                          "pulse_input_capture", "pulse_output_capture", "alsa_input_capture",
                          "jack_output_capture", "v4l2_input", "pipewire-screen-capture-source",
                          "pipewire-desktop-capture-source", "xshm_input", "xcomposite_input",
                          "wasapi_input_capture", "wasapi_output_capture", "wasapi_process_output_capture",
                          "dshow_input", "window_capture", "game_capture", "monitor_capture"})
PROGRAM_DIR = "program"
HUD_SOURCE = "HUD Overlay"
STINT_SCENE = "Stint"
STATE_FILE, LOG_FILE = "obs-state.json", "obs.log"
RECOVERY = "quit OBS, then run `python3 tools/event-sim/event_sim.py --restore-obs`"
KILLPG = getattr(os, "killpg", None)     # absent on Windows, where --obs real is refused


def _setup_assets():
    spec = importlib.util.spec_from_file_location("setup_assets", os.path.join(ROOT, "src", "setup-assets.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mac_config_dir(home):
    return home + MAC_SUPPORT + "/obs-studio"


def mac_backup_dir(home):
    return home + MAC_SUPPORT + "/obs-studio.racecast-sim-backup"


def mac_backup_mark(home):
    """Written once the backup was verified; a backup without it is a copy that broke off."""
    return mac_backup_dir(home) + ".complete"


def neutralize_devices(collection):
    """`collection` with every DEVICE_KINDS input turned into an empty media input of
    the same name, so the relay's requests to it still find it and nothing is captured."""
    for src in collection.get("sources") or []:
        if isinstance(src, dict) and src.get("id") in DEVICE_KINDS:
            src["id"] = src["versioned_id"] = "ffmpeg_source"
            src["settings"] = {"is_local_file": True, "local_file": ""}
    return collection


def localize(collection, home, name, profile_dir, setup=None):
    """The demo collection for this run's app home: its graphics and media tokens point
    into home/runtime/demo, its name is the profile's OBS_COLLECTION, the profile's
    overlay boxes are baked in as `racecast setup` does, and no device is captured."""
    setup = setup or _setup_assets()
    runtime = os.path.join(home, "runtime", "demo")
    graphics, media = os.path.join(runtime, "graphics"), os.path.join(runtime, "media")
    raw = json.dumps(collection)
    setup.placeholders.fill_missing(setup.placeholders.expected_graphics_from_template(raw), graphics,
                                    setup.placeholders.graphic_placeholder_path())
    out = setup.replace_tokens(copy.deepcopy(collection), {GRAPHICS_TOKEN: graphics, MEDIA_TOKEN: media})
    neutralize_devices(out)
    setup.apply_collection_name(out, name)
    css = ""
    with contextlib.suppress(OSError), open(os.path.join(profile_dir, "overlay", "hud.css"), encoding="utf-8") as fh:
        css = fh.read()
    setup.bake_overlay_boxes(out, setup.read_hud_html(os.path.join(home, "src")), css)
    setup.bake_overlay_visibility(out, css)
    return out


def config_files(ws_port, record_dir, collection, platform, rtmp=""):
    """{relative path: text} of the Sim profile (with `rtmp`, its stream target), the
    collection and the WebSocket config; on Linux also global.ini and user.ini of the
    isolated configuration. No file holds the WebSocket password: OBS gets it on its
    command line (obs_argv)."""
    w, h = CANVAS
    files = {
        f"basic/profiles/{PROFILE}/basic.ini":
            f"[General]\nName={PROFILE}\n[Video]\nBaseCX={w}\nBaseCY={h}\nOutputCX={w}\nOutputCY={h}\n"
            f"FPSType=0\nFPSCommon={FPS}\n[Output]\nMode=Simple\n[SimpleOutput]\n"
            f"FilePath={os.path.join(record_dir, PROGRAM_DIR)}\nRecFormat2=mkv\nRecQuality=Small\n",
        f"basic/scenes/{PROFILE}.json": json.dumps(collection, ensure_ascii=False),
        "plugin_config/obs-websocket/config.json": json.dumps(
            {"alerts_enabled": False, "auth_required": True, "first_load": False, "server_enabled": True,
             "server_port": ws_port}),
    }
    if rtmp:
        files[f"basic/profiles/{PROFILE}/service.json"] = json.dumps(
            {"type": "rtmp_custom", "settings": {"server": rtmp, "key": "sim", "use_auth": False}})
    if platform != "darwin":
        files["global.ini"] = "[General]\nEnableAutoUpdates=false\n"
        files["user.ini"] = (f"[General]\nFirstRun=true\nConfirmOnExit=false\n[Basic]\nProfile={PROFILE}\n"
                             f"ProfileDir={PROFILE}\nSceneCollection={collection.get('name', PROFILE)}\n"
                             f"SceneCollectionFile={PROFILE}.json\n[BasicWindow]\nSysTrayEnabled=true\n"
                             "SysTrayWhenStarted=true\n")
    return files


def obs_argv(binary, name, platform, port, password):
    """OBS's command line; obs-websocket 5 takes the port and the password from it, turns
    authentication on and writes neither into its config (`--websocket_password`)."""
    argv = [binary, "--multi", "--minimize-to-tray", "--disable-missing-files-check"]
    if platform == "darwin":
        argv.append("--disable-shutdown-check")
    return argv + ["--profile", PROFILE, "--collection", name,
                   "--websocket_port", str(port), "--websocket_password", password]


def version_tuple(text):
    parts = []
    for p in str(text or "").split(".")[:2]:
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (2 - len(parts)))


def digest(root):
    """{relative path: SHA-256} of every file under `root`."""
    out = {}
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


class LinuxConfig:
    """An isolated configuration under the work dir; nothing of the user's is touched."""

    def __init__(self, work):
        self.base = os.path.join(work, "obs-config")
        self.root = os.path.join(self.base, "obs-studio")

    def prepare(self):
        os.makedirs(self.root, exist_ok=True)

    def env(self, base):
        return dict(base, XDG_CONFIG_HOME=self.base)

    def restore(self):
        return []


class MacConfig:
    """The user's configuration folder, backed up before and restored after the run. The
    backup counts only with its completion mark, written after the copy was verified."""

    def __init__(self, home, run=subprocess.run):
        self.root, self.backup, self.mark = mac_config_dir(home), mac_backup_dir(home), mac_backup_mark(home)
        self.run = run
        self.prepared = False
        self.existed = True

    def _refuse_running(self, what):
        if self.run(["pgrep", "-x", "OBS"], capture_output=True, timeout=10).returncode == 0:
            raise RuntimeError(f"OBS is running: quit it before {what}")

    def _refuse_unmarked(self):
        if os.path.exists(self.backup) and not os.path.exists(self.mark):
            raise RuntimeError(f"the backup at {self.backup} is not complete: the run that made it stopped before "
                               f"changing {self.root}, so that folder is the user's own. Remove the backup")

    def prepare(self):
        self._refuse_running("a run with --obs real")
        self._refuse_unmarked()
        if os.path.exists(self.backup):
            raise RuntimeError(f"{self.backup} exists: an earlier run did not restore the OBS configuration. "
                               f"Restore it with `python3 tools/event-sim/event_sim.py --restore-obs`")
        self.existed = os.path.isdir(self.root)
        os.makedirs(self.root, exist_ok=True)
        tmp = self.backup + ".partial"
        shutil.rmtree(tmp, ignore_errors=True)       # a copy a killed run left; the folder itself is intact
        try:
            self.run(["ditto", self.root, tmp], check=True, timeout=300)
            if digest(tmp) != digest(self.root):
                raise RuntimeError("the backup of the OBS configuration differs from it; nothing was started")
            os.rename(tmp, self.backup)
            with open(self.mark, "w", encoding="utf-8") as fh:
                fh.write("verified\n")
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            if not os.path.exists(self.mark):
                shutil.rmtree(self.backup, ignore_errors=True)
            raise
        self.prepared = True

    def env(self, base):
        return dict(base)

    def _drop_backup(self):
        shutil.rmtree(self.backup, ignore_errors=True)
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.mark)

    def restore(self):
        """Problem texts; [] once the folder matches the backup and the backup is gone."""
        if not self.prepared:
            return []
        self.prepared = False
        if not self.existed:
            shutil.rmtree(self.root, ignore_errors=True)
            self._drop_backup()
            return []
        return self._from_backup()

    def recover(self):
        """Restore the folder from a backup a killed run left: problem texts, [] when it
        matches and the backup is gone. RuntimeError while OBS runs, without a backup or
        with one that is not marked complete."""
        self._refuse_running("restoring its configuration")
        if not os.path.isdir(self.backup):
            raise RuntimeError(f"no backup at {self.backup}: nothing to restore")
        self._refuse_unmarked()
        return self._from_backup()

    def _from_backup(self):
        """Copy the backup next to the folder, verify the copy, then swap it in; a folder
        that is already gone is just replaced. The backup stays unless the result matches."""
        tmp = self.root + ".racecast-sim-restore"
        keep = f"the backup stays at {self.backup}; {RECOVERY}"
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            self.run(["ditto", self.backup, tmp], check=True, timeout=300)
            want, copied = digest(self.backup), digest(tmp)
            if copied != want:
                shutil.rmtree(tmp, ignore_errors=True)
                return [f"the copy of the backup differs in {_differs(want, copied)}; {keep}"]
            if os.path.exists(self.root):
                shutil.rmtree(self.root)
            os.rename(tmp, self.root)
            got = digest(self.root)
        except (OSError, subprocess.SubprocessError) as exc:
            return [f"the OBS configuration was not restored ({type(exc).__name__}: {exc}); {keep}"]
        if got != want:
            return [f"the restored OBS configuration differs in {_differs(want, got)}; {keep}"]
        self._drop_backup()
        return []


def _differs(a, b):
    return ", ".join(sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))[:5])


def restore_obs(home=None, run=subprocess.run, platform=None):
    """`event_sim.py --restore-obs`: (exit code, text). 0 restored, 1 restored but it
    differs (the backup stays), 2 nothing done."""
    platform = platform or sys.platform
    if platform != "darwin":
        return 2, "only a macOS run changes the user's OBS configuration: nothing to restore"
    cfg = MacConfig(home or os.path.expanduser("~"), run=run)
    try:
        problems = cfg.recover()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return 2, str(exc)
    if problems:
        return 1, "; ".join(problems)
    return 0, f"{cfg.root} restored from the backup, every file verified; the backup is removed"


def bmp_rgb(data):
    """(rgb24 bytes top down, width, height) of an uncompressed 24 or 32 bit BMP, or a 32
    bit one whose bit fields give each channel a whole byte, as OBS writes a screenshot in
    that format; ValueError for anything else."""
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("not a BMP")
    offset = struct.unpack_from("<I", data, 10)[0]
    w, h, _planes, bpp, compression = struct.unpack_from("<iiHHI", data, 18)
    step = bpp // 8
    if bpp not in (24, 32) or compression not in (0, 3) or (compression == 3 and bpp != 32) \
            or w <= 0 or h == 0:
        raise ValueError(f"an unsupported BMP: {bpp} bpp, compression {compression}")
    channels = (2, 1, 0)             # byte of red, green, blue in a pixel: BGR(A)
    if compression == 3:
        if len(data) < 66:
            raise ValueError("a cut BMP")
        masks = struct.unpack_from("<III", data, 54)   # after the 40 byte header, or inside a longer one
        lows = [(m & -m).bit_length() - 1 for m in masks]
        if any(m == 0 or m >> low != 0xFF or low % 8 for m, low in zip(masks, lows, strict=True)) \
                or len(set(lows)) != 3:
            raise ValueError(f"unsupported BMP bit fields {[hex(m) for m in masks]}")
        channels = tuple(low // 8 for low in lows)
    stride = (w * step + 3) // 4 * 4
    if len(data) < offset + stride * abs(h):
        raise ValueError("a cut BMP")
    out = bytearray(w * abs(h) * 3)
    for y in range(abs(h)):
        src = offset + (y if h < 0 else abs(h) - 1 - y) * stride
        row = data[src:src + w * step]
        dst = y * w * 3
        for c, byte in enumerate(channels):
            out[dst + c:dst + w * 3:3] = row[byte::step]
    return bytes(out), w, abs(h)


class ObsMirror:
    """What the probes and the `obs` role read of the real OBS, with the simulated OBS's
    surface: an ObsState of the same collection mirrors the program and preview scene,
    each item's visibility and the outputs, so `program_feeds` and its occlusion work as
    with the stand-in; requests and screenshots go to OBS."""

    def __init__(self, collection, session, grabber=None, clock=time.monotonic, on_program=None):
        self.on_program = on_program     # called with the program scene after each sync
        self.model = obs_state.ObsState(collection, grabber=grabber)
        self.grabber = grabber
        self.session, self.clock = session, clock
        self._synced = None
        self._frames = {}                # source -> (asked at, frame or None)

    def _ask(self, kind, data=None):
        try:
            return self.session.request(kind, dict(data or {})) or {}
        except ValueError as exc:
            raise obs_state.ObsError(None, str(exc)) from None

    def sync(self):
        now = self.clock()
        if self._synced is not None and now - self._synced < SNAPSHOT_S:
            return
        m = self.model
        program = self._ask("GetCurrentProgramScene").get("currentProgramSceneName")
        try:
            preview = self._ask("GetCurrentPreviewScene").get("currentPreviewSceneName")
        except obs_state.ObsError:
            preview = None           # studio mode is off
        items = {}
        for scene in list(m.scenes):
            try:
                items[scene] = {i.get("sceneItemId"): bool(i.get("sceneItemEnabled"))
                                for i in self._ask("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])}
            except obs_state.ObsError:
                continue             # a scene OBS does not know
        stream = bool(self._ask("GetStreamStatus").get("outputActive"))
        rec = bool(self._ask("GetRecordStatus").get("outputActive"))
        with m.lock:
            if program in m.scenes:
                m.program = program
            m.preview = preview
            for scene, enabled in items.items():
                for item in m.scenes[scene]:
                    item["enabled"] = enabled.get(item["id"], item["enabled"])
            m.stream["active"], m.record["active"] = stream, rec
        self._synced = now
        if self.on_program is not None:
            self.on_program(program)

    def snapshot(self):
        self.sync()
        return self.model.snapshot()

    def request(self, kind, data, origin="http"):
        self._synced = None
        return self._ask(kind, data)

    def screenshot(self, source, width=640, fmt="png"):
        got = self._ask("GetSourceScreenshot", {"sourceName": source, "imageFormat": fmt, "imageWidth": width})
        data = got.get("imageData") or ""
        return base64.b64decode(data.split(",", 1)[1] if "," in data else data), fmt

    def source_frame(self, source):
        """(rgb, width, height) of the picture OBS renders for `source` alone, None when OBS
        gives none. The program check reads a feed here: on program the Overlay covers the
        identity strip, and the relay's own port shows what it serves, not what OBS plays."""
        now = self.clock()
        asked = self._frames.get(source)
        if asked is not None and now - asked[0] < SNAPSHOT_S:
            return asked[1]
        try:
            frame = bmp_rgb(self.screenshot(source, FEED_SHOT_WIDTH, "bmp")[0])
        except (obs_state.ObsError, ValueError, OSError):
            frame = None
        self._frames[source] = (now, frame)
        return frame


class RealObs:
    """A started OBS instance: `env()` points racecast at it, `state` is its ObsMirror,
    `start_record()` and `stop_record()` drive its recording, `stop()` quits it and
    restores what it changed (problem texts)."""

    def __init__(self, work, record_dir, collection, grabber=None, platform=None, home=None,
                 launcher=subprocess.Popen, connect=obs_ws._open_session, kill=os.kill,
                 killpg=KILLPG, run=subprocess.run, binary=None,
                 clock=time.monotonic, sleep=time.sleep, port=None, rtmp="", evidence=None, relay_ready=None):
        self.platform = platform or sys.platform
        if self.platform not in ("linux", "darwin"):
            raise RuntimeError(f"--obs real runs on Linux or macOS, not {self.platform}")
        try:
            self.rtmp = simulation.rtmp_target({simulation.RTMP: rtmp})
        except ValueError as exc:
            raise RuntimeError(f"the real OBS streams only to a loopback sink: {exc}") from None
        self.work, self.record_dir, self.collection = work, record_dir, collection
        self.name = collection.get("name") or PROFILE
        self.grabber = grabber
        self.launcher, self.connect, self.kill, self.killpg, self.run = launcher, connect, kill, killpg, run
        self.clock, self.sleep = clock, sleep
        self.binary = binary or (MAC_BINARY if self.platform == "darwin" else shutil.which(LINUX_BINARY))
        home = home or os.path.expanduser("~")
        self.config = MacConfig(home, run=run) if self.platform == "darwin" else LinuxConfig(work)
        self.port = port or _free_port()
        self.password = secrets.token_urlsafe(24)
        self.proc = self.session = self.state = self.log = None
        self.version, self.notes, self.recording = "", [], False
        self.evidence = evidence         # the run's logs dir: OBS's state, the HUD picture, OBS's log
        self.states, self.stint_seen, self.logs_before = [], False, set()
        self.relay_ready = relay_ready   # True once the relay answers: its pages must load live in OBS

    def env(self):
        return {simulation.FLAG: "1", simulation.OBS_WS: f"ws://{HOST}:{self.port}",
                simulation.OBS_WS_PASSWORD: self.password,
                **({simulation.RTMP: self.rtmp} if self.rtmp else {})}

    def start(self):
        if not self.binary or not os.path.exists(self.binary):
            raise RuntimeError(f"OBS not found ({self.binary or LINUX_BINARY}): --obs real needs OBS installed")
        self._wait_for_relay()
        self.config.prepare()
        try:
            self._launch()
        except BaseException:
            self.stop()
            raise
        return self

    def _launch(self):
        os.makedirs(os.path.join(self.record_dir, PROGRAM_DIR), exist_ok=True)
        for rel, text in config_files(self.port, self.record_dir, self.collection, self.platform,
                                      rtmp=self.rtmp).items():
            path = os.path.join(self.config.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        os.makedirs(self.work, exist_ok=True)
        self.logs_before = set(self._obs_logs())
        self.log = open(os.path.join(self.work, "obs-real.log"), "wb")  # noqa: SIM115  closed in stop()
        self.proc = self.launcher(obs_argv(self.binary, self.name, self.platform, self.port, self.password),
                                  env=self.config.env(os.environ), stdin=subprocess.DEVNULL,
                                  stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline, last = self.clock() + START_TIMEOUT_S, ""
        while self.session is None:
            if self.proc.poll() is not None:
                raise RuntimeError(f"OBS exited during its start with {self.proc.returncode}")
            if self.clock() >= deadline:
                raise RuntimeError(f"OBS's WebSocket did not answer within {START_TIMEOUT_S}s: {last}")
            try:
                self.session = self.connect(HOST, self.port, self.password, CONNECT_S)
            except Exception as exc:  # noqa: BLE001  not listening yet
                last = f"{type(exc).__name__}: {exc}"
                self.sleep(POLL_S)
        req = self.session.request
        self.version = self._when_ready("GetVersion", deadline).get("obsVersion") or ""
        profile = req("GetProfileList", {}).get("currentProfileName")
        current = req("GetSceneCollectionList", {}).get("currentSceneCollectionName")
        if profile != PROFILE or current != self.name:
            raise RuntimeError(f"OBS runs profile {profile!r} and collection {current!r}, "
                               f"not {PROFILE!r} and {self.name!r}")
        self._check_own_collection(req)
        if version_tuple(self.version) < SIGNAL_QUIT_FROM:
            self.notes.append(f"OBS {self.version} quits on a signal only from "
                              f"{'.'.join(map(str, SIGNAL_QUIT_FROM))} on: its quit may not be graceful")
        self.state = ObsMirror(self.collection, self.session, grabber=self.grabber, clock=self.clock,
                               on_program=self._on_program)
        self.capture("start", "hud-source-start.png")
        try:
            refreshed = self.reload_pages()
        except Exception as exc:  # noqa: BLE001  a page that stays on its error page shows in the evidence
            refreshed = []
            self.notes.append(self._scrub(f"OBS page reload: {type(exc).__name__}: {exc}"))
        if self.states:
            self.states[0]["refreshed"] = refreshed
            self._write_states()

    def _wait_for_relay(self):
        """OBS loads its relay pages once at its start; a page that finds the relay down
        keeps Chromium's error page. So OBS starts only after the relay answers."""
        if self.relay_ready is None:
            return
        deadline = self.clock() + START_TIMEOUT_S
        while not self.relay_ready():
            if self.clock() >= deadline:
                raise RuntimeError(f"the relay did not answer within {START_TIMEOUT_S}s: OBS not started")
            self.sleep(POLL_S)

    def reload_pages(self):
        """Reload every relay page once (refreshnocache), in case one loaded an error page.
        Returns the reloaded source names."""
        names = [src["name"] for src in self.collection.get("sources") or []
                 if src.get("id") == "browser_source" and (src.get("settings") or {}).get("url")]
        for name in names:
            self.session.request("PressInputPropertiesButton", {"inputName": name, "propertyName": "refreshnocache"})
        return names

    def _obs_logs(self):
        logs = os.path.join(self.config.root, "logs")
        return sorted(os.path.join(logs, f) for f in os.listdir(logs)) if os.path.isdir(logs) else []

    def _scrub(self, value):
        return redaction.scrub(value, [self.password])

    def redact(self, text):
        """`text` without the WebSocket password, which OBS logs with its command line."""
        return redaction.redact(text, [self.password])

    def _on_program(self, program):
        if program == STINT_SCENE and not self.stint_seen:
            self.stint_seen = True
            self.capture("stint", "hud-source.png")

    def capture(self, label, picture):
        """Append what OBS reports now to the run's obs-state.json: the program scene, its
        items with their visibility, each browser source's url, width and height; and save
        the HUD source alone as `picture`. Evidence only: a failure is a note."""
        if not self.evidence or self.session is None:
            return
        req = self.session.request
        try:
            program = req("GetCurrentProgramScene", {}).get("currentProgramSceneName")
            items = [{"name": i.get("sourceName"), "id": i.get("sceneItemId"), "enabled": i.get("sceneItemEnabled")}
                     for i in req("GetSceneItemList", {"sceneName": program}).get("sceneItems", [])]
            pages = {}
            for inp in req("GetInputList", {"inputKind": "browser_source"}).get("inputs", []):
                got = req("GetInputSettings", {"inputName": inp["inputName"]}).get("inputSettings", {})
                pages[inp["inputName"]] = {k: got.get(k) for k in ("url", "width", "height")}
            self.states.append({"when": label, "at": round(self.clock(), 3), "program_scene": program,
                                "items": items, "browser_sources": pages})
            self._write_states()
            shot = req("GetSourceScreenshot", {"sourceName": HUD_SOURCE, "imageFormat": "png",
                                               "imageWidth": CANVAS[0]}).get("imageData") or ""
            with open(os.path.join(self.evidence, picture), "wb") as fh:
                fh.write(base64.b64decode(shot.split(",", 1)[1] if "," in shot else shot))
        except Exception as exc:  # noqa: BLE001  evidence never decides the run
            self.notes.append(self._scrub(f"OBS state {label}: {type(exc).__name__}: {exc}"))

    def _write_states(self):
        os.makedirs(self.evidence, exist_ok=True)
        with open(os.path.join(self.evidence, STATE_FILE), "w", encoding="utf-8") as fh:
            json.dump(self._scrub(self.states), fh, indent=1)

    def keep_log(self):
        """Copy the log files this OBS wrote into the run's logs dir as obs.log, before the
        restore removes them (macOS); the user's older logs stay out."""
        if not self.evidence or self.proc is None:
            return []
        try:
            fresh = [p for p in self._obs_logs() if p not in self.logs_before and os.path.isfile(p)]
            if not fresh:
                return ["OBS wrote no log file"]
            os.makedirs(self.evidence, exist_ok=True)
            with open(os.path.join(self.evidence, LOG_FILE), "w", encoding="utf-8") as out:
                for path in fresh:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        out.write(f"--- {os.path.basename(path)} ---\n")
                        out.write(self.redact(fh.read()))
        except OSError as exc:
            return [f"OBS log not kept: {exc}"]
        return []

    def _check_own_collection(self, req):
        """Refuse an OBS whose collection of the run's name is not the run's: its relay
        pages would point elsewhere (the user's import points them at 8088)."""
        pages = [(src["name"], src["settings"].get("url")) for src in self.collection.get("sources") or []
                 if src.get("id") == "browser_source" and (src.get("settings") or {}).get("url")]
        for name, url in pages[:1]:
            got = req("GetInputSettings", {"inputName": name}).get("inputSettings", {}).get("url")
            if got != url:
                raise RuntimeError(f"OBS loaded another collection named {self.name!r}: its {name!r} "
                                   f"opens {got!r}, the run's opens {url!r}")

    def _when_ready(self, kind, deadline):
        """`kind`'s answer once OBS accepts requests: its WebSocket listens before OBS has
        loaded, and answers NOT_READY meanwhile, which is asked again until `deadline`."""
        while True:
            try:
                return self.session.request(kind, {})
            except ValueError as exc:
                if f"'code': {NOT_READY}," not in str(exc):
                    raise
                if self.clock() >= deadline:
                    raise RuntimeError(f"OBS was not ready within {START_TIMEOUT_S}s: {exc}") from None
            self.sleep(POLL_S)

    def _record_active(self, want):
        deadline = self.clock() + RECORD_TIMEOUT_S
        while bool(self.session.request("GetRecordStatus", {}).get("outputActive")) != want:
            if self.clock() >= deadline:
                raise RuntimeError(f"OBS's recording did not {'start' if want else 'stop'} "
                                   f"within {RECORD_TIMEOUT_S}s")
            self.sleep(POLL_S)

    def start_record(self):
        """Start the program recording; return once OBS reports it active, with the clock
        time of OBS's answer to StartRecord, the recording's start."""
        self.session.request("StartRecord", {})
        at = self.clock()
        self.recording = True
        self._record_active(True)
        return at

    def stop_record(self):
        """Stop the recording; its file once OBS reports it inactive."""
        path = self.session.request("StopRecord", {}).get("outputPath")
        self._record_active(False)
        self.recording = False
        return path

    def quit(self):
        """End the OBS this run started: SIGINT, then SIGTERM, to its PID only, each given
        QUIT_TIMEOUT_S; its own process group is killed only when both were ignored."""
        if self.proc is None or self.proc.poll() is not None:
            return []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.kill(self.proc.pid, sig)
                self.proc.wait(timeout=QUIT_TIMEOUT_S)
                return []
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                return []            # it ended meanwhile
        if self.killpg is not None:
            with contextlib.suppress(OSError):
                self.killpg(self.proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=10)
        return [f"OBS did not quit on SIGINT or SIGTERM within {QUIT_TIMEOUT_S}s each; its process group was killed"]

    def stop(self):
        """Stop the recording, quit OBS, restore its configuration. Problem texts."""
        problems = list(self.notes)
        if self.session is not None:
            if self.recording:
                try:
                    self.stop_record()
                except Exception as exc:  # noqa: BLE001  OBS still has to quit
                    problems.append(f"OBS recording stop: {exc}")
            self.session.close()
            self.session = None
        problems += self.quit()
        if self.log is not None:
            self.log.close()
            self.log = None
        problems += self.keep_log()
        try:
            problems += self.config.restore()
        except Exception as exc:  # noqa: BLE001  the steps below are the way back
            problems.append(f"the OBS configuration was not restored ({type(exc).__name__}: {exc}): {RECOVERY}")
        return problems
