"""Run Comparison — Diff two Preflight runs to detect regressions and progress.

Usage:
    preflight compare ./artifacts/run_20260313 ./artifacts/run_20260314

Outputs:
- New issues (in current, not in baseline)
- Resolved issues (in baseline, not in current)
- Regressed issues (severity increased)
- Persistent issues (still present)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from preflight.core.schemas import Issue, RunResult, Severity

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


class ComparisonResult:
    """Result of comparing two runs."""

    def __init__(self):
        self.new_issues: list[Issue] = []
        self.resolved_issues: list[Issue] = []
        self.regressed_issues: list[tuple[Issue, Issue]] = []  # (baseline, current)
        self.persistent_issues: list[tuple[Issue, Issue]] = []  # (baseline, current)
        self.baseline_run_id: str = ""
        self.current_run_id: str = ""

    @property
    def summary(self) -> str:
        parts = [
            f"New: {len(self.new_issues)}",
            f"Resolved: {len(self.resolved_issues)}",
            f"Regressed: {len(self.regressed_issues)}",
            f"Persistent: {len(self.persistent_issues)}",
        ]
        return " | ".join(parts)

    def to_markdown(self) -> str:
        """Generate a markdown comparison report."""
        lines: list[str] = []
        lines.append("# Preflight Run Comparison")
        lines.append("")
        lines.append(f"**Baseline:** {self.baseline_run_id}")
        lines.append(f"**Current:** {self.current_run_id}")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"| Category | Count |")
        lines.append(f"|----------|-------|")
        lines.append(f"| New issues | {len(self.new_issues)} |")
        lines.append(f"| Resolved issues | {len(self.resolved_issues)} |")
        lines.append(f"| Regressed issues | {len(self.regressed_issues)} |")
        lines.append(f"| Persistent issues | {len(self.persistent_issues)} |")
        lines.append("")

        # New issues
        if self.new_issues:
            lines.append("## New Issues")
            lines.append("")
            for issue in self.new_issues:
                lines.append(
                    f"- **[{issue.severity.value}]** {issue.title} "
                    f"({issue.category.value})"
                )
            lines.append("")

        # Resolved
        if self.resolved_issues:
            lines.append("## Resolved Issues")
            lines.append("")
            for issue in self.resolved_issues:
                lines.append(
                    f"- ~~[{issue.severity.value}] {issue.title}~~ "
                    f"({issue.category.value})"
                )
            lines.append("")

        # Regressed
        if self.regressed_issues:
            lines.append("## Regressed Issues (severity increased)")
            lines.append("")
            for baseline, current in self.regressed_issues:
                lines.append(
                    f"- **{current.title}**: "
                    f"{baseline.severity.value} -> {current.severity.value}"
                )
            lines.append("")

        # Persistent
        if self.persistent_issues:
            lines.append("## Persistent Issues")
            lines.append("")
            for baseline, current in self.persistent_issues:
                sev_change = ""
                if baseline.severity != current.severity:
                    sev_change = f" (was {baseline.severity.value})"
                lines.append(
                    f"- [{current.severity.value}] {current.title}{sev_change}"
                )
            lines.append("")

        return "\n".join(lines)


def load_run_result(run_dir: str | Path) -> RunResult:
    """Load a RunResult from a directory containing report.json."""
    run_path = Path(run_dir)
    json_path = run_path / "report.json"
    if not json_path.exists():
        raise FileNotFoundError(f"No report.json found in {run_path}")

    data = json.loads(json_path.read_text())
    return RunResult(**data)


def compare_runs(
    baseline: RunResult,
    current: RunResult,
) -> ComparisonResult:
    """Compare two run results and categorize differences."""
    result = ComparisonResult()
    result.baseline_run_id = baseline.run_id
    result.current_run_id = current.run_id

    # Build lookup by normalized title for matching
    baseline_map = _build_issue_map(baseline.issues)
    current_map = _build_issue_map(current.issues)

    baseline_keys = set(baseline_map.keys())
    current_keys = set(current_map.keys())

    # New issues: in current but not baseline
    for key in current_keys - baseline_keys:
        result.new_issues.append(current_map[key])

    # Resolved: in baseline but not current
    for key in baseline_keys - current_keys:
        result.resolved_issues.append(baseline_map[key])

    # Persistent / regressed: in both
    for key in baseline_keys & current_keys:
        b_issue = baseline_map[key]
        c_issue = current_map[key]

        b_sev = SEVERITY_ORDER.get(b_issue.severity.value, 5)
        c_sev = SEVERITY_ORDER.get(c_issue.severity.value, 5)

        if c_sev < b_sev:
            # Lower number = higher severity = regression
            result.regressed_issues.append((b_issue, c_issue))
        else:
            result.persistent_issues.append((b_issue, c_issue))

    return result


def _build_issue_map(issues: list[Issue]) -> dict[str, Issue]:
    """Build a lookup map from normalized title to issue.

    Uses title + category as key to avoid false matches across categories.
    """
    result: dict[str, Issue] = {}
    for issue in issues:
        key = f"{issue.title.lower().strip()}|{issue.category.value}"
        # Keep highest confidence if duplicates exist
        if key not in result or issue.confidence > result[key].confidence:
            result[key] = issue
    return result
