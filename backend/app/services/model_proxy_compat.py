"""Narrow, documented protocol adaptations for known upstream services."""
from __future__ import annotations

import json
from urllib.parse import urlsplit


def prepare_upstream_body(body: dict, base_url: str, endpoint: str) -> dict:
    """DeepSeek Chat accepts JSON mode, but not OpenAI's json_schema mode.

    Preserve the schema in model instructions. The caller remains responsible
    for validating the resulting JSON: JSON mode does not enforce a schema.
    Do not apply this to Responses, which supports JSON Schema natively.
    """
    if urlsplit(base_url).hostname != "api.deepseek.com" or endpoint != "/chat/completions":
        return body
    response_format = body.get("response_format")
    if not isinstance(response_format, dict) or response_format.get("type") != "json_schema":
        return body
    descriptor = response_format.get("json_schema")
    schema = descriptor.get("schema") if isinstance(descriptor, dict) else None
    messages = body.get("messages")
    if not isinstance(schema, dict) or not isinstance(messages, list):
        return body
    # JSON-object mode cannot faithfully represent a requested top-level array,
    # string, or union. Leave those requests intact so the upstream can reject
    # them explicitly rather than silently changing the output shape.
    if schema.get("type") != "object" and not ("properties" in schema and "type" not in schema):
        return body
    instruction = (
        "Return a JSON object only, without Markdown or surrounding text. "
        "The JSON object must conform to this JSON Schema. "
        "Preserve all required fields, field types, enum values, and nesting.\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )
    updated_messages = list(messages)
    if (updated_messages and isinstance(updated_messages[0], dict)
            and updated_messages[0].get("role") == "system"
            and isinstance(updated_messages[0].get("content"), str)):
        updated_messages[0] = {**updated_messages[0], "content": updated_messages[0]["content"] + "\n\n" + instruction}
    else:
        updated_messages.insert(0, {"role": "system", "content": instruction})
    return {**body, "messages": updated_messages, "response_format": {"type": "json_object"}}
