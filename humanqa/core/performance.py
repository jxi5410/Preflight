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


PRODUCT_TYPE_ALIASES: dict[str, str] = {
    "marketing_site": "marketing_site",
    "marketing": "marketing_site",
    "landing": "marketing_site",
    "landing_page": "marketing_site",
    "brochure": "marketing_site",
    "portfolio": "marketing_site",
    "saas_app": "saas_app",
    "saas": "saas_app",
    "dashboard": "saas_app",
    "platform": "saas_app",
    "tool": "saas_app",
    "mobile_web": "mobile_web",
    "mobile": "mobile_web",
    "ecommerce": "ecommerce",
    "e-commerce": "ecommerce",
    "marketplace": "ecommerce",
    "store": "ecommerce",
    "content": "content_site",
    "blog": "content_site",
    "news": "content_site",
    "documentation": "content_site",
    "docs": "content_site",
    "wiki": "content_site",
}

# Ecommerce and content site budgets
BUDGETS["ecommerce"] = {
    "lcp_ms": (2500, 4000),
    "cls_score": (0.05, 0.15),  # Stricter CLS — layout shift hurts conversion
    "load_time_ms": (3000, 5000),
    "network_error_count": (1, 3),
}
BUDGETS["content_site"] = {
    "lcp_ms": (2000, 3500),
    "cls_score": (0.05, 0.1),
    "load_time_ms": (2500, 5000),
    "network_error_count": (1, 5),
}


def classify_product_type(product_type: str) -> str:
    """Map a product type string to a budget category.

    Uses a keyword matching approach with explicit alias table for
    product-type-aware performance budgets.
    """
    pt = product_type.lower().strip()

    # Direct alias match
    for keyword, category in PRODUCT_TYPE_ALIASES.items():
        if keyword in pt:
            return category

    # Fallback keyword detection
    if any(kw in pt for kw in ("marketing", "landing", "brochure", "portfolio")):
        return "marketing_site"
    if any(kw in pt for kw in ("mobile",)):
        return "mobile_web"
    if any(kw in pt for kw in ("saas", "dashboard", "app", "platform", "tool")):
        return "saas_app"
    if any(kw in pt for kw in ("ecommerce", "e-commerce", "store", "marketplace")):
        return "ecommerce"
    if any(kw in pt for kw in ("content", "blog", "docs", "documentation", "news", "wiki")):
        return "content_site"
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


def score_explanation(product_type: str) -> str:
    """Return a human-readable explanation of why a budget category was chosen."""
    category = classify_product_type(product_type)
    budget = BUDGETS.get(category, BUDGETS["default"])

    explanations = {
        "marketing_site": "Marketing/landing pages are expected to load fast for first impressions. Budgets are tight on LCP and load time.",
        "saas_app": "SaaS applications trade initial load speed for interactivity. Budgets are more lenient on LCP but strict on CLS.",
        "mobile_web": "Mobile web budgets account for slower networks and smaller devices. CLS is strict for touch interfaces.",
        "ecommerce": "E-commerce sites need fast loads and zero layout shift to avoid hurting conversion rates. CLS budget is the strictest.",
        "content_site": "Content/documentation sites prioritize fast text rendering. LCP budget is the tightest across categories.",
        "default": "Using default performance budgets — product type was not confidently classified.",
    }

    explanation = explanations.get(category, explanations["default"])
    budget_lines = []
    for metric, (warn, fail) in budget.items():
        label = METRIC_LABELS.get(metric, metric)
        unit = METRIC_UNITS.get(metric, "")
        warn_str = f"{warn:.0f}{unit}" if unit else f"{warn}"
        fail_str = f"{fail:.0f}{unit}" if unit else f"{fail}"
        budget_lines.append(f"  {label}: warn={warn_str}, fail={fail_str}")

    return f"Category: {category}\n{explanation}\nBudgets:\n" + "\n".join(budget_lines)
