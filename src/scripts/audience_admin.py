"""Local provider setup boundary shared by the UI and relay."""

import ipaddress
from urllib.parse import urlparse

PROTOCOL = "racecast-audience-admin-v1"


def allowed(peer, port, headers):
    try:
        if not ipaddress.ip_address(peer).is_loopback:
            return False
        host = headers.get("Host", "")
        parsed = urlparse("http://" + host)
        if (
            parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or (parsed.port or 80) != port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        for field in ("Origin", "Referer"):
            value = headers.get(field)
            if value:
                origin = urlparse(value)
                if origin.scheme != "http" or origin.netloc.lower() != host.lower():
                    return False
        if headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            return False
    except (ValueError, TypeError):
        return False
    return True
