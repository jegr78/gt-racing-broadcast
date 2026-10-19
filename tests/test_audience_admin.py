"""Provider setup accepts only the actual local control-server origin."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
import audience_admin as admin


def t_local_boundary_checks_socket_host_origin_and_fetch_metadata():
    port = 18777
    good = {"Host": f"127.0.0.1:{port}"}
    assert admin.allowed("127.0.0.1", port, good), "Local command-line setup refused"
    for headers in (
        {"Host": "evil.invalid:18777"},
        {"Host": "127.0.0.1:18778"},
        {},
        {**good, "Origin": "http://127.0.0.1:9999"},
        {**good, "Referer": "http://evil.invalid/form"},
        {**good, "Origin": "null"},
        {**good, "Sec-Fetch-Site": "cross-site"},
        {**good, "Origin": "http://127.0.0.1:18777", "Referer": "http://evil.invalid/"},
    ):
        assert not admin.allowed("127.0.0.1", port, headers), headers
    assert not admin.allowed("100.64.0.2", port, good), "Tailnet admin accepted"
    assert admin.allowed(
        "127.0.0.1", port, {**good, "Origin": "http://127.0.0.1:18777"}
    )


if __name__ == "__main__":
    t_local_boundary_checks_socket_host_origin_and_fetch_metadata()
    print("PASS local provider boundary")
