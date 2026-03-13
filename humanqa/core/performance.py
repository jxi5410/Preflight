"""Performance Budget Engine for HumanQA.

Defines performance budgets by product type and evaluates collected metrics
against those budgets. Reports pass/fail/warn for each metric.
"""

from __future__ import annotations

import logging
from typing import Any

from humanqa.core.schemas import Issue, IssueCategory, PageSnapshot, Platform, Severity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Performance budgets by product type
# ---------------------------------------------------------------------------

# Budget thresholds: (warn, fail) — warn means "approaching limit", fail means "over budget"
BUDGETS: dict[str, dict[str, tuple[float, float]]] = {
    "marketing_site": {
        "lcp_ms": (2500, 4000),
        "cls_score": (0.1, 0.25),
        "load_time_ms": (3000, 6000),
        "network_error_count": (1, 5),
    },
    "saas_app": {
        "lcp_ms": (4000, 6000),
        "cls_score": (0.1, 0.25),
        "load_time_ms": (4000, 8000),
        "network_error_count": (2, 10),
    },
    "mobile_web": {
        "lcp_ms": (3000, 5000),
        "cls_score": (0.1, 0.25),
        "load_time_ms": (3500, 7000),
        "network_error_count": (1, 5),
    },
    "default": {
        "lcp_ms": (3000, 5000),
        "cls_score": (0.1, 0.25),
        "load_time_ms": (4000, 8000),
        "network_error_count": (2, 10),
    },
}

# Human-readable metric labels
METRIC_LABELS: dict[str, str] = {
    "lcp_ms": "Largest Contentful Paint",
    "cls_score": "Cumulative Layout Shift",
    "load_time_ms": "Page Load Time",
    "network_error_count": "Network Errors",
}

METRIC_UNITS: dict[str, str] = {
    "lcp_ms": "ms",
    "cls_score": "",
    "load_time_ms": "ms",
    "network_error_count": "",
}


def classify_product_type(product_type: str) -> str:
    """Map a product type string to a budget category."""
    pt = product_type.lower()
    if any(kw in pt for kw in ("marketing", "landing", "brochure", "portfolio")):
        return "marketing_site"
    # Check mobile before saas — "mobile web app" should be mobile, not saas
    if any(kw in pt for kw in ("mobile",)):
        return "mobile_web"
    if any(kw in pt for kw in ("saas", "dashboard", "app", "platform", "tool")):
        return "saas_app"
    return "default"


def get_budget(product_type: str) -> dict[str, tuple[float, float]]:
    """Get the performance budget for a product type."""
    category = classify_product_type(product_type)
    return BUDGETS.get(category, BUDGETS["default"])


class PerformanceResult:
    """Result of evaluating a single metric against its budget."""

    def __init__(
        self,
        metric: str,
        value: float,
        warn_threshold: float,
        fail_threshold: float,
    ):
        self.metric = metric
        self.value = value
        self.warn_threshold = warn_threshold
        self.fail_threshold = fail_threshold

        if value > fail_threshold:
            self.status = "fail"
        elif value > warn_threshold:
            self.status = "warn"
        else:
            self.status = "pass"

    @property
    def label(self) -> str:
        return METRIC_LABELS.get(self.metric, self.metric)

    @property
    def unit(self) -> str:
        return METRIC_UNITS.get(self.metric, "")

    def __repr__(self) -> str:
        return f"PerformanceResult({self.metric}={self.value}{self.unit} [{self.status}])"


def evaluate_snapshot_performance(
    snapshot: PageSnapshot,
    product_type: str = "default",
) -> list[PerformanceResult]:
    """Evaluate a PageSnapshot's metrics against the budget for the product type."""
    budget = get_budget(product_type)
    results: list[PerformanceResult] = []

    metric_values: dict[str, float | None] = {
        "lcp_ms": snapshot.lcp_ms,
        "cls_score": snapshot.cls_score,
        "load_time_ms": float(snapshot.load_time_ms) if snapshot.load_time_ms else None,
        "network_error_count": float(snapshot.network_error_count) if snapshot.network_error_count else None,
    }

    for metric, (warn, fail) in budget.items():
        value = metric_values.get(metric)
        if value is not None:
            results.append(PerformanceResult(metric, value, warn, fail))

    return results


def performance_results_to_issues(
    results: list[PerformanceResult],
    snapshot: PageSnapshot,
    agent_id: str = "performance_monitor",
) -> list[Issue]:
    """Convert performance budget violations into Issue objects."""
    issues: list[Issue] = []

    for r in results:
        if r.status == "pass":
            continue

        severity = Severity.high if r.status == "fail" else Severity.medium
        value_str = f"{r.value:.0f}{r.unit}" if r.unit else f"{r.value:.3f}"
        warn_str = f"{r.warn_threshold:.0f}{r.unit}" if r.unit else f"{r.warn_threshold:.3f}"
        fail_str = f"{r.fail_threshold:.0f}{r.unit}" if r.unit else f"{r.fail_threshold:.3f}"

        issues.append(Issue(
            title=f"Performance budget {'exceeded' if r.status == 'fail' else 'warning'}: {r.label}",
            severity=severity,
            confidence=1.0,  # Measured, not inferred
            platform=Platform.web,
            category=IssueCategory.performance,
            agent=agent_id,
            user_impact=(
                f"{r.label} is {value_str}, which {'exceeds' if r.status == 'fail' else 'approaches'} "
                f"the budget (warn: {warn_str}, fail: {fail_str}). "
                "Users will experience degraded performance."
            ),
            observed_facts=[
                f"{r.label} measured at {value_str}",
                f"Budget warn threshold: {warn_str}",
                f"Budget fail threshold: {fail_str}",
                f"Page URL: {snapshot.url}",
            ],
            inferred_judgment=f"{r.label} is {'over' if r.status == 'fail' else 'approaching'} budget",
            likely_product_area="Performance",
            repair_brief=f"Optimize {r.label.lower()} — currently {value_str}, target < {warn_str}",
        ))

    return issues


def summarize_performance(
    results: list[PerformanceResult],
) -> dict[str, Any]:
    """Create a summary dict of performance results for inclusion in run scores."""
    summary: dict[str, Any] = {}
    for r in results:
        summary[f"perf_{r.metric}_value"] = r.value
        summary[f"perf_{r.metric}_status"] = r.status
    summary["perf_pass_count"] = sum(1 for r in results if r.status == "pass")
    summary["perf_warn_count"] = sum(1 for r in results if r.status == "warn")
    summary["perf_fail_count"] = sum(1 for r in results if r.status == "fail")
    return summary
