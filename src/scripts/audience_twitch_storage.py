"""Stage a private one-use refresh before making the provider exchange."""

import json
from audience_connections import ConnectionFiles, StorageError


class CredentialFiles(ConnectionFiles):
    def stage_refresh(self, expected_id):
        """Mark the saved pair as refreshing, atomically, before the one-use POST.

        The pair stays in the private file next to the phase, so a process that
        restarts mid-refresh resumes the rotation with the same refresh token:
        Twitch either accepts it (it was never consumed) or rejects it with HTTP
        400, which ends in the same reconnect a lost pair would have needed. The
        token never leaves this machine either way."""
        loaded = self.load()
        if (
            not loaded
            or loaded[0].get("connectionId") != expected_id
            or loaded[1].get("phase") != "ready"
        ):
            raise StorageError(
                "Saved Twitch connection changed; reconnect before refreshing"
            )
        private = {
            "version": 1,
            "profile": self.owner,
            "connectionId": expected_id,
            "credentials": {**loaded[1], "phase": "refreshing"},
        }
        try:
            body = json.dumps(private, allow_nan=False, sort_keys=True).encode("utf-8")
            self.writer(self.secret, body, private=True)
        except (OSError, ValueError, TypeError):
            raise StorageError(
                "Could not mark Twitch refresh; no token was exchanged"
            ) from None
