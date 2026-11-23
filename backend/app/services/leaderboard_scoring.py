"""Pure scoring primitives for competition leaderboards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite


@dataclass(frozen=True)
class ScoringConfig:
    alpha: float = 0.1
    beta: float = 0.2
    cost_per_test: float = 0.4
    cost_floor: float = 0.1
    ratio_min: float = 0.01
    ratio_max: float = 100.0
    currency: str = "CNY"


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float | None
    pass_rate: float
    cost: float | None
    effective_cost: float | None
    expected_cost: float | None
    ratio: float | None
    regime: str
    test_total: int
    passed: int


def compute_score(
    *,
    passed: int,
    test_total: int,
    cost: float | None,
    currency: str | None,
    config: ScoringConfig,
) -> ScoreBreakdown:
    total = max(0, int(test_total))
    passed_count = min(max(0, int(passed)), total) if total else 0
    pass_rate = 100.0 * passed_count / total if total else 0.0
    normalized_currency = str(currency or "").strip().upper()
    valid_cost = (isinstance(cost, (int, float)) and not isinstance(cost, bool)
                  and isfinite(float(cost)) and float(cost) >= 0
                  and normalized_currency == config.currency.upper())
    # Unknown/invalid costs are +infinity for the very same bounded formula.
    # Keep API-facing values nullable: JSON does not support Infinity/NaN.
    raw_cost = float(cost) if valid_cost else float("inf")
    displayed_cost = raw_cost if valid_cost else None
    effective_cost = max(raw_cost, config.cost_floor)
    displayed_effective_cost = effective_cost if valid_cost else None
    if pass_rate == 0 or total == 0:
        return ScoreBreakdown(0.0, pass_rate, displayed_cost, displayed_effective_cost, 0.0, None,
                              "zero" if valid_cost else "unavailable", total, passed_count)

    expected_cost = config.cost_per_test * total * pass_rate / 100.0
    if not isfinite(expected_cost) or expected_cost <= 0:
        return ScoreBreakdown(pass_rate * config.ratio_max ** (-config.beta), pass_rate,
                              None, None, None, config.ratio_max, "unavailable", total, passed_count)
    ratio = min(config.ratio_max, max(config.ratio_min, effective_cost / expected_cost))
    exponent = config.alpha if ratio <= 1 else config.beta
    regime = ("reward" if ratio <= 1 else "penalty") if valid_cost else "unavailable"
    score = pass_rate * ratio ** (-exponent)
    return ScoreBreakdown(score, pass_rate, displayed_cost, displayed_effective_cost, expected_cost, ratio, regime, total, passed_count)


def rank_key(
    score: float | None,
    pass_rate: float,
    cost: float | None,
    submitted_at: datetime,
) -> tuple[int, float, float, float, datetime]:
    """Lower keys rank first; unavailable scores always rank last."""
    unavailable = score is None
    return (
        1 if unavailable else 0,
        -(score or 0.0),
        -float(pass_rate),
        float(cost) if cost is not None else float("inf"),
        submitted_at,
    )


def funding_source_for_runs(runs) -> str | None:
    modes = {getattr(run, "billing_mode", None) for run in runs}
    if not modes or not modes <= {"self_funded", "official_evaluation"}:
        return None
    return next(iter(modes)) if len(modes) == 1 else "mixed"


def scoring_cost_for_run(run, currency: str = "CNY") -> float | None:
    personal = getattr(run, "billing_mode", None) == "self_funded"
    if getattr(run, "billing_mode", None) == "official_evaluation" and getattr(run, "settled_at", None) is None:
        return None
    # Partial recorded costs are usable under the recorded-only billing policy.
    # cost_complete is retained for diagnostics, not as a scoring gate.
    if str(run.token_cost_currency or "").upper() != currency.upper():
        return 0.0 if personal else None
    cost = sum_scoring_costs([run.token_cost_usd])
    return 0.0 if personal and cost is None else cost


def sum_scoring_costs(costs) -> float | None:
    costs = list(costs)
    if any(not isinstance(cost, (int, float)) or isinstance(cost, bool)
           or not isfinite(cost) or cost < 0 for cost in costs):
        return None
    total = sum(float(cost) for cost in costs)
    return total if isfinite(total) else None


def aggregate_run_cost(runs, currency: str = "CNY") -> float | None:
    return sum_scoring_costs(scoring_cost_for_run(run, currency) for run in runs)
