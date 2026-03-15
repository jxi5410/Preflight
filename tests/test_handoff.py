"""Tests for handoff feature: schemas, FileMapper, HandoffGenerator, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from preflight.core.schemas import (
    AgentPersona,
    CoverageEntry,
    CoverageMap,
    Evidence,
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
    """Tests for HandoffTask, FeatureGap, Handoff models per HANDOFF_SPEC."""

    def test_handoff_task_spec_fields(self):
        task = HandoffTask(
            task_number=1,
            issue_id="ISS-A1B2C3",
            severity="critical",
            title="Checkout form silently fails",
            description="No error message shown",
            likely_files=["app/checkout/page.tsx"],
            repro_steps=["Navigate to /checkout", "Click Pay"],
            expected_behavior="Clear error message",
            fix_guidance="Add client-side validation",
            verification="Fill with invalid card, assert error visible",
            evidence_screenshots=["screenshots/step-7.png"],
            depends_on=[],
            blocks=[5],
            estimated_complexity="significant",
        )
        assert task.task_number == 1
        assert task.severity == "critical"
        assert task.blocks == [5]
        assert task.estimated_complexity == "significant"

    def test_handoff_task_defaults(self):
        task = HandoffTask(task_number=1, issue_id="ISS-001", severity="medium", title="Bug")
        assert task.description == ""
        assert task.likely_files == []
        assert task.depends_on == []
        assert task.blocks == []
        assert task.estimated_complexity == "moderate"

    def test_feature_gap_spec_fields(self):
        gap = FeatureGap(
            feature="Dark mode toggle",
            source="README",
            claim="supports dark/light theme",
            ui_status="not_found",
        )
        assert gap.feature == "Dark mode toggle"
        assert gap.ui_status == "not_found"

    def test_feature_gap_defaults(self):
        gap = FeatureGap(feature="SSO", source="docs")
        assert gap.claim == ""
        assert gap.ui_status == "not_found"

    def test_handoff_spec_fields(self):
        h = Handoff(
            run_id="run-abc123",
            product_name="TestApp",
            repo_url="https://github.com/user/repo",
            tech_stack=["Next.js", "TypeScript"],
            target_url="https://product.com",
            total_estimated_hours="~3-4 hours",
            summary="15 issues found",
        )
        assert h.handoff_version == "1.0"
        assert h.repo_url == "https://github.com/user/repo"
        assert h.tech_stack == ["Next.js", "TypeScript"]
        assert h.total_estimated_hours == "~3-4 hours"

    def test_handoff_roundtrip(self):
        h = Handoff(
            run_id="run-abc",
            product_name="App",
            target_url="https://app.com",
            tasks=[HandoffTask(task_number=1, issue_id="ISS-001", severity="high", title="Bug")],
            feature_gaps=[FeatureGap(feature="Search", source="README", ui_status="not_found")],
        )
        json_str = h.model_dump_json()
        h2 = Handoff.model_validate_json(json_str)
        assert h2.run_id == "run-abc"
        assert len(h2.tasks) == 1
        assert h2.tasks[0].task_number == 1
        assert len(h2.feature_gaps) == 1
        assert h2.feature_gaps[0].ui_status == "not_found"


# ===========================================================================
# FileMapper tests
# ===========================================================================

class TestFileMapper:
    """Tests for core/file_mapper.py FileMapper class."""

    def test_class_interface(self):
        from preflight.core.file_mapper import FileMapper

        insights = _make_insights()
        mapper = FileMapper(insights)
        issue = _make_issue("Settings broken", likely_product_area="settings")
        result = mapper.map_issue_to_files(issue)
        assert "/settings" in result

    def test_no_insights_returns_empty(self):
        from preflight.core.file_mapper import FileMapper

        mapper = FileMapper(None)
        issue = _make_issue("Broken settings", likely_product_area="settings")
        assert mapper.map_issue_to_files(issue) == []

    def test_override_insights(self):
        from preflight.core.file_mapper import FileMapper

        mapper = FileMapper(None)
        insights = _make_insights()
        issue = _make_issue("Dashboard slow", likely_product_area="dashboard")
        result = mapper.map_issue_to_files(issue, repo_insights=insights)
        assert "/dashboard" in result

    def test_product_area_matches_route(self):
        from preflight.core.file_mapper import FileMapper

        mapper = FileMapper(_make_insights())
        issue = _make_issue("Settings broken", likely_product_area="settings")
        assert "/settings" in mapper.map_issue_to_files(issue)

    def test_title_matches_route(self):
        from preflight.core.file_mapper import FileMapper

        mapper = FileMapper(_make_insights())
        issue = _make_issue("Dashboard loading slow", likely_product_area="")
        assert "/dashboard" in mapper.map_issue_to_files(issue)

    def test_repro_step_url_matches(self):
        from preflight.core.file_mapper import FileMapper

        mapper = FileMapper(_make_insights())
        issue = _make_issue(
            "Error on page",
            likely_product_area="",
            repro_steps=["Navigate to /profile", "Click edit"],
        )
        assert "/profile" in mapper.map_issue_to_files(issue)

    def test_deduplication(self):
        from preflight.core.file_mapper import FileMapper

        mapper = FileMapper(_make_insights())
        issue = _make_issue(
            "Settings page broken",
            likely_product_area="settings",
            repro_steps=["Go to /settings"],
        )
        result = mapper.map_issue_to_files(issue)
        assert result.count("/settings") == 1

    def test_caps_at_ten(self):
        from preflight.core.file_mapper import FileMapper

        routes = [f"/page-{i}" for i in range(20)]
        mapper = FileMapper(_make_insights(routes_or_pages=routes))
        issue = _make_issue(
            " ".join(f"page-{i}" for i in range(20)),
            likely_product_area="page",
        )
        assert len(mapper.map_issue_to_files(issue)) <= 10

    def test_backward_compat_function(self):
        from preflight.core.file_mapper import map_issue_to_files

        issue = _make_issue("Settings broken", likely_product_area="settings")
        result = map_issue_to_files(issue, _make_insights())
        assert "/settings" in result


# ===========================================================================
# HandoffGenerator tests
# ===========================================================================

class TestHandoffGenerator:
    """Tests for reporting/handoff.py matching HANDOFF_SPEC."""

    def test_generate_basic(self):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Critical bug", severity="critical"),
            _make_issue("Minor thing", severity="low"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_basic")
        handoff = gen.generate(result)
        assert handoff.run_id == "run-test"
        assert handoff.product_name == "TestApp"
        assert handoff.handoff_version == "1.0"
        assert len(handoff.tasks) == 2

    def test_info_issues_skipped(self):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("FYI note", severity="info"),
            _make_issue("Real bug", severity="high"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_skip")
        handoff = gen.generate(result)
        assert len(handoff.tasks) == 1
        assert handoff.tasks[0].title == "Real bug"

    def test_tasks_numbered_and_sorted_by_severity(self):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Low issue", severity="low"),
            _make_issue("Critical issue", severity="critical"),
            _make_issue("High issue", severity="high"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_sort")
        handoff = gen.generate(result)
        assert [t.task_number for t in handoff.tasks] == [1, 2, 3]
        assert [t.severity for t in handoff.tasks] == ["critical", "high", "low"]

    def test_complexity_estimation(self):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Crit", severity="critical"),
            _make_issue("Hi", severity="high"),
            _make_issue("Med", severity="medium"),
            _make_issue("Lo", severity="low"),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_complexity")
        handoff = gen.generate(result)
        complexities = {t.title: t.estimated_complexity for t in handoff.tasks}
        assert complexities["Crit"] == "significant"
        assert complexities["Hi"] == "moderate"
        assert complexities["Med"] == "moderate"
        assert complexities["Lo"] == "quick_fix"

    def test_total_estimated_hours(self):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Crit", severity="critical"),  # 3.0h
            _make_issue("Lo", severity="low"),  # 0.5h
        ])
        gen = HandoffGenerator("/tmp/test_handoff_hours")
        handoff = gen.generate(result)
        # 3.0 + 0.5 = 3.5 -> "~4-5 hours" approximately
        assert "~" in handoff.total_estimated_hours
        assert "hours" in handoff.total_estimated_hours

    def test_feature_gaps_spec_format(self):
        from preflight.reporting.handoff import HandoffGenerator

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
        assert len(handoff.feature_gaps) == 2
        features = {g.feature for g in handoff.feature_gaps}
        assert "Search" in features
        assert "SSO" in features
        assert "Export" not in features

    def test_feature_gap_ui_status(self):
        from preflight.reporting.handoff import HandoffGenerator

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
        gap_map = {g.feature: g for g in handoff.feature_gaps}
        assert gap_map["Search"].ui_status == "different"
        assert gap_map["Notifications"].ui_status == "not_found"

    def test_repo_info_in_handoff(self):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result()
        result.config.repo_url = "https://github.com/user/repo"
        insights = _make_insights()
        gen = HandoffGenerator("/tmp/test_handoff_repo")
        handoff = gen.generate(result, insights)
        assert handoff.repo_url == "https://github.com/user/repo"
        assert handoff.tech_stack == ["React", "Next.js", "Tailwind CSS"]

    def test_dependency_inference(self):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Checkout form broken", severity="critical",
                        likely_product_area="checkout"),
            _make_issue("Payment confirmation missing", severity="high",
                        likely_product_area="confirmation",
                        repro_steps=["Complete checkout flow", "View confirmation"]),
        ])
        gen = HandoffGenerator("/tmp/test_handoff_deps")
        handoff = gen.generate(result)
        # Task 2's repro mentions "checkout" which is Task 1's area
        task1 = handoff.tasks[0]
        task2 = handoff.tasks[1]
        assert task1.title == "Checkout form broken"
        assert task2.depends_on == [1] or 1 in task2.depends_on
        assert task2.task_number in task1.blocks

    def test_generate_all_writes_files(self, tmp_path):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Bug A", severity="high", repair_brief="Fix A",
                        user_impact="Users can't proceed"),
            _make_issue("Bug B", severity="medium"),
        ])
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result)

        assert "handoff_md" in paths
        assert "handoff_json" in paths

        # Check HANDOFF.md
        md_path = Path(paths["handoff_md"])
        assert md_path.exists()
        md = md_path.read_text()
        assert "# Preflight Handoff" in md
        assert "## Context" in md
        assert "## Summary" in md
        assert "## Task 1 of 2" in md
        assert "Bug A" in md
        assert "**What's wrong:**" in md
        assert "## Verification Checklist" in md

        # Check handoff.json
        json_path = Path(paths["handoff_json"])
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["handoff_version"] == "1.0"
        assert data["run_id"] == "run-test"
        assert "product" in data
        assert data["product"]["name"] == "TestApp"
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["task_number"] == 1
        assert "dependency_graph" in data

    def test_markdown_spec_format(self, tmp_path):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Login broken", severity="critical",
                        likely_product_area="login",
                        user_impact="Users cannot log in",
                        repro_steps=["Go to /login", "Enter creds", "Click submit"],
                        expected="User logged in",
                        repair_brief="Fix auth handler",
                        evidence=Evidence(screenshots=["screenshots/login-fail.png"])),
        ])
        result.config.repo_url = "https://github.com/user/repo"
        insights = _make_insights()
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result, insights)

        md = Path(paths["handoff_md"]).read_text()
        assert "# Preflight Handoff — TestApp" in md
        assert "Repo: https://github.com/user/repo" in md
        assert "Tech stack: React, Next.js, Tailwind CSS" in md
        assert "## Task 1 of 1 — CRITICAL" in md
        assert "**What's wrong:**" in md
        assert "**Where to look:**" in md
        assert "**Repro:**" in md
        assert "**Expected:**" in md
        assert "**Fix guidance:**" in md
        assert "**Verify fix:**" in md
        assert "**Evidence:**" in md
        assert "**Complexity:** significant" in md
        assert "## Verification Checklist" in md
        assert "preflight run" in md

    def test_json_spec_format(self, tmp_path):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Bug", severity="high"),
        ])
        result.config.repo_url = "https://github.com/user/repo"
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result, _make_insights())

        data = json.loads(Path(paths["handoff_json"]).read_text())
        # Check spec structure
        assert data["product"]["repo"] == "https://github.com/user/repo"
        assert data["product"]["tech_stack"] == ["React", "Next.js", "Tailwind CSS"]
        task = data["tasks"][0]
        assert "task_number" in task
        assert "depends_on" in task
        assert "blocks" in task
        assert "estimated_complexity" in task
        assert "dependency_graph" in data

    def test_dependency_notes_in_markdown(self, tmp_path):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("A", severity="critical", likely_product_area="checkout"),
            _make_issue("B", severity="high", likely_product_area="payment",
                        repro_steps=["Complete checkout", "Check payment"]),
        ])
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result)
        md = Path(paths["handoff_md"]).read_text()
        assert "## Dependency Notes" in md

    def test_feature_gaps_in_markdown(self, tmp_path):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result()
        result.intent_model.feature_expectations = [
            FeatureExpectation(feature_name="Dark mode", source="README", verified=None),
        ]
        gen = HandoffGenerator(str(tmp_path))
        paths = gen.generate_all(result)
        md = Path(paths["handoff_md"]).read_text()
        assert "## Feature Gaps" in md
        assert "Dark mode" in md
        assert "not_found" in md

    def test_file_mapping_with_repo_insights(self, tmp_path):
        from preflight.reporting.handoff import HandoffGenerator

        result = _make_run_result(issues=[
            _make_issue("Settings crash", severity="high",
                        likely_product_area="settings"),
        ])
        gen = HandoffGenerator(str(tmp_path))
        handoff = gen.generate(result, _make_insights())
        assert "/settings" in handoff.tasks[0].likely_files


# ===========================================================================
# CLI tests
# ===========================================================================

class TestHandoffCLI:
    """Tests for CLI handoff command and --handoff flag."""

    def test_handoff_command_exists(self):
        from preflight.cli import main
        assert "handoff" in main.commands

    def test_handoff_command_has_format_option(self):
        from preflight.cli import main
        cmd = main.commands["handoff"]
        param_names = [p.name for p in cmd.params]
        assert "fmt" in param_names

    def test_run_command_has_handoff_option(self):
        from preflight.cli import main
        cmd = main.commands["run"]
        param_names = [p.name for p in cmd.params]
        assert "handoff_format" in param_names

    def test_handoff_command_generates_files(self, tmp_path):
        from click.testing import CliRunner
        from preflight.cli import main

        result = _make_run_result(issues=[
            _make_issue("Test bug", severity="high"),
        ])
        report_json = tmp_path / "report.json"
        report_json.write_text(result.model_dump_json(indent=2))

        runner = CliRunner()
        res = runner.invoke(main, ["handoff", str(tmp_path)])
        assert res.exit_code == 0
        assert "Handoff generated" in res.output
        assert (tmp_path / "HANDOFF.md").exists()
        assert (tmp_path / "handoff.json").exists()

    def test_handoff_command_with_format(self, tmp_path):
        from click.testing import CliRunner
        from preflight.cli import main

        result = _make_run_result(issues=[
            _make_issue("Bug", severity="medium"),
        ])
        (tmp_path / "report.json").write_text(result.model_dump_json(indent=2))

        runner = CliRunner()
        res = runner.invoke(main, ["handoff", str(tmp_path), "--format", "claude-code"])
        assert res.exit_code == 0
        assert "claude-code" in res.output

    def test_handoff_command_missing_dir(self):
        from click.testing import CliRunner
        from preflight.cli import main

        runner = CliRunner()
        res = runner.invoke(main, ["handoff", "/nonexistent/path"])
        assert res.exit_code != 0

    def test_handoff_shows_scope(self, tmp_path):
        from click.testing import CliRunner
        from preflight.cli import main

        result = _make_run_result(issues=[
            _make_issue("Bug", severity="critical"),
        ])
        (tmp_path / "report.json").write_text(result.model_dump_json(indent=2))

        runner = CliRunner()
        res = runner.invoke(main, ["handoff", str(tmp_path)])
        assert "Estimated scope" in res.output
