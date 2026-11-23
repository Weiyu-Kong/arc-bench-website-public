"""Integrated proxy identity, per-request accounting and Run aggregation.

This service never mutates competition balances or invokes Meter.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import uuid4

import yaml
from sqlalchemy import and_, case, func, or_, select, text, update

from app.core.config import ROOT_DIR, get_settings
from app.db.session import SessionLocal
from app.models.model_usage import ModelProxySession, ModelUsageRequest
from app.models.run import Run
from app.services.model_proxy_network import validate_upstream_url
from app.services.model_pricing_service import ModelPricingService

log = logging.getLogger("arcbench.model_proxy")

class ModelProxyService:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    def prices(self, model):
        path = self.settings.model_proxy_pricing_path
        if not path.is_absolute():
            path = ROOT_DIR / path
        try:
            return ModelPricingService(path).prices(model)
        except (OSError, ValueError, ArithmeticError, yaml.YAMLError) as exc:
            # Invalid/missing pricing must not block model generation under
            # recorded-only accounting. No price or fee is fabricated.
            log.warning("Model price unavailable; model=%s error_type=%s", model, type(exc).__name__)
            return "unavailable", None

    @staticmethod
    def authenticate(db, token):
        session = db.scalar(select(ModelProxySession).where(
            ModelProxySession.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            ModelProxySession.revoked.is_(False), ModelProxySession.expires_at > datetime.utcnow()))
        run = db.get(Run, session.run_id) if session else None
        if (run is None or run.active_generation != session.generation or
                run.active_attempt_id != session.attempt_id or
                run.status not in {"STARTING", "RUNNING"}):
            raise PermissionError("Run is no longer active")
        return session, run

    def models(self, token):
        with SessionLocal() as db:
            _, run = self.authenticate(db, token)
            observed = db.scalars(select(ModelUsageRequest.model).where(
                ModelUsageRequest.run_id == run.id).distinct()).all()
            suggestions = {run.model_name, run.visual_model, *observed} - {None, ""}
        path = self.settings.model_proxy_pricing_path
        if not path.is_absolute():
            path = ROOT_DIR / path
        try:
            suggestions.update(ModelPricingService(path).models())
        except (OSError, ValueError, ArithmeticError, yaml.YAMLError):
            pass
        # Discovery suggestions are not an allowlist. Upstream availability is
        # determined by the submitted endpoint and API key.
        return sorted(suggestions)

    def issue(self, run_id, attempt_id, generation):
        base_url = self.settings.model_proxy_base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("Configure model_proxy.base_url to a runner-reachable proxy URL")
        if not parsed.path.endswith("/model-proxy/v1"):
            raise ValueError("model_proxy.base_url must end in /model-proxy/v1")
        secret = "arcproxy_" + secrets.token_urlsafe(36)
        now = datetime.utcnow()
        with SessionLocal() as db:
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None or run.billing_mode != "self_funded" or run.active_generation != generation:
                raise ValueError("Run is unavailable for personal model metering")
            if run.active_attempt_id != attempt_id:
                raise ValueError("Model proxy attempt no longer owns this run")
            validate_upstream_url(run.openai_base_url or "")
            if not run.openai_api_key:
                raise ValueError("External API key is missing")
            db.execute(update(ModelProxySession).where(ModelProxySession.run_id == run_id).values(revoked=True))
            expires_at = (
                now + timedelta(seconds=max(self.settings.worker_hard_time_limit_seconds,
                                            self.settings.model_proxy_request_timeout_seconds) + 900)
                if self.settings.model_proxy_request_timeout_seconds > 0 else datetime.max
            )
            session = ModelProxySession(id=uuid4().hex, run_id=run_id, attempt_id=attempt_id,
                                        generation=generation, token_hash=hashlib.sha256(secret.encode()).hexdigest(),
                                        created_at=now, expires_at=expires_at, revoked=False)
            has_prior_session = db.scalar(select(func.count()).select_from(ModelProxySession).where(
                ModelProxySession.run_id == run_id))
            db.add(session)
            if generation > 1 and not has_prior_session:
                # A pre-migration/previously direct attempt cannot be reconstructed
                # from this proxy's ledger. Keep subsequent execution available,
                # and retain the gap in the ledger for partial-cost reporting.
                db.add(ModelUsageRequest(
                    id=uuid4().hex, run_id=run_id, session_id=session.id, attempt_id=None,
                    model=run.model_name or "unknown", endpoint="pre-proxy-attempts",
                    upstream_host=urlsplit(run.openai_base_url).hostname,
                    price_version="unmetered-history", started_at=now, deadline_at=now,
                    finished_at=now, status="unmetered_history", error_code="prior_attempts_not_metered"))
            run.cost_source = "model_proxy"
            db.flush()
            self._apply_recorded_costs(db, run)
            db.commit()
            return session.id, secret, base_url

    @staticmethod
    def expire_requests(db, run_id=None):
        now = datetime.utcnow()
        # Unlimited calls have no clock deadline. Retire abandoned accounting
        # only after their run/session ends; a late usage response is still
        # accepted by finish(). Never expire a healthy, long-running call.
        ended_runs = select(Run.id).where(Run.status.in_(("PASSED", "FAILED", "CANCELLED", "PAUSED")))
        closed_sessions = select(ModelProxySession.id).where(or_(
            ModelProxySession.revoked.is_(True), ModelProxySession.expires_at < now,
        ))
        statement = update(ModelUsageRequest).where(
            ModelUsageRequest.status == "pending",
            or_(ModelUsageRequest.deadline_at < now, and_(
                ModelUsageRequest.deadline_at == datetime.max,
                or_(ModelUsageRequest.run_id.in_(ended_runs), ModelUsageRequest.session_id.in_(closed_sessions)),
            )))
        if run_id is not None:
            statement = statement.where(ModelUsageRequest.run_id == run_id)
        return db.execute(statement.values(
            status="interrupted", finished_at=now,
            error_code=case((ModelUsageRequest.deadline_at < now, "request_deadline_expired"),
                            else_="request_session_closed"),
        ))

    def begin(self, token, model, endpoint):
        version, prices = self.prices(model)
        now = datetime.utcnow()
        with SessionLocal() as db:
            # Cross-process admission lock: multiple API workers/replicas share
            # the same short transaction; no lock is held across network I/O.
            db.execute(text("SELECT pg_advisory_xact_lock(72894243, 19)"))
            self.expire_requests(db)
            session, run = self.authenticate(db, token)
            # The agent selects models per request; credentials remain bound to
            # this run and its submitted upstream URL.
            if not isinstance(model, str) or not model.strip() or len(model) > 120:
                raise ValueError("A model name of 1..120 characters is required")
            base_url = validate_upstream_url(run.openai_base_url or "")
            count = db.scalar(select(func.count()).select_from(ModelUsageRequest).where(ModelUsageRequest.status == "pending"))
            own = db.scalar(select(func.count()).select_from(ModelUsageRequest).where(
                ModelUsageRequest.status == "pending", ModelUsageRequest.run_id == run.id))
            if count >= self.settings.model_proxy_max_concurrent or own >= self.settings.model_proxy_max_concurrent_per_run:
                raise OverflowError("Model proxy concurrency limit")
            run = db.scalar(select(Run).where(Run.id == run.id).with_for_update().execution_options(populate_existing=True))
            if (run.active_generation != session.generation or run.active_attempt_id != session.attempt_id
                    or run.status not in {"STARTING", "RUNNING"}):
                raise PermissionError("Run is no longer active")
            request_id = uuid4().hex
            db.add(ModelUsageRequest(
                id=request_id, run_id=run.id, session_id=session.id, attempt_id=session.attempt_id,
                model=model, endpoint=endpoint, upstream_host=urlsplit(base_url).hostname,
                price_version=version, prices_json=json.dumps(prices) if prices is not None else None,
                started_at=now, deadline_at=(
                    now + timedelta(seconds=self.settings.model_proxy_request_timeout_seconds + 30)
                    if self.settings.model_proxy_request_timeout_seconds > 0 else datetime.max),
                status="pending"))
            db.flush()
            self._apply_recorded_costs(db, run)
            db.commit()
            return {"id": request_id, "run_id": run.id, "base_url": base_url,
                    "api_key": run.openai_api_key, "prices": prices}

    def finish(self, request_id, result, http_status=None, upstream_request_id=None, error_code=None):
        with SessionLocal() as db:
            row = db.scalar(select(ModelUsageRequest).where(ModelUsageRequest.id == request_id).with_for_update())
            if row is None:
                return
            run_id = row.run_id
            if row.status == "pending" or (
                row.status == "interrupted" and row.error_code in {"request_deadline_expired", "request_session_closed"}
                and row.usage_json is None and row.cost_cny is None
            ):
                # A response arriving after a sweep expired its pending row
                # may still carry usable usage. Retain that late accounting.
                row.status = result["status"]
                row.usage_json = json.dumps(result["usage"]) if result["usage"] is not None else None
                row.normalized_usage_json = json.dumps(result["normalized_usage"]) if result["normalized_usage"] is not None else None
                row.cost_cny = result["cost_cny"]
                row.reported_model = result["reported_model"]
                row.http_status = http_status
                row.upstream_request_id = (upstream_request_id or "")[:256] or None
                row.error_code = error_code
                row.finished_at = datetime.utcnow()
            db.commit()
        self.refresh_run(run_id)

    def revoke(self, session_id):
        with SessionLocal() as db:
            session = db.get(ModelProxySession, session_id)
            if session:
                session.revoked = True
                db.commit()
                run_id = session.run_id
            else:
                return
        self.refresh_run(run_id)

    @staticmethod
    def is_rejected_without_usage(row):
        """Site pricing policy: an HTTP 400 rejection carries no token charge.

        This deliberately excludes failed HTTP 200 payloads/streams, transport
        failures and other HTTP statuses. Preserve the original error ledger;
        only aggregation treats a recorded rejection without usage as zero.
        """
        return (
            row.status == "upstream_error"
            and row.http_status == 400
            and row.error_code == "upstream_http_error"
            and row.usage_json is None
            and row.normalized_usage_json is None
            and row.cost_cny is None
        )

    @staticmethod
    def decode_object(value):
        if not isinstance(value, str) or len(value) > 65536:
            return None
        try:
            result = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return None
        return result if isinstance(result, dict) else None

    @staticmethod
    def recorded_cost(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            cost = Decimal(str(value))
        except (ValueError, TypeError, InvalidOperation):
            return None
        return cost if cost.is_finite() and cost >= 0 else None

    @classmethod
    def recorded_tokens(cls, row):
        usage = cls.decode_object(row.normalized_usage_json)
        fields = {"input", "output", "cache_read", "cache_write_5m", "cache_write_1h"}
        if usage is None or set(usage) != fields:
            return None
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000
               for value in usage.values()):
            return None
        return sum(usage.values())

    @classmethod
    def aggregate(cls, rows):
        """Sum valid recorded values, independently of request success status.

        Missing values are skipped, never fabricated from prompts or current
        prices. Completeness remains diagnostic; it no longer gates scoring.
        """
        states = {}
        cost = Decimal(0)
        tokens = 0
        non_billable = skipped_cost = skipped_tokens = 0
        complete = bool(rows)
        for row in rows:
            states[row.status] = states.get(row.status, 0) + 1
            if cls.is_rejected_without_usage(row):
                non_billable += 1
                continue
            row_cost = cls.recorded_cost(row.cost_cny)
            row_tokens = cls.recorded_tokens(row)
            if row_cost is not None:
                cost += row_cost
            else:
                skipped_cost += 1
            if row_tokens is not None:
                tokens += row_tokens
            else:
                skipped_tokens += 1
            if row.status != "complete" or row_cost is None or row_tokens is None:
                complete = False
        return {"request_count": len(rows), "statuses": states, "usage_complete": complete,
                "accounting_mode": "recorded_only",
                "non_billable_request_count": non_billable,
                "skipped_cost_request_count": skipped_cost,
                "skipped_token_request_count": skipped_tokens,
                "observed_tokens": tokens, "token_count": tokens,
                "observed_cost_cny": str(cost), "cost_cny": str(cost), "currency": "CNY"}

    def _apply_recorded_costs(self, db, run, rows=None):
        if rows is None:
            rows = db.scalars(select(ModelUsageRequest).where(ModelUsageRequest.run_id == run.id)).all()
        result = self.aggregate(rows)
        run.token_count = result["token_count"]
        run.token_cost_usd = float(result["cost_cny"])
        run.token_cost_currency = "CNY"
        run.cost_complete = result["usage_complete"]
        return result

    def refresh_run(self, run_id):
        with SessionLocal() as db:
            # Match begin(): expire ledger rows before locking the Run. Keep
            # each transaction small so historical refresh can run with traffic.
            self.expire_requests(db, run_id)
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None:
                return
            if run.cost_source != "model_proxy" and run.billing_mode != "self_funded":
                return
            rows = db.scalars(select(ModelUsageRequest).where(ModelUsageRequest.run_id == run_id)).all()
            if run.cost_source != "model_proxy" and not rows:
                # Pre-proxy personal runs have no reconstructible request
                # ledger. Preserve a valid recorded CNY total; missing values
                # contribute zero, per the same recorded-only policy.
                previous_cost = self.recorded_cost(run.token_cost_usd)
                known_cost = previous_cost is not None and str(run.token_cost_currency or "").upper() == "CNY"
                if not known_cost:
                    run.token_cost_usd = 0.0
                    run.token_cost_currency = "CNY"
                    run.cost_complete = False
                if run.token_count is None or run.token_count < 0:
                    run.token_count = 0
                result = self.aggregate([])
                result.update(cost_cny=str(previous_cost) if known_cost else "0",
                              observed_cost_cny=str(previous_cost) if known_cost else "0",
                              token_count=run.token_count, observed_tokens=run.token_count,
                              accounting_mode="legacy_recorded")
            else:
                run.cost_source = "model_proxy"
                result = self._apply_recorded_costs(db, run, rows)
            db.commit()
            return result

    def inspect(self, run_id, limit=100, offset=0):
        summary = self.refresh_run(run_id)
        with SessionLocal() as db:
            rows = db.scalars(select(ModelUsageRequest).where(ModelUsageRequest.run_id == run_id)
                              .order_by(ModelUsageRequest.started_at, ModelUsageRequest.id).limit(limit).offset(offset)).all()
            items = []
            for row in rows:
                non_billable = self.is_rejected_without_usage(row)
                recorded_cost = self.recorded_cost(row.cost_cny)
                items.append({"id": row.id, "attempt_id": row.attempt_id, "model": row.model,
                              "reported_model": row.reported_model, "endpoint": row.endpoint,
                              "upstream_host": row.upstream_host, "upstream_request_id": row.upstream_request_id,
                              "price_version": row.price_version, "prices": self.decode_object(row.prices_json),
                              "usage": self.decode_object(row.usage_json),
                              "normalized_usage": self.decode_object(row.normalized_usage_json),
                              "cost_cny": str(recorded_cost) if recorded_cost is not None else None,
                              "billing_status": "rejected_no_charge" if non_billable else "recorded" if recorded_cost is not None else "skipped",
                              "accounted_cost_cny": str(recorded_cost) if recorded_cost is not None else "0",
                              "status": row.status, "http_status": row.http_status, "error_code": row.error_code,
                              "started_at": row.started_at.isoformat(),
                              "finished_at": row.finished_at.isoformat() if row.finished_at else None})
            return {"run_id": run_id, "summary": summary, "items": items, "limit": limit, "offset": offset}
