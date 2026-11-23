"""Small Redis registry for executor discovery and operational status."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import docker
import redis

from app.core.config import get_settings


def executor_id_file() -> Path:
    settings = get_settings()
    return settings.executor_identity_path


def load_or_create_executor_id() -> str:
    path = executor_id_file()
    try:
        value = path.read_text(encoding="ascii").strip().lower()
        if len(value) == 16 and all(char in "0123456789abcdef" for char in value):
            return value
    except FileNotFoundError:
        pass
    value = os.urandom(8).hex()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as identity_file:
            identity_file.write(value + "\n")
    except FileExistsError:
        existing = path.read_text(encoding="ascii").strip().lower()
        if len(existing) == 16 and all(char in "0123456789abcdef" for char in existing):
            return existing
        raise RuntimeError(f"Executor identity file contains an invalid ID: {path}")
    return value


def get_eth0_ip() -> str:
    """Return the IPv4 address assigned to eth0."""
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", "eth0"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        for field in result.stdout.split():
            if "/" in field and field[0].isdigit():
                return field.split("/", 1)[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return "0.0.0.0"


class ExecutorRegistry:
    def __init__(self, executor_id: str | None = None) -> None:
        self.settings = get_settings()
        self.executor_id = executor_id or self.settings.executor_id or load_or_create_executor_id()
        self.client = redis.Redis.from_url(self.settings.redis_url, decode_responses=True)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.running_tasks: dict[str, dict[str, Any]] = {}
        self._state_lock = threading.Lock()
        self._state = "starting"
        self._pause_callback: Callable[[], None] | None = None
        self._resume_callback: Callable[[], None] | None = None
        self._drain_callback: Callable[[], None] | None = None
        self._shutdown_callback: Callable[[], None] | None = None
        self._last_control_request = ""
        self._controls_executor = False
        self._shutdown_requested = False

    @property
    def executor_key(self) -> str:
        return f"executors:{self.executor_id}"

    @property
    def health_key(self) -> str:
        return f"executors_health:{self.executor_id}"

    @property
    def control_key(self) -> str:
        return f"executor_control:{self.executor_id}"

    @property
    def completed_tasks_key(self) -> str:
        return f"executor_completed:{self.executor_id}"

    def configure_control(
        self,
        *,
        pause: Callable[[], None],
        resume: Callable[[], None],
        drain: Callable[[], None],
        shutdown: Callable[[], None],
    ) -> None:
        self._pause_callback = pause
        self._resume_callback = resume
        self._drain_callback = drain
        self._shutdown_callback = shutdown
        self._controls_executor = True

    def start(self) -> None:
        self._set_state("ready")
        self._write_executor()
        self._write_health()
        self.thread = threading.Thread(target=self._heartbeat_loop, name=f"executor-health-{self.executor_id}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        try:
            self.client.hset(
                self.executor_key,
                mapping={"status": "offline", "accepting_tasks": "false", "updated_at": str(time.time())},
            )
            self.client.expire(self.executor_key, self.settings.executor_registry_ttl_seconds)
            self.client.expire(self.health_key, self.settings.executor_registry_ttl_seconds)
        finally:
            self.client.close()

    def task_started(self, task_id: str, *, run_id: str, status: str = "running", step: str = "starting") -> None:
        payload = {
            "task_id": task_id,
            "run_id": run_id,
            "executor_id": self.executor_id,
            "status": status,
            "step": step,
            "started_at": str(time.time()),
            "updated_at": str(time.time()),
        }
        self.running_tasks[task_id] = payload
        self.client.hset(f"executingtasks:{task_id}", mapping=payload)
        self.client.expire(f"executingtasks:{task_id}", self.settings.executor_registry_ttl_seconds)
        self._write_executor()

    def task_finished(self, task_id: str, *, status: str = "completed") -> None:
        task_key = f"executingtasks:{task_id}"
        payload = self.running_tasks.pop(task_id, None) or self.client.hgetall(task_key)
        if payload:
            now = str(time.time())
            payload.update({"status": status, "finished_at": now, "updated_at": now})
            self.client.lpush(self.completed_tasks_key, json.dumps(payload))
            self.client.ltrim(self.completed_tasks_key, 0, self.settings.executor_completed_task_limit - 1)
            self.client.expire(self.completed_tasks_key, self.settings.executor_completed_task_ttl_seconds)
        self.client.delete(task_key)
        self._write_executor()
        self._maybe_finish_draining()

    def mark_draining(self) -> None:
        """Record warm shutdown without doing Redis I/O in the signal handler."""
        self._set_state("draining")

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _get_state(self) -> str:
        with self._state_lock:
            return self._state

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(self.settings.executor_health_interval_seconds):
            try:
                self._write_executor()
                self._write_health()
                self._process_control_request()
                self._maybe_finish_draining()
                now = str(time.time())
                for key in self.client.scan_iter(match="executingtasks:*"):
                    if self.client.hget(key, "executor_id") == self.executor_id:
                        self.client.hset(key, mapping={"updated_at": now})
                        self.client.expire(key, self.settings.executor_registry_ttl_seconds)
            except (redis.RedisError, OSError):
                # Redis is observability state; it must not terminate a run.
                continue

    def _write_executor(self) -> None:
        running_tasks = self._running_task_count()
        state = self._get_state()
        persisted_state = self.client.hget(self.executor_key, "status")
        if not self._controls_executor and persisted_state in {"paused", "draining"}:
            state = persisted_state
        elif state not in {"paused", "draining"}:
            state = "running" if running_tasks else "ready"
        mapping = {
            "executor_id": self.executor_id,
            "hostname": socket.gethostname(),
            "ip_address": get_eth0_ip(),
            "status": state,
            "accepting_tasks": "false" if state in {"paused", "draining"} else "true",
            "running_tasks": str(running_tasks),
            "updated_at": str(time.time()),
            "capabilities": json.dumps(["docker", "celery"]),
        }
        self.client.hset(self.executor_key, mapping=mapping)
        self.client.expire(self.executor_key, self.settings.executor_registry_ttl_seconds)

    def _process_control_request(self) -> None:
        request = self.client.hgetall(self.control_key)
        request_id = request.get("request_id", "")
        if (
            not request_id
            or request_id == self._last_control_request
            or request_id == request.get("acknowledged_request_id")
        ):
            return

        action = request.get("action")
        if action == "pause" and self._pause_callback is not None:
            self._pause_callback()
            self._set_state("paused")
        elif action == "resume" and self._resume_callback is not None:
            self._resume_callback()
            self._set_state("ready")
        elif action == "shutdown" and self._pause_callback is not None:
            self._pause_callback()
            self._set_state("draining")
            if self._drain_callback is not None:
                self._drain_callback()
        else:
            return

        self._last_control_request = request_id
        self.client.hset(
            self.control_key,
            mapping={
                "acknowledged_request_id": request_id,
                "acknowledged_at": str(time.time()),
                "acknowledged_action": action,
            },
        )
        self.client.expire(self.control_key, self.settings.executor_completed_task_ttl_seconds)
        self._write_executor()

    def _maybe_finish_draining(self) -> None:
        if self._get_state() != "draining" or self._shutdown_callback is None or self._shutdown_requested:
            return
        if self._running_task_count() == 0:
            self._shutdown_requested = True
            self._shutdown_callback()

    def _running_task_count(self) -> int:
        return sum(
            1
            for key in self.client.scan_iter(match="executingtasks:*")
            if self.client.hget(key, "executor_id") == self.executor_id
        )

    def _write_health(self) -> None:
        docker_status = "ok"
        try:
            docker.from_env().ping()
        except Exception:  # noqa: BLE001
            docker_status = "error"
        health = {
            "executor_id": self.executor_id,
            "status": "ok" if docker_status == "ok" else "degraded",
            "docker": docker_status,
            "redis": "ok",
            "ip_address": get_eth0_ip(),
            "updated_at": str(time.time()),
        }
        self.client.hset(self.health_key, mapping=health)
        self.client.expire(self.health_key, self.settings.executor_registry_ttl_seconds)
