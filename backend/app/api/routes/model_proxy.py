"""Website-owned model proxy. No Meter dependency or budget deductions."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.services.model_proxy_network import PublicTransport
from app.services.model_proxy_errors import upstream_error_details
from app.services.model_proxy_compat import prepare_upstream_body
from app.services.model_proxy_service import ModelProxyService
from app.services.model_usage_parser import UsageTracker
from app.services.submission_service import RunService

router = APIRouter(prefix="/model-proxy", tags=["model-proxy"])
usage_router = APIRouter(prefix="/runs", tags=["runs"])
log = logging.getLogger("arcbench.model_proxy")


def credential_from_request(request):
    authorization = request.headers.get("authorization", "")
    value = authorization[7:].strip() if authorization.lower().startswith("bearer ") else request.headers.get("x-api-key", "").strip()
    if not value.startswith("arcproxy_") or len(value) > 256:
        raise HTTPException(401, "A run-scoped proxy credential is required")
    return value


@router.get("/v1/models")
async def model_list(request: Request):
    if not get_settings().model_proxy_enabled:
        raise HTTPException(503, "Integrated model proxy is disabled")
    try:
        names = await run_in_threadpool(ModelProxyService().models, credential_from_request(request))
    except PermissionError as exc:
        raise HTTPException(401, "Run is no longer active") from exc
    return {"object": "list", "data": [{"id": name, "object": "model", "owned_by": "upstream"} for name in names]}


@usage_router.get("/{run_id}/model-usage")
def model_usage(run_id: str, limit: int = 100, offset: int = 0,
                db: Session = Depends(get_db), user: User = Depends(require_current_user)):
    try:
        RunService(db).get_submission(run_id, user.id)
    except LookupError as exc:
        raise HTTPException(404, "Run not found") from exc
    if not 1 <= limit <= 1000 or offset < 0:
        raise HTTPException(400, "Invalid pagination")
    return ModelProxyService().inspect(run_id, limit, offset)


def error(status, message, request_id=None, *, upstream_details=None):
    headers = {"x-arc-request-id": request_id} if request_id else {}
    payload = {"message": message, "type": "model_proxy_error", "request_id": request_id}
    if upstream_details:
        payload["upstream"] = upstream_details
    return JSONResponse({"error": payload},
                        status_code=status, headers=headers)


async def bounded_body(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, "Request body too large")
    return bytes(data)


async def bounded_response(upstream, limit):
    data = bytearray()
    async for chunk in upstream.aiter_bytes():
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("response_too_large")
    return bytes(data)


async def read_error_details(upstream, *secrets):
    data = bytearray()
    try:
        async for chunk in upstream.aiter_bytes():
            if len(data) + len(chunk) > 64 * 1024:
                return {}
            data.extend(chunk)
    except (httpx.HTTPError, TimeoutError):
        return {}
    return upstream_error_details(bytes(data), *secrets)


@router.post("/v1/chat/completions")
@router.post("/v1/responses")
@router.post("/v1/messages")
async def proxy(request: Request):
    settings = get_settings()
    if not settings.model_proxy_enabled:
        raise HTTPException(503, "Integrated model proxy is disabled")
    credential = credential_from_request(request)
    service = ModelProxyService(settings)
    if request.query_params:
        return error(400, "Query parameters are not supported")
    try:
        body = json.loads(await bounded_body(request, settings.model_proxy_max_body_bytes))
    except (ValueError, UnicodeError):
        return error(400, "A JSON object is required")
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        return error(400, "A JSON object with a model alias is required")
    alias = body["model"]
    if not alias.strip() or len(alias) > 120:
        return error(400, "Model name must contain 1..120 characters")
    endpoint = request.url.path.rsplit("/v1", 1)[-1]
    protocol = "anthropic" if endpoint == "/messages" else "chat" if endpoint == "/chat/completions" else "responses"
    if not isinstance(body.get("stream", False), bool):
        return error(400, "stream must be a boolean")
    if body.get("background") or body.get("service_tier") not in (None, "auto", "default"):
        return error(400, "Background responses and premium service tiers are not supported")
    if body.get("audio") is not None or body.get("modalities") not in (None, ["text"]):
        return error(400, "Audio output and non-text output modalities are not supported")
    messages = body.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list) and any(isinstance(part, dict) and part.get("type") == "input_audio" for part in content):
                return error(400, "Audio input pricing is not supported")
    # Hosted tools may have non-token charges; local function tools are supported.
    tools = body.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(tool, dict) or
        tool.get("type", "custom" if protocol == "anthropic" else "") not in
        ({"custom"} if protocol == "anthropic" else {"function"}) for tool in tools):
        return error(400, "Only client-executed function/custom tools are supported")
    streaming = body.get("stream", False)
    if protocol == "chat" and streaming:
        options = body.get("stream_options")
        if options is None:
            options = {}
        if not isinstance(options, dict):
            return error(400, "stream_options must be an object")
        if settings.model_proxy_request_stream_usage and "include_usage" not in options:
            body["stream_options"] = {**options, "include_usage": True}
    try:
        context = await run_in_threadpool(service.begin, credential, alias, endpoint)
    except PermissionError:
        return error(401, "Proxy credential is unavailable")
    except OverflowError:
        return error(429, "Model proxy concurrency limit reached")
    except (ValueError, ArithmeticError):
        return error(400, "Upstream URL or price configuration is invalid")
    except Exception:
        return error(503, "Model accounting or configuration is unavailable")
    request_id = context["id"]
    body = prepare_upstream_body(body, context["base_url"], endpoint)
    headers = {"content-type": "application/json", "accept-encoding": "identity"}
    if protocol == "anthropic":
        headers["x-api-key"] = context["api_key"]
        headers["anthropic-version"] = request.headers.get("anthropic-version", "2023-06-01")
    else:
        headers["authorization"] = f"Bearer {context['api_key']}"
    # Preserve SDK protocol negotiation without forwarding website cookies or
    # the caller's temporary Authorization / x-api-key to the upstream.
    for name in ("anthropic-beta", "openai-beta", "user-agent"):
        if value := request.headers.get(name):
            headers[name] = value
    client = httpx.AsyncClient(transport=PublicTransport(),
        timeout=httpx.Timeout(connect=None, read=settings.model_proxy_read_timeout_seconds or None, write=None, pool=None),
        follow_redirects=False, trust_env=False)
    tracker = UsageTracker(protocol, settings.model_proxy_max_event_bytes)
    upstream = None
    started = time.monotonic()
    closed = False

    def remaining_timeout():
        limit = settings.model_proxy_request_timeout_seconds
        return max(0.001, limit - (time.monotonic() - started)) if limit > 0 else None

    async def persist_and_close(status, code=None):
        nonlocal closed
        if closed:
            return
        # SDKs may close an SSE connection as soon as they consume [DONE],
        # response.completed or message_stop. ASGI can cancel our generator
        # before the upstream socket reaches EOF. A received terminal event
        # still proves completion; result() must independently validate usage
        # and prices before allowing a complete ledger entry.
        if status in {"client_disconnected", "incomplete"} and tracker.terminal and not tracker.failed:
            status, code = "complete", None
        # Ledger commit precedes closing the transport. No upstream response
        # bodies, credentials or exception strings are included in logs.
        result = tracker.result(context["prices"], status)
        await run_in_threadpool(service.finish, request_id, result,
            upstream.status_code if upstream is not None else None,
            (upstream.headers.get("x-request-id") or upstream.headers.get("request-id")) if upstream is not None else None,
            code)
        if result["status"] != "complete":
            log.warning("Model cost incomplete; request_id=%s run_id=%s status=%s error_code=%s",
                        request_id, context["run_id"], result["status"], code)
        closed = True
        if upstream is not None:
            # ASGI cancels streaming tasks on disconnect. Shield connection
            # cleanup so cancelled streams do not leak pooled connections.
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(5):
                    await client.aclose()

    async def finalize(status, code=None):
        with anyio.CancelScope(shield=True):
            try:
                await persist_and_close(status, code)
            finally:
                with anyio.move_on_after(5):
                    await client.aclose()

    try:
        outgoing = client.build_request("POST", context["base_url"] + endpoint,
                                                         headers=headers, json=body)
        async with asyncio.timeout(remaining_timeout()):
            upstream = await client.send(outgoing, stream=True)
        if not 200 <= upstream.status_code < 300:
            status = upstream.status_code if 400 <= upstream.status_code <= 599 else 502
            details = await read_error_details(upstream, context["api_key"], credential)
            await finalize("upstream_error", "upstream_http_error")
            reason = details.get("message") or "Upstream did not provide a readable JSON error message"
            return error(status, f"Upstream HTTP {upstream.status_code}: {reason}", request_id,
                         upstream_details=details)
        if streaming:
            if "text/event-stream" not in upstream.headers.get("content-type", "").lower():
                await finalize("upstream_error", "expected_sse")
                return error(502, "Upstream did not return an SSE stream", request_id)

            async def stream():
                status = "incomplete"
                code = None
                try:
                    async with asyncio.timeout(remaining_timeout()):
                        async for chunk in upstream.aiter_bytes():
                            tracker.feed(chunk)
                            yield chunk
                    tracker.finish_stream()
                    status = "upstream_error" if tracker.failed else "complete" if tracker.terminal else "incomplete"
                    if status != "complete":
                        code = "upstream_stream_error" if tracker.failed else "missing_terminal_event"
                except asyncio.CancelledError:
                    status, code = "client_disconnected", "client_disconnected"
                    raise
                except (httpx.HTTPError, TimeoutError, ValueError):
                    status, code = "incomplete", "stream_transport_error"
                    # Terminate without fabricating a provider success event.
                except Exception:
                    status, code = "internal_error", "stream_internal_error"
                    log.error("Model proxy stream failed; request_id=%s", request_id)
                finally:
                    await finalize(status, code)

            return StreamingResponse(stream(), media_type="text/event-stream",
                                     headers={"x-arc-request-id": request_id, "cache-control": "no-cache",
                                              "x-accel-buffering": "no"},
                                     background=BackgroundTask(finalize, "client_disconnected", "response_not_consumed"))
        async with asyncio.timeout(remaining_timeout()):
            data = await bounded_response(upstream, settings.model_proxy_max_response_bytes)
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeError):
            await finalize("upstream_error", "invalid_json_response")
            return error(502, "Upstream returned invalid JSON", request_id)
        tracker.payload(payload)
        bad = not isinstance(payload, dict) or bool(payload.get("error"))
        await finalize("upstream_error" if bad else "complete", "upstream_payload_error" if bad else None)
        return Response(data, status_code=upstream.status_code, media_type="application/json",
                        headers={"x-arc-request-id": request_id})
    except asyncio.CancelledError:
        await finalize("client_disconnected", "client_disconnected")
        raise
    except (httpx.HTTPError, TimeoutError, ValueError):
        await finalize("transport_error", "upstream_transport_error")
        return error(502, "Upstream connection, response size or timeout error", request_id)
    except Exception:
        # Failing to persist accounting must not quietly produce an unmetered success.
        log.error("Model proxy request failed; request_id=%s", request_id)
        try:
            await finalize("internal_error", "gateway_internal_error")
        finally:
            if upstream is not None:
                await client.aclose()
        return error(503, "Model accounting unavailable", request_id)
