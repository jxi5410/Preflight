"""Tests for handoff generation: file_mapper, HandoffGenerator, schemas, CLI."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from humanqa.core.schemas import (
    AgentPersona,
    CoverageEntry,
    CoverageMap,
    FeatureExpectation,
    FeatureGap,
    Handoff,
    HandoffTask,
    Issue,
    IssueCategory,
    Platform,
    ProductIntentModel,
    RepoInsights,
    RunConfig,
    RunResult,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> RunConfig:
    defaults = {"target_url": "https://example.com"}
    defaults.update(kwargs)
    return RunConfig(**defaults)


def _make_issue(title="Test Issue", severity="medium", category="functional",
                confidence=0.8, agent="tester", **kwargs) -> Issue:
    return Issue(
        title=title,
        severity=Severity(severity),
        category=IssueCategory(category),
        confidence=confidence,
        agent=agent,
        **kwargs,
    )


def _make_run_result(issues=None, run_id="run-test", **kwargs) -> RunResult:
    return RunResult(
        run_id=run_id,
        config=_make_config(),
        intent_model=ProductIntentModel(product_name="TestApp", product_type="saas"),
        issues=issues or [],
        agents=[AgentPersona(name="Tester", role="QA", persona_type="power_user")],
        coverage=CoverageMap(entries=[
            CoverageEntry(url="https://example.com", status="visited"),
            CoverageEntry(url="https://example.com/settings", status="visited"),
            CoverageEntry(url="https://example.com/login", status="failed"),
        ]),
        **kwargs,
    )


def _make_insights(**kwargs) -> RepoInsights:
    defaults = {
        "product_name": "TestApp",
        "tech_stack": ["React", "Next.js", "Tailwind CSS"],
        "routes_or_pages": ["/", "/dashboard", "/settings", "/login", "/profile"],
    }
    defaults.update(kwargs)
    return RepoInsights(**defaults)


# ===========================================================================
# Schema tests
# ===========================================================================

class TestHandoffSchemas:
    """Tests for HandoffTask, FeatureGap, Handoff models."""

    def test_handoff_task_defaults(self):
        task = HandoffTask(issue_id="ISS-001", title="Broken button")
        assert task.severity == Severity.medium
        assert task.category == IssueCategory.functional
        assert task.likely_files == []
        assert task.effort_estimate == ""

    def test_handoff_task_full(self):
        task = HandoffTask(
            issue_id="ISS-002",
            title="Login fails",
            severity=Severity.critical,
            category=IssueCategory.functional,
            likely_files=["/login", "src/components/"],
            repair_brief="Fix the auth handler",
            repro_steps=["Go to /login", "Enter credentials", "Click submit"],
            expected="User is logged in",
            actual="500 error returned",
            effort_estimate="large",
        )
        assert task.severity == Severity.critical
        assert len(task.repro_steps) == 3
        data = task.model_dump()
        assert data["effort_estimate"] == "large"

    def test_feature_gap_defaults(self):
        gap = FeatureGap(feature_name="Dark mode")
        assert gap.source == ""
        assert gap.status == ""
        assert gap.related_issues == []

    def test_feature_gap_serialization(self):
        gap = FeatureGap(
            feature_name="SSO",
            source="README",
            status="missing",
            details="Not found",
            related_issues=["ISS-001"],
        )
        data = json.loads(gap.model_dump_json())
        assert data["feature_name"] == "SSO"
        assert data["related_issues"] == ["ISS-001"]

    def test_handoff_defaults(self):
        h = Handoff()
        assert h.run_id == ""
        assert h.tasks == []
        assert h.feature_gaps == []
        assert h.coverage_summary == {}

    def test_handoff_roundtrip(self):
        h = Handoff(
            run_id="run-abc",
            product_name="TestApp",
            target_url="https://example.com",
            summary="Test summary",
            tasks=[HandoffTask(issue_id="ISS-001", title="Bug")],
            feature_gaps=[FeatureGap(feature_name="Search", status="missing")],
            coverage_summary={"visited": 5, "failed": 1},
        )
        json_str = h.model_dump_json()
        h2 = Handoff.model_validate_json(json_str)
        assert h2.run_id == "run-abc"
        assert len(h2.tasks) == 1
        assert len(h2.feature_gaps) == 1
        assert h2.coverage_summary["visited"] == 5


# ===========================================================================
# FileMapper tests
# ===========================================================================

class TestFileMapper:
    """Tests for core/file_mapper.py."""

    def test_no_insights_returns_empty(self):
        from humanqa.core.file_mapper import map_issue_to_files

        issue = _make_issue("Broken settings", likely_product_area="settings")
        result = map_issue_to_files(issue, None)
        assert result == []

    def test_product_area_matches_route(self):
        from humanqa.core.file_mapper import map_issue_to_files

        issue = _make_issue("Settings broken", likely_product_area="settings")
        insights = _make_insights()
        result = map_issue_to_files(issue, insights)
        assert "/settings" in result

    def test_title_matches_route(self):
        from humanqa.core.file_mapper import map_issue_to_files

        issue = _make_issue("Dashboard loading slow", likely_product_area="")
        insights = _make_insights()
        result = map_issue_to_files(issue, insights)
        assert "/dashboard" in result

    def test_repro_step_url_matches(self):
        from humanqa.core.file_mapper import map_issue_to_files

        issue = _make_issue(
            "Error on profile",
            likely_product_area="",
            repro_steps=["Navigate to /profile", "Click edit"],
        )
        insights = _make_insights()
        result = map_issue_to_files(issue, insights)
        assert "/profile" in result

    def test_category_accessibility_hints(self):
        from humanqa.core.file_mapper import map_issue_to_files

        issue = _make_issue(
            "Missing alt text",
            category="accessibility",
            likely_product_area="",
        )
        insights = _make_insights()
        result = map_issue_to_files(issue, insights)
        assert any("component" in f.lower() or "ARIA" in f for f in result)

    def test_deduplication(self):
        from humanqa.core.file_mapper import map_issue_to_files

        issue = _make_issue(
            "Settings page broken on settings view",
            likely_product_area="settings",
            repro_steps=["Go to /settings"],
        )
        insights = _make_insights()
        result = map_issue_to_files(issue, insights)
        # /settings should appear only once
        assert result.count("/settings") == 1

    def test_caps_at_ten(self):
        from humanqa.core.file_mapper import map_issue_to_files

        # Create insights with many routes
        routes = [f"/page-{i}" for i in range(20)]
        insights = _make_insights(routes_or_pages=routes)
        # Issue whose title matches many routes
        issue = _make_issue(
            " ".join(f"page-{i}" for i in range(20)),
            likely_product_area="page",
        )
        result = map_issue_to_files(issue, insights)
        assert len(result) <= 10


# ===========================================================================
# HandoffGenerator tests
# ===========================================================================

class TestHandoffGenerator:
    """Tests for reporting/handoff.py."""

    def test_generate_basic(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Critical bug", severity="critical"),
            _make_issue("Minor thing", severity="low"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_basic")
        handoff = gen.generate(result)
        assert handoff.run_id == "run-test"
        assert handoff.product_name == "TestApp"
        assert len(handoff.tasks) == 2  # info skipped, critical + low remain

    def test_info_issues_skipped(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("FYI note", severity="info"),
            _make_issue("Real bug", severity="high"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_skip")
        handoff = gen.generate(result)
        assert len(handoff.tasks) == 1
        assert handoff.tasks[0].title == "Real bug"

    def test_tasks_sorted_by_severity(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Low issue", severity="low"),
            _make_issue("Critical issue", severity="critical"),
            _make_issue("High issue", severity="high"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_sort")
        handoff = gen.generate(result)
        severities = [t.severity.value for t in handoff.tasks]
        assert severities == ["critical", "high", "low"]

    def test_effort_estimate_mapping(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Crit", severity="critical"),
            _make_issue("Hi", severity="high"),
            _make_issue("Med", severity="medium"),
            _make_issue("Lo", severity="low"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_effort")
        handoff = gen.generate(result)
        efforts = {t.title: t.effort_estimate for t in handoff.tasks}
        assert efforts["Crit"] == "large"
        assert efforts["Hi"] == "medium"
        assert efforts["Med"] == "medium"
        assert efforts["Lo"] == "small"

    def test_feature_gaps_from_expectations(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Search is broken", severity="high",
                        likely_product_area="search"),
        ])
        result.intent_model.feature_expectations = [
            FeatureExpectation(feature_name="Search", source="README", verified=False),
            FeatureExpectation(feature_name="Export", source="docs", verified=True),
            FeatureExpectation(feature_name="SSO", source="README", verified=None),
        ]
        gen = HandoffGenerator("/tmp/test_handoff_gaps")
        handoff = gen.generate(result)
        # Export is verified=True, should be excluded
        assert len(handoff.feature_gaps) == 2
        names = {g.feature_name for g in handoff.feature_gaps}
        assert "Search" in names
        assert "SSO" in names
        assert "Export" not in names

    def test_feature_gap_status_broken_vs_missing(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Search fails", severity="high",
                        likely_product_area="search"),
        ])
        result.intent_model.feature_expectations = [
            FeatureExpectation(feature_name="Search", source="README", verified=False),
            FeatureExpectation(feature_name="Notifications", source="docs", verified=False),
        ]
        gen = HandoffGenerator("/tmp/test_handoff_status")
        handoff = gen.generate(result)
        gap_map = {g.feature_name: g for g in handoff.feature_gaps}
        # Search has related issues and verified=False -> broken
        assert gap_map["Search"].status == "broken"
        # Notifications has no related issues and verified=False -> missing
        assert gap_map["Notifications"].status == "missing"

    def test_coverage_summary(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result()
        gen = HandoffGenerator("/tmp/test_handoff_cov")
        handoff = gen.generate(result)
        cov = handoff.coverage_summary
        assert cov["visited"] == 2
        assert cov["failed"] == 1
        assert cov["pending"] == 0
        assert len(cov["visited_urls"]) == 2

    def test_generate_all_writes_files(self, tmp_path):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Bug A", severity="high", repair_brief="Fix A"),
            _make_issue("Bug B", severity="medium"),
        ])
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result)

        assert "handoff_md" in paths
        assert "handoff_json" in paths

        md_path = Path(paths["handoff_md"])
        assert md_path.exists()
        md_content = md_path.read_text()
        assert "# Developer Handoff" in md_content
        assert "Bug A" in md_content
        assert "Bug B" in md_content
        assert "run-test" in md_content

        json_path = Path(paths["handoff_json"])
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["run_id"] == "run-test"
        assert len(data["tasks"]) == 2

    def test_markdown_task_table(self, tmp_path):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Login broken", severity="critical",
                        likely_product_area="login"),
        ])
        insights = _make_insights()
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result, insights)

        md_content = Path(paths["handoff_md"]).read_text()
        assert "| # | Severity |" in md_content
        assert "critical" in md_content
        assert "/login" in md_content

    def test_markdown_feature_gaps_table(self, tmp_path):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result()
        result.intent_model.feature_expectations = [
            FeatureExpectation(feature_name="Dark mode", source="README", verified=None),
        ]
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result)

        md_content = Path(paths["handoff_md"]).read_text()
        assert "## Feature Gaps" in md_content
        assert "Dark mode" in md_content

    def test_summary_text(self):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("A", severity="critical"),
            _make_issue("B", severity="high"),
            _make_issue("C", severity="low"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_summary")
        handoff = gen.generate(result)
        assert "3 issues" in handoff.summary
        assert "2 critical/high" in handoff.summary
        assert "3 actionable tasks" in handoff.summary

    def test_with_repo_insights_file_mapping(self, tmp_path):
        from humanqa.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Settings page crash", severity="high",
                        likely_product_area="settings"),
        ])
        insights = _make_insights()
        gen = HandoffGenerator(str(tmp_path))
        handoff = gen.generate(result, insights)
        assert len(handoff.tasks) == 1
        assert "/settings" in handoff.tasks[0].likely_files


# ===========================================================================
# CLI handoff command tests
# ===========================================================================

class TestHandoffCLI:
    """Tests for the CLI handoff command."""

    def test_handoff_command_exists(self):
        """Verify the handoff command is registered."""
        from humanqa.cli import main
        commands = main.commands
        assert "handoff" in commands

    def test_handoff_command_with_run_dir(self, tmp_path):
        """Test handoff command with a valid run directory."""
        from click.testing import CliRunner
        from humanqa.cli import main

        # Create a minimal report.json
        result = _make_run_result(issues=[
            _make_issue("Test bug", severity="high"),
        ])
        report_json = tmp_path / "report.json"
        report_json.write_text(result.model_dump_json(indent=2))

        runner = CliRunner()
        res = runner.invoke(main, ["handoff", str(tmp_path)])
        assert res.exit_code == 0
        assert "Handoff generated" in res.output

        # Check files were created
        assert (tmp_path / "HANDOFF.md").exists()
        assert (tmp_path / "handoff.json").exists()

    def test_handoff_command_missing_run_dir(self):
        """Test handoff command with nonexistent directory."""
        from click.testing import CliRunner
        from humanqa.cli import main

        runner = CliRunner()
        res = runner.invoke(main, ["handoff", "/nonexistent/path"])
        assert res.exit_code != 0

    def test_run_command_shows_handoff_in_output(self):
        """Verify the run command output mentions handoff files."""
        from humanqa.cli import main
        # Check that the CLI source references handoff in output
        import inspect
        source = inspect.getsource(main.commands["run"].callback)
        assert "HANDOFF.md" in source
        assert "handoff.json" in source
