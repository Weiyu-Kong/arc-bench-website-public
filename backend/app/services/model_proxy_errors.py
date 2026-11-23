"""Bounded, credential-redacted upstream error details for the owning caller."""
from __future__ import annotations

import json
import re


def upstream_error_details(data: bytes, *secrets: str) -> dict:
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    source = payload.get("error")
    if isinstance(source, str):
        source = {"message": source}
    if not isinstance(source, dict):
        source = payload

    def redact(value, limit):
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return None
        text = str(value)
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
        text = re.sub(r"\b(?:sk-|arcproxy_)[A-Za-z0-9_.\-*]+", "[REDACTED]", text)
        text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,<>]+",
                      r"\1[REDACTED]", text)
        return " ".join(text.split())[:limit] or None

    result = {}
    for name, limit in (("message", 1500), ("type", 120), ("code", 120), ("param", 120)):
        value = redact(source.get(name), limit)
        if value is not None:
            result[name] = value
    # FastAPI-compatible upstream services sometimes report a string detail.
    if "message" not in result:
        detail = redact(payload.get("detail") or source.get("msg"), 1500)
        if detail is not None:
            result["message"] = detail
        elif isinstance(payload.get("detail"), list):
            messages = []
            for item in payload["detail"][:5]:
                if isinstance(item, dict):
                    # Never copy Pydantic's `input` / `ctx` values: these may
                    # contain the original request body or credentials.
                    message = redact(item.get("msg"), 240)
                    if message:
                        messages.append(message)
            if messages:
                result["message"] = "; ".join(messages)
    return result
