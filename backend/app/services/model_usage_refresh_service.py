"""Refresh historical personal-run costs in the API background after startup."""
from __future__ import annotations

import logging
import threading

from sqlalchemy import func, select, text

from app.db.session import SessionLocal, engine
from app.models.run import Run
from app.services.model_proxy_service import ModelProxyService

log = logging.getLogger("arcbench.model_usage_refresh")


class ModelUsageRefreshWorker(threading.Thread):
    def __init__(self, settings):
        super().__init__(name="model-usage-refresh", daemon=True)
        self.service = ModelProxyService(settings)
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()
        self.join(timeout=5)

    def run(self):
        try:
            self._refresh()
        except Exception:
            # A failed background refresh must not take authentication or other
            # API routes down. Restarting the API retries the idempotent scan.
            log.exception("Historical model cost refresh failed; API remains available")

    def _refresh(self):
        # Keep a dedicated connection for this session-level lock; commits in
        # refresh_run use other connections and do not release the scan lock.
        with engine.connect() as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(72894243, 20)"))
            connection.commit()
            if not acquired:
                log.info("Historical model cost refresh already running in another API worker")
                return
            try:
                self._scan()
            finally:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(72894243, 20)"))
                connection.commit()

    def _scan(self):
        last_id = ""
        updated = failed = 0
        with SessionLocal() as db:
            maximum_id = db.scalar(select(func.max(Run.id)))
        if maximum_id is None:
            log.info("Historical model cost refresh finished: no runs")
            return
        log.info("Historical model cost refresh started (recorded-only accounting)")
        while not self.stop_requested.is_set():
            with SessionLocal() as db:
                run_ids = db.scalars(select(Run.id).where(
                    Run.id > last_id, Run.id <= maximum_id,
                    (Run.billing_mode == "self_funded") | (Run.cost_source == "model_proxy"),
                ).order_by(Run.id).limit(100)).all()
            if not run_ids:
                break
            for run_id in run_ids:
                if self.stop_requested.is_set():
                    break
                try:
                    if self.service.refresh_run(run_id) is not None:
                        updated += 1
                except Exception:
                    failed += 1
                    log.exception("Historical model cost refresh skipped failed run_id=%s", run_id)
                last_id = run_id
            log.info("Historical model cost refresh progress: updated=%s failed=%s", updated, failed)
        log.info("Historical model cost refresh %s: updated=%s failed=%s",
                 "stopped" if self.stop_requested.is_set() else "finished", updated, failed)
