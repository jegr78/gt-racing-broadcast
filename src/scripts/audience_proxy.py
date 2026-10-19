"""Bounded local provider proxy with explicit relay protocol/profile checks."""

from dataclasses import fields
import json
import time
from urllib.parse import urlencode

import audience_admin
from audience_widgets import Sample
import http_util


class Client:
    def __init__(self, profile, port, *, monotonic=time.monotonic):
        self.profile, self.port, self.monotonic = profile, port, monotonic
        self.url = f"http://127.0.0.1:{port}/audience/admin/youtube"

    def _request(self, body=None, query=""):
        data = (
            None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        )
        if data and len(data) > 8192:
            raise ValueError("Provider command is too large")
        try:
            try:
                response = http_util.open_url(self.url + query, data=data,
                    headers={"Content-Type": "application/json"}, timeout=3, allow_redirects=False)
            except http_util.HTTPError as error:
                response = error
            with response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Local provider response is too large")
                result = json.loads(raw)
        except Exception:
            raise ValueError("Local provider connection unavailable; start the relay for this profile") from None
        if not isinstance(result, dict):
            raise ValueError("Invalid local provider response")
        if result.get("ok") is not True:
            if result.get("protocol") == audience_admin.PROTOCOL and result.get("profile") == self.profile:
                message = result.get("error")
                if isinstance(message, str) and len(message) <= 512:
                    raise ValueError(message)
            raise ValueError("Local provider command failed; reopen connection settings")
        return result

    def status(self, metric=None):
        result = self._request(
            query="?" + urlencode({"metric": metric}) if metric else ""
        )
        if (
            result.get("protocol") != audience_admin.PROTOCOL
            or result.get("profile") != self.profile
        ):
            raise ValueError("Local server is not the audience relay for this profile")
        return result

    def command(self, body):
        if not isinstance(body, dict) or body.get("expectedProfile") != self.profile:
            raise ValueError("Active profile changed; reopen connection settings")
        self.status()  # Never send a credential to an unverified port holder.
        return self._request(body)

    def sample(self, metric):
        started = self.monotonic()
        result = self.status(metric)
        raw = result.get("sample")
        allowed = {field.name for field in fields(Sample)}
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise ValueError("Invalid local provider sample")
        sample = Sample(**raw)
        # RPC time is part of sample age, including time awaiting the response.
        from dataclasses import replace
        from audience_widgets import finite

        if not finite(sample.age_seconds) or sample.age_seconds < 0:
            raise ValueError("Invalid local provider sample age")
        return replace(
            sample, age_seconds=sample.age_seconds + max(0, self.monotonic() - started)
        )
