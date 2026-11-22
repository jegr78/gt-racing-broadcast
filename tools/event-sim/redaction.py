"""One redaction for everything a run writes: step errors, results, findings, check
details and the lines printed from them. Console tokens (`t=`), driver invites (`i=`),
timing links (`l=`) and driver codes go by shape, values the run handed out (minted
tokens, the event's link ids, the console secret) by value."""
import re

MARK = "REDACTED"
_PARAM = re.compile(r"([?&][til]=)[^&#\s)\"']+")
_CODE = re.compile(r"\bD1:[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
MIN_SECRET = 6            # a shorter value would blank ordinary words


def redact(text, secrets=()):
    """`text` as a string without credentials."""
    out = _CODE.sub("D1:" + MARK, _PARAM.sub(r"\1" + MARK, str(text)))
    for secret in sorted((s for s in secrets if isinstance(s, str) and len(s) >= MIN_SECRET),
                         key=len, reverse=True):
        out = out.replace(secret, MARK)
    return out


def scrub(value, secrets=()):
    """`value` with every string in it redacted; dict keys too."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {(redact(k, secrets) if isinstance(k, str) else k): scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, secrets) for v in value]
    return value
