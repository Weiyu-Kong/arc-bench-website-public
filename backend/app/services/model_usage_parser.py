from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP


def count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
        raise ValueError("invalid_token_count")
    return value


def normalize(raw, protocol):
    """Return disjoint billable categories; never add reasoning tokens twice."""
    output = count(raw.get("output_tokens") if protocol != "chat" else raw.get("completion_tokens"))
    if protocol == "anthropic":
        regular = count(raw.get("input_tokens"))
        cached = count(raw.get("cache_read_input_tokens", 0))
        written = count(raw.get("cache_creation_input_tokens", 0))
        detail = raw.get("cache_creation") or {}
        long = count(detail.get("ephemeral_1h_input_tokens", 0))
        short = count(detail.get("ephemeral_5m_input_tokens", written - long))
        if short + long != written:
            raise ValueError("cache_write_breakdown_mismatch")
    else:
        total = count(raw.get("prompt_tokens") if protocol == "chat" else raw.get("input_tokens"))
        detail = raw.get("prompt_tokens_details" if protocol == "chat" else "input_tokens_details") or {}
        cached = count(detail.get("cached_tokens", raw.get("prompt_cache_hit_tokens", 0)))
        if "prompt_cache_hit_tokens" in raw and count(raw["prompt_cache_hit_tokens"]) != cached:
            raise ValueError("cache_hit_breakdown_mismatch")
        if "prompt_cache_miss_tokens" in raw and count(raw["prompt_cache_miss_tokens"]) + cached != total:
            raise ValueError("cache_miss_breakdown_mismatch")
        short = count(detail.get("cache_write_tokens", 0))
        long = 0
        regular = total - cached - short
        if regular < 0:
            raise ValueError("cache_tokens_exceed_input")
    return {"input": regular, "output": output, "cache_read": cached,
            "cache_write_5m": short, "cache_write_1h": long}


def price(usage, prices):
    result = sum(Decimal(value) * Decimal(str(prices[key])) / Decimal(1_000_000)
                 for key, value in usage.items())
    return result.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)


class UsageTracker:
    def __init__(self, protocol, max_event_bytes):
        self.protocol = protocol
        self.max_event_bytes = max_event_bytes
        self.raw = None
        self.reported_model = None
        self.terminal = False
        self.failed = False
        self.parse_error = False
        self.buffer = bytearray()
        self.data_lines = []
        self.event_size = 0
        self.discard_event = False

    def payload(self, payload):
        if not isinstance(payload, dict):
            self.parse_error = True
            return
        if isinstance(payload.get("model"), str):
            self.reported_model = payload["model"][:256]
        usage = payload.get("usage")
        if isinstance(usage, dict):
            # Store only metering fields, never arbitrary response/user content.
            allowed = {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
                       "prompt_tokens_details", "input_tokens_details", "output_tokens_details",
                       "completion_tokens_details", "cache_read_input_tokens", "cache_creation_input_tokens", "cache_creation",
                       "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
            selected = {key: value for key, value in usage.items() if key in allowed}
            if len(json.dumps(selected)) > 16384:
                self.parse_error = True
                return
            self.raw = {**(self.raw or {}), **selected}

    def event(self, data):
        if data.strip() == "[DONE]":
            self.terminal = True
            return
        try:
            item = json.loads(data)
        except (ValueError, UnicodeError):
            self.parse_error = True
            return
        if not isinstance(item, dict):
            self.parse_error = True
            return
        kind = item.get("type")
        self.payload(item)
        if kind == "message_start":
            self.payload(item.get("message"))
        if kind in {"response.completed", "response.incomplete", "response.failed"}:
            self.payload(item.get("response"))
            self.terminal = True
            self.failed = kind == "response.failed"
        if kind == "message_stop":
            self.terminal = True
        if kind == "error" or item.get("error"):
            self.failed = True

    def line(self, line):
        if not line:
            data = b"\n".join(self.data_lines) if self.data_lines and not self.discard_event else None
            self.data_lines.clear()
            self.event_size = 0
            self.discard_event = False
            if data is not None:
                self.event(data.decode("utf-8", errors="strict"))
        elif line.startswith(b"data:") and not self.discard_event:
            value = line[5:].lstrip(b" ")
            self.event_size += len(value)
            if self.event_size > self.max_event_bytes:
                self.parse_error = True
                self.discard_event = True
                self.data_lines.clear()
            else:
                self.data_lines.append(value)

    def feed(self, chunk):
        self.buffer.extend(chunk)
        while True:
            pos = self.buffer.find(b"\n")
            if pos < 0:
                if len(self.buffer) > self.max_event_bytes:
                    self.parse_error = True
                    raise ValueError("sse_line_too_large")
                return
            line = bytes(self.buffer[:pos]).rstrip(b"\r")
            del self.buffer[:pos + 1]
            try:
                self.line(line)
            except (UnicodeError, TypeError, AttributeError):
                self.parse_error = True

    def finish_stream(self):
        if self.buffer:
            try:
                self.line(bytes(self.buffer).rstrip(b"\r"))
            except (UnicodeError, TypeError, AttributeError):
                self.parse_error = True
            self.buffer.clear()
        try:
            self.line(b"")
        except (UnicodeError, TypeError, AttributeError):
            self.parse_error = True

    def result(self, prices, status):
        normalized = cost = None
        if self.raw is not None:
            try:
                normalized = normalize(self.raw, self.protocol)
                if prices is not None:
                    cost = price(normalized, prices)
                elif status == "complete":
                    status = "price_missing"
            except (ValueError, TypeError, AttributeError, ArithmeticError):
                if status == "complete":
                    status = "usage_invalid"
        elif status == "complete":
            status = "usage_missing"
        if self.parse_error and status in {"complete", "price_missing"}:
            status = "usage_invalid"
        return {"status": status, "usage": self.raw, "normalized_usage": normalized,
                "cost_cny": cost, "reported_model": self.reported_model}
