"""The public live timing link of an event (#1128): a random id in the URL is the
only credential, separate from the drivers' invite. `event start` creates it, `event
stop` ends it, the producer renews it. Stored like the invite (driver_invite)."""
import os

import driver_invite

FILENAME = "timing-link.json"
current = driver_invite.current
create = driver_invite.create
clear = driver_invite.clear
Cache = driver_invite.Cache


def path_in(runtime_dir):
    return os.path.join(runtime_dir, FILENAME)
