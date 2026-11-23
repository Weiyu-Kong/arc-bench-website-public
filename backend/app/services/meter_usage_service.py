from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import time
from urllib.parse import urlparse

import requests

from app.core.config import Settings, get_settings


class MeterUsageError(RuntimeError):
    """Raised when a configured meter cannot provide a trustworthy snapshot."""


@dataclass(frozen=True)
class MeterUsageSnapshot:
    access_key_id: str
    total_tokens: int
    total_cost: Decimal
    currency: str
    captured_at: int
    query_start: int


@dataclass(frozen=True)
class MeterUsageDelta:
    token_count: int
    cost: Decimal
    currency: str


class MeterUsageService:
    """Read token and billing totals for the model key used by one runner.

    The meter user API deliberately accepts an Access Key and exposes only that
    key's account. ARC Bench takes a snapshot immediately before starting the
    runner and another after billing settles, then stores the non-negative
    delta on the Run.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.meter_usage_enabled and self.settings.meter_base_url.strip())

    def capture(
        self,
        api_key: str | None,
        *,
        wait_for_settlement: bool = False,
        query_start: int | None = None,
    ) -> MeterUsageSnapshot | None:
        if not self.enabled:
            return None

        normalized_api_key = str(api_key or "").strip()
        if not normalized_api_key:
            raise MeterUsageError("meter API key is unavailable")
        self._validate_meter_url()
        base_url = self.settings.meter_base_url.rstrip("/")
        timeout = max(1.0, float(self.settings.meter_usage_timeout_seconds))

        with requests.Session() as session:
            try:
                login = session.post(
                    f"{base_url}/api/user/login",
                    json={"access_key": normalized_api_key},
                    timeout=timeout,
                )
                login.raise_for_status()
                login_payload = login.json()
            except (requests.RequestException, ValueError) as exc:
                raise MeterUsageError(f"meter login failed: {exc}") from exc

            account = login_payload.get("account") if isinstance(login_payload, dict) else None
            access_key_id = str(account.get("access_key_id") or "").strip() if isinstance(account, dict) else ""
            if not access_key_id:
                raise MeterUsageError("meter login response did not include an access-key identity")

            if wait_for_settlement:
                self._wait_for_settlement(session, base_url, timeout)

            captured_at = int(time.time())
            # The meter accepts a maximum 31-day window and end timestamps up
            # to five minutes in the future. One extra second avoids dropping
            # an event written in the same whole second as this snapshot.
            end = captured_at + 1
            start = query_start if query_start is not None else end - int(self.settings.meter_usage_window_seconds)
            start = max(1, int(start))
            if start >= end:
                raise MeterUsageError("meter usage window start must be before its end")
            try:
                response = session.get(
                    f"{base_url}/api/user/usage",
                    params={"start": start, "end": end, "timezone": "UTC", "bucket": "week"},
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise MeterUsageError(f"meter usage query failed: {exc}") from exc
            finally:
                # The meter keeps user sessions in memory for twelve hours.
                # Explicit logout avoids accumulating two sessions per run.
                try:
                    session.post(f"{base_url}/api/user/logout", timeout=timeout)
                except requests.RequestException:
                    pass

        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise MeterUsageError("meter usage response was not successful")
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise MeterUsageError("meter usage response did not include rows")

        total_tokens = 0
        total_cost = Decimal("0")
        try:
            for row in rows:
                measures = row.get("measures") if isinstance(row, dict) else None
                if not isinstance(measures, dict):
                    continue
                total_tokens += int(measures.get("quantity") or 0)
                total_cost += Decimal(str(measures.get("amount") or "0"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise MeterUsageError("meter usage response contained invalid totals") from exc

        return MeterUsageSnapshot(
            access_key_id=access_key_id,
            total_tokens=max(0, total_tokens),
            total_cost=max(Decimal("0"), total_cost),
            currency=str(payload.get("currency") or "").strip().upper() or "UNKNOWN",
            captured_at=captured_at,
            query_start=start,
        )

    def _wait_for_settlement(self, session: requests.Session, base_url: str, timeout: float) -> None:
        deadline = time.monotonic() + max(0.0, float(self.settings.meter_usage_settle_timeout_seconds))
        while True:
            try:
                response = session.get(f"{base_url}/api/user/freshness", timeout=timeout)
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise MeterUsageError(f"meter freshness query failed: {exc}") from exc
            pending = payload.get("pending_billing_events") if isinstance(payload, dict) else None
            if isinstance(pending, int) and pending <= 0:
                return
            if time.monotonic() >= deadline:
                raise MeterUsageError("meter billing did not settle before the configured timeout")
            time.sleep(0.5)

    def _validate_meter_url(self) -> None:
        meter = urlparse(self.settings.meter_base_url)
        if not meter.scheme or not meter.netloc:
            raise MeterUsageError("ARCBENCH_METER_BASE_URL must be an absolute URL")

    @staticmethod
    def delta(start: MeterUsageSnapshot | None, end: MeterUsageSnapshot | None) -> MeterUsageDelta | None:
        if start is None or end is None:
            return None
        if start.access_key_id != end.access_key_id:
            raise MeterUsageError("meter access-key identity changed during the run")
        if start.currency != end.currency:
            raise MeterUsageError("meter billing currency changed during the run")
        return MeterUsageDelta(
            token_count=max(0, end.total_tokens - start.total_tokens),
            cost=max(Decimal("0"), end.total_cost - start.total_cost),
            currency=end.currency,
        )
