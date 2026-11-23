from __future__ import annotations

import hashlib
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from typing import Callable

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings


_held_distributed_locks: ContextVar[frozenset[str]] = ContextVar("held_distributed_locks", default=frozenset())


def advisory_lock_key(*parts: str) -> int:
    payload = "\0".join(part.strip() for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


@contextmanager
def hold_advisory_xact_lock(db: Session, *parts: str):
    for part in sorted(parts):
        db.execute(select(func.pg_advisory_xact_lock(advisory_lock_key(part))))
    yield


@contextmanager
def hold_distributed_lock(name: str):
    """Serialize shared storage mutations across API and worker processes."""
    held = _held_distributed_locks.get()
    if name in held:
        yield
        return

    client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=10)
    lock = client.lock(f"arcbench:lock:{name}", timeout=1800, blocking_timeout=30)
    acquired = False
    try:
        acquired = bool(lock.acquire())
        if not acquired:
            raise TimeoutError(f"Timed out waiting for shared state lock '{name}'")
        token = _held_distributed_locks.set(held | {name})
        try:
            yield
        finally:
            _held_distributed_locks.reset(token)
    finally:
        if acquired:
            try:
                lock.release()
            except redis.exceptions.LockError:
                pass
        client.close()


def distributed_locked(name_factory: Callable[..., str]):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with hold_distributed_lock(name_factory(*args, **kwargs)):
                return function(*args, **kwargs)

        return wrapped

    return decorator
