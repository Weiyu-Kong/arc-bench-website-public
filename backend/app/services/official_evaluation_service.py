"""Revoke and reconcile historical evaluation keys without competition budgets."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.competition_account import CompetitionBudgetTransaction
from app.models.run import Run


class OfficialEvaluationError(RuntimeError):
    pass


class OfficialEvaluationService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self.settings.meter_admin_token.strip())

    @property
    def _base_url(self) -> str:
        return (self.settings.meter_admin_base_url or self.settings.meter_base_url).strip().rstrip("/")

    def provision(self, run: Run, attempt_id: str, generation: int) -> str:
        raise OfficialEvaluationError(
            "Competition credits are no longer supported. Upload a new submission with your own API key."
        )

    def settle(self, run: Run) -> Decimal:
        if run.billing_mode != "official_evaluation":
            return Decimal("0")
        if run.settled_at is not None and run.budget_charge_cny is not None:
            return Decimal(run.budget_charge_cny)
        if not run.meter_key_name:
            raise OfficialEvaluationError("Official evaluation key was never provisioned")

        self._request("POST", f"/api/admin/access-keys/{run.meter_key_name}/revoke")
        run.meter_key_status = "revoked"
        self.db.commit()

        deadline = time.monotonic() + max(0.0, self.settings.meter_usage_settle_timeout_seconds)
        settlement: dict | None = None
        while True:
            payload = self._request("GET", f"/api/admin/access-keys/{run.meter_key_name}/settlement")
            candidate = payload.get("settlement")
            if isinstance(candidate, dict):
                settlement = candidate
                pending = candidate.get("pending_billing_events")
                if isinstance(pending, int) and pending <= 0:
                    break
            if time.monotonic() >= deadline:
                raise OfficialEvaluationError("ONR billing did not settle before the configured timeout")
            time.sleep(0.5)

        account = settlement.get("account") if settlement else None
        if not isinstance(account, dict) or str(account.get("currency") or "").upper() != "CNY":
            raise OfficialEvaluationError("ONR settlement returned an invalid billing account")
        try:
            initial_balance = Decimal(str(account.get("credit") or ""))
            ending_balance = Decimal(str(account.get("balance") or ""))
        except InvalidOperation as exc:
            raise OfficialEvaluationError("ONR settlement returned invalid credit or balance") from exc
        # Charge exactly the decrease in this key's own balance. This keeps
        # settlement independent from the ledger's cumulative `spent` field
        # and makes the accounting rule explicit for one-run keys.
        spent = initial_balance - ending_balance
        if initial_balance < 0 or ending_balance < 0 or spent < 0:
            raise OfficialEvaluationError("ONR settlement returned an invalid balance decrease")

        self.db.rollback()
        locked_run = self.db.scalar(select(Run).where(Run.id == run.id).with_for_update())
        if locked_run is None:
            raise OfficialEvaluationError("Run disappeared before budget settlement")
        idempotency_key = f"official-evaluation:{locked_run.meter_key_name}:settle"
        existing = self.db.scalar(
            select(CompetitionBudgetTransaction).where(
                CompetitionBudgetTransaction.idempotency_key == idempotency_key
            )
        )
        if locked_run.settled_at is not None and locked_run.budget_charge_cny is not None:
            return Decimal(locked_run.budget_charge_cny)
        # Reconcile historical key usage only; retired competition balances are
        # neither prerequisites nor debit targets for these records.
        if existing is None:
            locked_run.budget_charge_cny = Decimal(locked_run.budget_charge_cny or 0) + spent
        elif locked_run.budget_charge_cny is None:
            locked_run.budget_charge_cny = existing.amount_cny
        total_charge = Decimal(locked_run.budget_charge_cny or 0)
        locked_run.token_cost_usd = float(total_charge)
        locked_run.token_cost_currency = "CNY"
        locked_run.meter_key_status = "revoked"
        locked_run.settled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        locked_run.settlement_error = None
        self.db.commit()
        return spent

    def record_settlement_error(self, run_id: str, error: Exception) -> None:
        self.db.rollback()
        run = self.db.get(Run, run_id)
        if run is None:
            return
        run.settlement_error = str(error)[:2000]
        self.db.commit()

    def reconcile_terminal_runs(self, *, limit: int = 100) -> tuple[int, int]:
        run_ids = self.db.scalars(
            select(Run.id)
            .where(
                Run.billing_mode == "official_evaluation",
                Run.meter_key_name.is_not(None),
                Run.settled_at.is_(None),
                # A paused run has already stopped its current executor
                # attempt. Revoke and reconcile its historical key too.
                # Future attempts require the user's own API credentials.
                Run.status.in_(["PASSED", "FAILED", "CANCELLED", "PAUSED"]),
            )
            .order_by(Run.finished_at)
            .limit(limit)
        ).all()
        settled = failed = 0
        for run_id in run_ids:
            run = self.db.get(Run, run_id)
            if run is None:
                continue
            try:
                self.settle(run)
                settled += 1
            except Exception as exc:  # noqa: BLE001 - retain for the next reconciliation pass
                self.record_settlement_error(run_id, exc)
                failed += 1
        return settled, failed

    def _request(self, method: str, path: str, **kwargs) -> dict:
        timeout = max(1.0, float(self.settings.meter_usage_timeout_seconds))
        try:
            response = requests.request(
                method,
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {self.settings.meter_admin_token.strip()}"},
                timeout=timeout,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OfficialEvaluationError(f"ONR admin request failed: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise OfficialEvaluationError(str(payload.get("error") if isinstance(payload, dict) else "Invalid ONR response"))
        return payload
