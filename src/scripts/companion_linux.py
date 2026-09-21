"""Linux companion-pi control for the `racecast companion` adapter.

companion-pi runs Bitfocus Companion as a systemd service (user `companion`,
launched by /usr/local/src/companionpi/launch.sh, which passes no bind flag so
Companion binds 0.0.0.0). Companion 3.x headless takes the bind only as the CLI
flag `--admin-address`; config.json is ignored. To enforce the toolkit's
"never 0.0.0.0, Tailscale IP or 127.0.0.1" rule we override the service's
ExecStart via a systemd drop-in that reads the address from an EnvironmentFile,
and set that file (per start) through a narrow root helper. enable_control()
installs the drop-in, the helper, and a visudo-validated NOPASSWD sudoers rule.

Pure logic (content builders, validation, idempotency) is unit-tested; the I/O
in enable_control() takes injected `run`/`read_text`/`write_temp` seams so the
command sequence is testable without root.

This module ships NO shell-script file: bind_helper_content() is a string
written to /usr/local/sbin at enable_control() time on the target machine.
"""
import ipaddress, os, shutil, subprocess, sys, tempfile, getpass

from services import external_tool_env  # de-PyInstaller the env for bare systemctl spawns

UNIT = "companion"
DROPIN_DIR = "/etc/systemd/system/companion.service.d"
DROPIN_CONF = DROPIN_DIR + "/racecast-bind.conf"
BIND_ENV = DROPIN_DIR + "/racecast-bind.env"
HELPER_PATH = "/usr/local/sbin/racecast-companion-bind"
SUDOERS_PATH = "/etc/sudoers.d/racecast-companion"
SERVICE_UNIT_FILE = "/etc/systemd/system/companion.service"
LEGACY_2X_DIR = "/usr/local/src/companion"   # companion-pi 2.x layout (headless_ip.js)


def is_valid_bind_ip(ip):
    """True iff `ip` is a literal IPv4/IPv6 address (defends the helper's argv)."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def bind_env_content(ip):
    """EnvironmentFile body pinning the admin bind address. Raises on a bad ip."""
    if not is_valid_bind_ip(ip):
        raise ValueError(f"invalid bind ip: {ip!r}")
    return f"RACECAST_ADMIN_ADDRESS={ip}\n"


def bind_dropin_content():
    """systemd drop-in: reset the vendor ExecStart, relaunch with --admin-address
    from the EnvironmentFile (default 127.0.0.1). Node-path detection mirrors
    companion-pi's launch.sh."""
    return (
        "[Service]\n"
        f"EnvironmentFile=-{BIND_ENV}\n"
        "ExecStart=\n"
        "ExecStart=/bin/bash -c 'cd /opt/companion && "
        "NODE=$([ -d /opt/companion/node-runtime ] && "
        "echo /opt/companion/node-runtime/bin/node || "
        "echo /opt/companion/node-runtimes/main/bin/node); "
        "exec \"$NODE\" /opt/companion/main.js "
        "--admin-address \"${RACECAST_ADMIN_ADDRESS:-127.0.0.1}\" "
        "--extra-module-path /opt/companion-module-dev'\n"
    )


def bind_helper_content():
    """Root helper (written to HELPER_PATH): validate an IP arg, pin it in the
    EnvironmentFile, restart Companion. The single privileged action behind the
    NOPASSWD rule, and it cannot do anything but set a validated bind + restart."""
    return (
        "#!/bin/bash\n"
        "# Managed by racecast (companion enable-control). Sets the Companion admin\n"
        "# bind address and restarts the service. Do not edit by hand.\n"
        "set -euo pipefail\n"
        'ip="${1:-}"\n'
        'if ! printf "%s" "$ip" | grep -Eq '
        "'^([0-9]{1,3}\\.){3}[0-9]{1,3}$|^[0-9A-Fa-f:]*:[0-9A-Fa-f:]*$'; then\n"
        '  printf "racecast-companion-bind: invalid ip: %s\\n" "$ip" >&2\n'
        "  exit 2\n"
        "fi\n"
        f'printf "RACECAST_ADMIN_ADDRESS=%s\\n" "$ip" > {BIND_ENV}\n'
        f"exec systemctl restart {UNIT}\n"
    )


def sudoers_dropin_content(user, systemctl_path):
    """NOPASSWD for exactly the bind helper (start path) + `systemctl stop`."""
    return (
        "# Managed by racecast (companion enable-control). Passwordless start (via\n"
        "# the bind helper) + stop of the Companion service for the operator.\n"
        f"{user} ALL=(root) NOPASSWD: {HELPER_PATH}, {systemctl_path} stop {UNIT}\n"
    )


def control_commands(unit):
    """Start/quit/running argv for the companion-pi systemd service. `start` is a
    template: the caller appends the validated bind IP (see racecast
    companion_start). `running`/`quit` are complete."""
    return {
        "start": ["sudo", "-n", HELPER_PATH],
        "quit": ["sudo", "-n", "systemctl", "stop", unit],
        "running": ["systemctl", "is-active", unit],
    }


