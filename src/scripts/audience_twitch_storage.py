"""Stage a private one-use refresh before making the provider exchange."""

import json
from audience_connections import ConnectionFiles, StorageError


class CredentialFiles(ConnectionFiles):
    def stage_refresh(self, expected_id):
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
            "credentials": {"phase": "refreshing"},
        }
        try:
            body = json.dumps(private, allow_nan=False, sort_keys=True).encode("utf-8")
            self.writer(self.secret, body, private=True)
        except (OSError, ValueError, TypeError):
            raise StorageError(
                "Could not mark Twitch refresh; no token was exchanged"
            ) from None
