"""Docker executor process entry point."""

import os
import signal
import threading
from typing import cast

from celery.platforms import EX_OK
from celery.signals import worker_ready, worker_shutdown, worker_shutting_down
from celery.worker import state as worker_state
from celery.worker.consumer.consumer import Consumer

from app.core.config import get_settings
from app.worker.celery_app import celery_app
from app.services.executor_registry import ExecutorRegistry

registry = ExecutorRegistry()
QUEUES = ("evaluation.default", "evaluation.retry")
shutdown_watchdog_started = threading.Event()


def _start_shutdown_watchdog() -> None:
    if shutdown_watchdog_started.is_set():
        return
    shutdown_watchdog_started.set()
    timeout = get_settings().executor_shutdown_timeout_seconds

    def force_exit() -> None:
        if not registry.stop_event.wait(timeout):
            os.kill(os.getpid(), signal.SIGQUIT)

    threading.Thread(target=force_exit, name="executor-shutdown-watchdog", daemon=True).start()


@worker_ready.connect
def _executor_ready(sender: object, **_kwargs: object) -> None:
    consumer = cast(Consumer, sender)

    def pause() -> None:
        for queue in QUEUES:
            consumer.call_soon(consumer.cancel_task_queue, queue)

    def resume() -> None:
        for queue in QUEUES:
            consumer.call_soon(consumer.add_task_queue, queue)

    def shutdown() -> None:
        def request_shutdown() -> None:
            worker_state.should_stop = EX_OK

        consumer.call_soon(request_shutdown)

    registry.configure_control(
        pause=pause,
        resume=resume,
        drain=_start_shutdown_watchdog,
        shutdown=shutdown,
    )
    registry.start()


@worker_shutting_down.connect
def _executor_draining(**_kwargs: object) -> None:
    registry.mark_draining()
    _start_shutdown_watchdog()


@worker_shutdown.connect
def _executor_shutdown(**_kwargs: object) -> None:
    registry.stop()


def main() -> None:
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            "--queues=evaluation.default,evaluation.retry",
            "--hostname=executor@%h",
        ]
    )