def is_enabled(read_text, user, systemctl_path):
    """True iff the drop-in, helper, and sudoers files already match the expected
    content for `user`. `read_text(path)` returns the file's text or None."""
    return (read_text(DROPIN_CONF) == bind_dropin_content()
            and read_text(HELPER_PATH) == bind_helper_content()
            and read_text(SUDOERS_PATH) == sudoers_dropin_content(user, systemctl_path))


def detect_unit(platform=None, which=None, run=None, exists=None):
    """The companion-pi systemd unit name on this Linux host, or None. None for
    Windows/macOS, for hosts without systemd, and for WSL/Docker/manual installs
    where no local companion.service exists (Companion is on the host)."""
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    run = _default_run if run is None else run
    exists = os.path.exists if exists is None else exists
    if platform.startswith("win") or platform == "darwin":
        return None
    if not which("systemctl"):
        return None
    try:
        if run(["systemctl", "cat", UNIT], capture_output=True,
               text=True).returncode == 0:
            return UNIT
    except Exception:    # noqa: BLE001 — systemctl missing/odd -> fall through to file check
        pass
    return UNIT if exists(SERVICE_UNIT_FILE) else None


def _default_run(argv, **kwargs):
    """subprocess.run with the PyInstaller _MEIPASS scrubbed off LD_LIBRARY_PATH
    (external_tool_env). The frozen binary makes bare `systemctl` calls (cat,
    is-active) that are not wrapped in sudo, so unlike the sudo'd writes, where sudo
    resets the env itself, they would inherit our bundled libcrypto and die with
    "OPENSSL_x.y.z not found". external_tool_env() is None off the frozen binary, so
    dev/source runs inherit os.environ unchanged."""
    kwargs.setdefault("env", external_tool_env())
    return subprocess.run(argv, **kwargs)


def _default_read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _default_write_temp(content):
    fd, path = tempfile.mkstemp(prefix="racecast-companion-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _install_file(run, write_temp, content, dest, mode):
    """Stage `content` to a temp file and place it at `dest` (root) with `mode`."""
    tmp = write_temp(content)
    try:
        return run(["sudo", "install", "-m", mode, "-o", "root", "-g", "root", tmp, dest])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # already gone or never existed (fake path in tests)


def enable_control(platform=None, run=None, which=None, getuser=None,
                   read_text=None, write_temp=None, exists=None, log=print):
    """Idempotently install the systemd bind drop-in, the root bind helper, and a
    visudo-validated NOPASSWD sudoers rule, then validate the service still starts
    (rolling back the drop-in if not). Returns 0 on success, non-zero otherwise.

    All privileged actions run through `run(argv)`; file staging via `write_temp`.
    These seams keep the command sequence unit-testable without root."""
    platform = sys.platform if platform is None else platform
    run = _default_run if run is None else run
    which = shutil.which if which is None else which
    getuser = getpass.getuser if getuser is None else getuser
    read_text = _default_read_text if read_text is None else read_text
    write_temp = _default_write_temp if write_temp is None else write_temp
    exists = os.path.exists if exists is None else exists

    unit = detect_unit(platform=platform, which=which, run=run, exists=exists)
    if not unit:
        log("companion enable-control: no companion.service found on this Linux host "
            "(WSL/host or manual install). Nothing to enable.")
        return 1
    if exists(LEGACY_2X_DIR):
        log("companion enable-control: companion-pi 2.x layout detected "
            f"({LEGACY_2X_DIR}); unsupported. Update Companion to 3.x first.")
        return 1

    user = getuser()
    systemctl = which("systemctl") or "/usr/bin/systemctl"
    if is_enabled(read_text, user, systemctl):
        log("companion enable-control: already enabled.")
        return 0

    # Validate the sudoers content before touching anything live.
    sudoers = sudoers_dropin_content(user, systemctl)
    tmp_sudoers = write_temp(sudoers)
    try:
        visudo_rc = run(["sudo", "visudo", "-cf", tmp_sudoers]).returncode
    finally:
        try:
            os.unlink(tmp_sudoers)
        except OSError:
            pass  # already gone or never existed (fake path in tests)
    if visudo_rc != 0:
        log("companion enable-control: sudoers validation failed; aborting (no changes).")
        return 1

    run(["sudo", "mkdir", "-p", DROPIN_DIR])
    _install_file(run, write_temp, bind_helper_content(), HELPER_PATH, "0755")
    _install_file(run, write_temp, bind_dropin_content(), DROPIN_CONF, "0644")
    _install_file(run, write_temp, sudoers, SUDOERS_PATH, "0440")
    run(["sudo", "systemctl", "daemon-reload"])

    # Validate: apply a safe local bind and confirm the unit comes up.
    run(["sudo", "-n", HELPER_PATH, "127.0.0.1"])
    if run(["systemctl", "is-active", unit], capture_output=True,
           text=True).returncode != 0:
        log("companion enable-control: service did not start with the new drop-in; "
            "rolling back.")
        run(["sudo", "rm", "-f", DROPIN_CONF, HELPER_PATH, SUDOERS_PATH])
        run(["sudo", "systemctl", "daemon-reload"])
        run(["sudo", "systemctl", "restart", unit])
        return 1

    log("companion enable-control: enabled. Start/Stop now works without a password.")
    return 0
