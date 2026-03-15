"""Handoff Generator — produces HANDOFF.md and handoff.json for AI coding tools.

Generates output designed to be consumed directly by AI coding tools
(Claude Code, Codex, Cursor) with zero reformatting.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from preflight.core.file_mapper import FileMapper
from preflight.core.schemas import (
    FeatureGap,
    FixOption,
    Handoff,
    HandoffTask,
    RepoInsights,
    RunResult,
    Severity,
)

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

COMPLEXITY_MAP = {
    "critical": "significant",
    "high": "moderate",
    "medium": "moderate",
    "low": "quick_fix",
    "info": "quick_fix",
}

# Rough hour estimates per complexity
HOURS_MAP = {
    "quick_fix": 0.5,
    "moderate": 1.5,
    "significant": 3.0,
}


class HandoffGenerator:
    """Generates developer handoff documents from a run result."""

    def __init__(self, output_dir: str = "./artifacts"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        result: RunResult,
        repo_insights: RepoInsights | None = None,
    ) -> Handoff:
        """Build a Handoff from the run result."""
        mapper = FileMapper(repo_insights)
        tasks = self._build_tasks(result, mapper)
        self._infer_dependencies(tasks, result)
        feature_gaps = self._build_feature_gaps(result)

        severity_counts: dict[str, int] = {}
        for issue in result.issues:
            sev = issue.severity.value
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        total_hours = sum(HOURS_MAP.get(t.estimated_complexity, 1.5) for t in tasks)
        hours_str = f"~{total_hours:.0f}-{total_hours * 1.3:.0f} hours" if tasks else "0"

        critical_high = severity_counts.get("critical", 0) + severity_counts.get("high", 0)
        summary = (
            f"{len(result.issues)} issues found: "
            f"{severity_counts.get('critical', 0)} critical, "
            f"{severity_counts.get('high', 0)} high, "
            f"{severity_counts.get('medium', 0)} medium, "
            f"{severity_counts.get('low', 0)} low"
        )

        tech_stack = repo_insights.tech_stack if repo_insights else []
        repo_url = result.config.repo_url

        return Handoff(
            run_id=result.run_id,
            product_name=result.intent_model.product_name,
            repo_url=repo_url,
            tech_stack=tech_stack,
            target_url=result.config.target_url,
            tasks=tasks,
            feature_gaps=feature_gaps,
            total_estimated_hours=hours_str,
            summary=summary,
        )

    def generate_all(
        self,
        result: RunResult,
        repo_insights: RepoInsights | None = None,
        handoff_format: str = "generic",
    ) -> dict[str, str]:
        """Generate HANDOFF.md and handoff.json. Returns dict of format -> path."""
        handoff = self.generate(result, repo_insights)
        paths: dict[str, str] = {}
        paths["handoff_md"] = self._write_markdown(handoff, handoff_format)
        paths["handoff_json"] = self._write_json(handoff)
        return paths

    def _build_tasks(
        self, result: RunResult, mapper: FileMapper
    ) -> list[HandoffTask]:
        """Convert issues into actionable HandoffTasks."""
        # Sort issues by severity first
        sorted_issues = sorted(
            result.issues,
            key=lambda i: (SEVERITY_ORDER.get(i.severity.value, 5), -i.confidence),
        )

        tasks: list[HandoffTask] = []
        task_num = 0
        for issue in sorted_issues:
            if issue.severity == Severity.info:
                continue

            task_num += 1
            likely_files = mapper.map_issue_to_files(issue)
            complexity = COMPLEXITY_MAP.get(issue.severity.value, "moderate")

            # Build description from user_impact + observed facts
            desc_parts = []
            if issue.user_impact:
                desc_parts.append(issue.user_impact)
            elif issue.actual:
                desc_parts.append(issue.actual)
            description = " ".join(desc_parts) or issue.title

            # Build verification suggestion
            verification = ""
            if issue.repro_steps:
                verification = (
                    f"After fixing, repeat the repro steps and confirm the expected "
                    f"behavior. Write an e2e test covering "
                    f"'{issue.likely_product_area or 'the affected area'}'."
                )

            # Generate fix options based on severity and category
            fix_options = self._generate_fix_options(issue, complexity)

            task = HandoffTask(
                task_number=task_num,
                issue_id=issue.id,
                severity=issue.severity.value,
                title=issue.title,
                description=description,
                likely_files=likely_files,
                repro_steps=issue.repro_steps,
                expected_behavior=issue.expected,
                fix_guidance=issue.repair_brief,
                fix_options=fix_options,
                verification=verification,
                evidence_screenshots=issue.evidence.screenshots,
                estimated_complexity=complexity,
            )
            tasks.append(task)

        return tasks

    @staticmethod
    def _generate_fix_options(issue, complexity: str) -> list[FixOption]:
        """Generate fix options based on issue characteristics."""
        options: list[FixOption] = []
        cat = issue.category.value

        if complexity == "significant":
            options.append(FixOption(
                approach="Quick patch",
                description=f"Address the immediate symptom: {issue.repair_brief or issue.title}",
                trade_offs="Fast to ship but may not address root cause. Good for urgent customer-facing issues.",
                estimated_effort="quick_fix",
            ))
            options.append(FixOption(
                approach="Proper fix",
                description=f"Fix the underlying issue in {issue.likely_product_area or 'the affected area'}",
                trade_offs="Takes longer but prevents recurrence. Recommended for production stability.",
                estimated_effort="significant",
            ))
        elif complexity == "moderate":
            options.append(FixOption(
                approach="Direct fix",
                description=issue.repair_brief or f"Fix {issue.title}",
                trade_offs="Standard fix — moderate effort with good coverage.",
                estimated_effort="moderate",
            ))
        else:
            options.append(FixOption(
                approach="Quick fix",
                description=issue.repair_brief or f"Fix {issue.title}",
                trade_offs="Straightforward change, low risk.",
                estimated_effort="quick_fix",
            ))

        # Category-specific options
        if cat == "accessibility":
            options.append(FixOption(
                approach="ARIA/semantic HTML update",
                description="Add appropriate ARIA attributes or switch to semantic HTML elements",
                trade_offs="Usually low-risk and improves screen reader compatibility.",
                estimated_effort="quick_fix",
            ))
        elif cat == "performance":
            options.append(FixOption(
                approach="Optimization pass",
                description="Profile and optimize the slow path (lazy loading, code splitting, caching)",
                trade_offs="May require architecture changes if the bottleneck is structural.",
                estimated_effort="moderate",
            ))

        return options

    def _infer_dependencies(
        self, tasks: list[HandoffTask], result: RunResult
    ) -> None:
        """Infer task dependencies from product area overlap and repro steps.

        If task B's repro steps reference a page/flow that task A is about,
        then B depends on A.
        """
        # Build a map of product area -> task number
        area_to_task: dict[str, int] = {}
        issue_map: dict[str, object] = {}
        for issue in result.issues:
            issue_map[issue.id] = issue

        for task in tasks:
            issue = issue_map.get(task.issue_id)
            if issue and hasattr(issue, "likely_product_area") and issue.likely_product_area:
                area_to_task[issue.likely_product_area.lower()] = task.task_number

        for task in tasks:
            issue = issue_map.get(task.issue_id)
            if not issue:
                continue
            # Check if repro steps reference another task's product area
            repro_text = " ".join(task.repro_steps).lower()
            for area, dep_task_num in area_to_task.items():
                if dep_task_num == task.task_number:
                    continue
                if area in repro_text:
                    if dep_task_num not in task.depends_on:
                        task.depends_on.append(dep_task_num)
                    # Mark the other task as blocking this one
                    for other in tasks:
                        if other.task_number == dep_task_num:
                            if task.task_number not in other.blocks:
                                other.blocks.append(task.task_number)

    def _build_feature_gaps(self, result: RunResult) -> list[FeatureGap]:
        """Identify gaps between claimed features and observed behavior."""
        gaps: list[FeatureGap] = []
        expectations = result.intent_model.feature_expectations

        for feat in expectations:
            if feat.verified is True:
                continue

            # Determine UI status
            feat_lower = feat.feature_name.lower()
            has_related = any(
                feat_lower in issue.title.lower()
                or feat_lower in issue.likely_product_area.lower()
                for issue in result.issues
            )

            if feat.verified is False:
                ui_status = "different" if has_related else "not_found"
            else:
                ui_status = "partial" if has_related else "not_found"

            gaps.append(FeatureGap(
                feature=feat.feature_name,
                source=feat.source,
                claim=f"{feat.source} claims '{feat.feature_name}'",
                ui_status=ui_status,
            ))

        return gaps

    def _write_markdown(self, handoff: Handoff, fmt: str = "generic") -> str:
        """Write HANDOFF.md in the spec format."""
        lines: list[str] = []

        # Header
        lines.append(f"# Preflight Handoff — {handoff.product_name}")
        lines.append(
            f"Generated: {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" | Run: {handoff.run_id}"
        )
        lines.append("")

        # Context
        lines.append("## Context")
        lines.append(f"Product: {handoff.product_name}")
        if handoff.repo_url:
            lines.append(f"Repo: {handoff.repo_url}")
        if handoff.tech_stack:
            lines.append(f"Tech stack: {', '.join(handoff.tech_stack)}")
        lines.append(f"Target URL: {handoff.target_url}")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append(handoff.summary)
        lines.append(f"Estimated scope: {handoff.total_estimated_hours} of implementation work")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Tasks
        total_tasks = len(handoff.tasks)
        for task in handoff.tasks:
            sev_label = task.severity.upper()
            lines.append(f"## Task {task.task_number} of {total_tasks} — {sev_label}")
            lines.append(f"### {task.title}")

            lines.append(f"**What's wrong:** {task.description}")

            if task.likely_files:
                files_str = ", ".join(f"`{f}`" for f in task.likely_files)
                lines.append(f"**Where to look:** {files_str}")

            if task.repro_steps:
                repro = " → ".join(task.repro_steps)
                lines.append(f"**Repro:** {repro}")

            if task.expected_behavior:
                lines.append(f"**Expected:** {task.expected_behavior}")

            if task.fix_guidance:
                lines.append(f"**Fix guidance:** {task.fix_guidance}")

            if task.fix_options:
                lines.append("**Fix options:**")
                for opt in task.fix_options:
                    lines.append(f"  - *{opt.approach}* ({opt.estimated_effort}): {opt.description}")
                    if opt.trade_offs:
                        lines.append(f"    Trade-offs: {opt.trade_offs}")

            if task.verification:
                lines.append(f"**Verify fix:** {task.verification}")

            if task.evidence_screenshots:
                for s in task.evidence_screenshots:
                    lines.append(f"**Evidence:** {s}")

            if task.depends_on:
                deps = ", ".join(f"Task {d}" for d in task.depends_on)
                lines.append(f"**Depends on:** {deps}")

            lines.append(f"**Complexity:** {task.estimated_complexity}")
            lines.append("")
            lines.append("---")
            lines.append("")

        # Dependency Notes
        dep_notes = self._build_dependency_notes(handoff.tasks)
        if dep_notes:
            lines.append("## Dependency Notes")
            for note in dep_notes:
                lines.append(f"- {note}")
            lines.append("")

        # Feature Gaps
        if handoff.feature_gaps:
            lines.append("## Feature Gaps (Repo claims vs UI reality)")
            lines.append("These features are documented in the repo but not found in the UI:")
            for gap in handoff.feature_gaps:
                claim_str = f" ({gap.claim})" if gap.claim else ""
                lines.append(f"- {gap.feature} [{gap.ui_status}]{claim_str}")
            lines.append("")

        # Verification Checklist
        lines.append("## Verification Checklist")
        rerun_cmd = f"preflight run {handoff.target_url}"
        if handoff.repo_url:
            rerun_cmd += f" --repo {handoff.repo_url}"
        lines.append(f"After all fixes, re-run: `{rerun_cmd}`")
        lines.append("Expected: Critical and high issues should not reappear.")

        md_text = "\n".join(lines)
        md_path = self.output_dir / "HANDOFF.md"
        md_path.write_text(md_text)
        logger.info("HANDOFF.md written to %s", md_path)
        return str(md_path)

    def _write_json(self, handoff: Handoff) -> str:
        """Write handoff.json in the spec format."""
        # Build the spec-compliant JSON structure
        data = {
            "handoff_version": handoff.handoff_version,
            "run_id": handoff.run_id,
            "product": {
                "name": handoff.product_name,
                "repo": handoff.repo_url,
                "tech_stack": handoff.tech_stack,
                "target_url": handoff.target_url,
            },
            "tasks": [
                {
                    "task_number": t.task_number,
                    "issue_id": t.issue_id,
                    "severity": t.severity,
                    "title": t.title,
                    "description": t.description,
                    "likely_files": t.likely_files,
                    "repro_steps": t.repro_steps,
                    "expected_behavior": t.expected_behavior,
                    "fix_guidance": t.fix_guidance,
                    "fix_options": [
                        {
                            "approach": o.approach,
                            "description": o.description,
                            "trade_offs": o.trade_offs,
                            "estimated_effort": o.estimated_effort,
                        }
                        for o in t.fix_options
                    ],
                    "verification": t.verification,
                    "evidence_screenshots": t.evidence_screenshots,
                    "depends_on": t.depends_on,
                    "blocks": t.blocks,
                    "estimated_complexity": t.estimated_complexity,
                }
                for t in handoff.tasks
            ],
            "feature_gaps": [
                {
                    "feature": g.feature,
                    "source": g.source,
                    "claim": g.claim,
                    "ui_status": g.ui_status,
                }
                for g in handoff.feature_gaps
            ],
            "dependency_graph": self._build_dependency_graph(handoff.tasks),
            "total_estimated_hours": handoff.total_estimated_hours,
            "summary": handoff.summary,
        }

        json_path = self.output_dir / "handoff.json"
        json_path.write_text(json.dumps(data, indent=2))
        logger.info("handoff.json written to %s", json_path)
        return str(json_path)

    def _build_dependency_notes(self, tasks: list[HandoffTask]) -> list[str]:
        """Build human-readable dependency notes."""
        notes: list[str] = []
        for task in tasks:
            if task.blocks:
                blocked_str = ", ".join(f"Task {b}" for b in task.blocks)
                notes.append(
                    f"Fix Task {task.task_number} ({task.title}) before "
                    f"{blocked_str} — they depend on it"
                )

        # Find independent tasks that can be parallelized
        independent = [
            t for t in tasks if not t.depends_on and not t.blocks
        ]
        if len(independent) > 1:
            nums = ", ".join(str(t.task_number) for t in independent)
            notes.append(f"Tasks {nums} are independent and can be done in parallel")

        return notes

    def _build_dependency_graph(self, tasks: list[HandoffTask]) -> dict:
        """Build the dependency_graph section for JSON output."""
        graph: dict[str, dict] = {}
        for task in tasks:
            entry: dict[str, list[int]] = {}
            if task.blocks:
                entry["blocks"] = task.blocks
            if task.depends_on:
                entry["depends_on"] = task.depends_on
            if entry:
                graph[str(task.task_number)] = entry

        # Add parallel_with for independent tasks
        independent = [
            t for t in tasks if not t.depends_on and not t.blocks
        ]
        if len(independent) > 1:
            for t in independent:
                others = [o.task_number for o in independent if o.task_number != t.task_number]
                key = str(t.task_number)
                if key not in graph:
                    graph[key] = {}
                graph[key]["parallel_with"] = others

        return graph
