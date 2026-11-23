"""Validate and pin public DNS answers at the actual TCP connect boundary."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend


def validate_upstream_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("External model URL must use HTTPS port 443 without credentials or query parameters")
    if parsed.path.rstrip("/").endswith("/model-proxy/v1"):
        raise ValueError("The upstream URL must not point back to the model proxy")
    return value.rstrip("/")


class PublicNetworkBackend(AnyIOBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if port != 443:
            raise httpcore.ConnectError("upstream_port_not_allowed")
        try:
            async with asyncio.timeout(timeout):
                answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (OSError, TimeoutError) as exc:
            raise httpcore.ConnectError("upstream_dns_failed") from exc
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses:
            raise httpcore.ConnectError("upstream_dns_empty")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            mapped = getattr(ip, "ipv4_mapped", None)
            if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                    or (mapped is not None and not mapped.is_global)):
                raise httpcore.ConnectError("upstream_address_not_public")
            # Block transition addresses that could embed an internal IPv4 target.
            if isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour is not None or ip.teredo is not None):
                raise httpcore.ConnectError("upstream_transition_address_not_allowed")
            if isinstance(ip, ipaddress.IPv6Address) and any(ip in prefix for prefix in (
                    ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))):
                raise httpcore.ConnectError("upstream_translation_address_not_allowed")
        # Connect to a validated literal IP, preventing a second DNS resolution
        # from rebinding to a private address. HTTPCore keeps the original origin
        # hostname for Host, TLS SNI and certificate verification.
        deadline = time.monotonic() + timeout if timeout is not None else None
        last_error = None
        for address in addresses:
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise httpcore.ConnectTimeout("upstream_connect_timeout")
            try:
                return await super().connect_tcp(address, port, timeout=remaining,
                                                 local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        raise last_error


class PublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        super().__init__(trust_env=False)
        # HTTPX/HTTPCore versions are pinned because this replaces the transport
        # pool. NetworkBackend is HTTPCore's documented connection extension.
        self._pool = httpcore.AsyncConnectionPool(network_backend=PublicNetworkBackend(),
                                                  max_connections=1, max_keepalive_connections=0, retries=0)
