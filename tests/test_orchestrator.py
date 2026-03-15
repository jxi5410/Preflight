"""Tests for Phase 2: Orchestrator comparative evaluation, LLM dedup, performance budgets."""

import json
from unittest.mock import MagicMock, patch

import pytest

from preflight.core.orchestrator import (
    COMPARATIVE_SYSTEM_PROMPT,
    DEDUP_SYSTEM_PROMPT,
    Orchestrator,
)
from preflight.core.performance import (
    PerformanceResult,
    classify_product_type,
    evaluate_snapshot_performance,
    get_budget,
    performance_results_to_issues,
    summarize_performance,
)
from preflight.core.schemas import (
    AgentPersona,
    CoverageMap,
    CoverageEntry,
    Issue,
    IssueCategory,
    PageSnapshot,
    Platform,
    ProductIntentModel,
    RunConfig,
    RunResult,
    Severity,
)


# ---------------------------------------------------------------------------
# Performance budget tests
# ---------------------------------------------------------------------------


class TestProductTypeClassification:
    def test_marketing_site(self):
        assert classify_product_type("Marketing Landing Page") == "marketing_site"
        assert classify_product_type("Portfolio Site") == "marketing_site"

    def test_saas_app(self):
        assert classify_product_type("SaaS Dashboard") == "saas_app"
        assert classify_product_type("Web Application") == "saas_app"
        assert classify_product_type("Analytics Platform") == "saas_app"

    def test_mobile_web(self):
        assert classify_product_type("Mobile Web App") == "mobile_web"

    def test_default(self):
        assert classify_product_type("Something Unknown") == "default"
        assert classify_product_type("") == "default"


class TestGetBudget:
    def test_returns_budget_for_known_type(self):
        budget = get_budget("SaaS Dashboard")
        assert "lcp_ms" in budget
        assert "cls_score" in budget
        assert "load_time_ms" in budget

    def test_returns_default_for_unknown(self):
        budget = get_budget("Unknown Type")
        assert budget == get_budget("")  # Both should be default


class TestPerformanceResult:
    def test_pass(self):
        r = PerformanceResult("lcp_ms", 1500, 2500, 4000)
        assert r.status == "pass"

    def test_warn(self):
        r = PerformanceResult("lcp_ms", 3000, 2500, 4000)
        assert r.status == "warn"

    def test_fail(self):
        r = PerformanceResult("lcp_ms", 5000, 2500, 4000)
        assert r.status == "fail"

    def test_on_boundary_warn(self):
        # Exactly at warn threshold should be pass (> not >=)
        r = PerformanceResult("lcp_ms", 2500, 2500, 4000)
        assert r.status == "pass"

    def test_just_over_warn(self):
        r = PerformanceResult("lcp_ms", 2501, 2500, 4000)
        assert r.status == "warn"

    def test_label_and_unit(self):
        r = PerformanceResult("lcp_ms", 3000, 2500, 4000)
        assert r.label == "Largest Contentful Paint"
        assert r.unit == "ms"

    def test_repr(self):
        r = PerformanceResult("lcp_ms", 3000, 2500, 4000)
        assert "lcp_ms" in repr(r)
        assert "warn" in repr(r)


class TestEvaluateSnapshotPerformance:
    def test_evaluates_available_metrics(self):
        snap = PageSnapshot(
            url="https://test.com",
            lcp_ms=3500.0,
            cls_score=0.05,
            load_time_ms=2000,
        )
        results = evaluate_snapshot_performance(snap, "SaaS Dashboard")
        assert len(results) >= 2
        metric_names = {r.metric for r in results}
        assert "lcp_ms" in metric_names
        assert "cls_score" in metric_names
        assert "load_time_ms" in metric_names

    def test_skips_none_metrics(self):
        snap = PageSnapshot(url="https://test.com", lcp_ms=None, cls_score=None)
        results = evaluate_snapshot_performance(snap, "default")
        # Should return no results for None metrics
        metric_names = {r.metric for r in results}
        assert "lcp_ms" not in metric_names

    def test_zero_values_skipped(self):
        snap = PageSnapshot(url="https://test.com", load_time_ms=0, network_error_count=0)
        results = evaluate_snapshot_performance(snap, "default")
        metric_names = {r.metric for r in results}
        # Zero values (falsy) are skipped
        assert "load_time_ms" not in metric_names


class TestPerformanceResultsToIssues:
    def test_generates_issue_for_fail(self):
        results = [PerformanceResult("lcp_ms", 5000, 2500, 4000)]
        snap = PageSnapshot(url="https://test.com")
        issues = performance_results_to_issues(results, snap)
        assert len(issues) == 1
        assert issues[0].severity == Severity.high
        assert "exceeded" in issues[0].title
        assert issues[0].confidence == 1.0

    def test_generates_issue_for_warn(self):
        results = [PerformanceResult("lcp_ms", 3000, 2500, 4000)]
        snap = PageSnapshot(url="https://test.com")
        issues = performance_results_to_issues(results, snap)
        assert len(issues) == 1
        assert issues[0].severity == Severity.medium
        assert "warning" in issues[0].title

    def test_no_issue_for_pass(self):
        results = [PerformanceResult("lcp_ms", 1500, 2500, 4000)]
        snap = PageSnapshot(url="https://test.com")
        issues = performance_results_to_issues(results, snap)
        assert len(issues) == 0

    def test_observed_facts_include_measurement(self):
        results = [PerformanceResult("load_time_ms", 6000, 3000, 5000)]
        snap = PageSnapshot(url="https://example.com/slow")
        issues = performance_results_to_issues(results, snap)
        assert any("6000ms" in fact for fact in issues[0].observed_facts)
        assert any("example.com" in fact for fact in issues[0].observed_facts)


class TestSummarizePerformance:
    def test_summary_counts(self):
        results = [
            PerformanceResult("lcp_ms", 1500, 2500, 4000),  # pass
            PerformanceResult("cls_score", 0.15, 0.1, 0.25),  # warn
            PerformanceResult("load_time_ms", 9000, 4000, 8000),  # fail
        ]
        summary = summarize_performance(results)
        assert summary["perf_pass_count"] == 1
        assert summary["perf_warn_count"] == 1
        assert summary["perf_fail_count"] == 1
        assert summary["perf_lcp_ms_value"] == 1500
        assert summary["perf_lcp_ms_status"] == "pass"

    def test_empty_results(self):
        summary = summarize_performance([])
        assert summary["perf_pass_count"] == 0


# ---------------------------------------------------------------------------
# LLM dedup tests
# ---------------------------------------------------------------------------


class TestDeduplicateByTitle:
    """Test the fallback title-based dedup."""

    def test_no_duplicates(self):
        issues = [
            Issue(title="Issue A", agent="agent-1", confidence=0.8),
            Issue(title="Issue B", agent="agent-2", confidence=0.9),
        ]
        result = Orchestrator._deduplicate_by_title(issues)
        assert len(result) == 2

    def test_exact_title_dedup(self):
        issues = [
            Issue(title="Button broken", agent="agent-1", confidence=0.7),
            Issue(title="Button broken", agent="agent-2", confidence=0.9),
        ]
        result = Orchestrator._deduplicate_by_title(issues)
        assert len(result) == 1
        assert result[0].confidence == 0.9  # Higher confidence kept
        assert any("agent-1" in f for f in result[0].observed_facts)

    def test_case_insensitive_dedup(self):
        issues = [
            Issue(title="Missing Alt Text", agent="agent-1", confidence=0.8),
            Issue(title="missing alt text", agent="agent-2", confidence=0.6),
        ]
        result = Orchestrator._deduplicate_by_title(issues)
        assert len(result) == 1

    def test_empty_list(self):
        assert Orchestrator._deduplicate_by_title([]) == []


class TestDeduplicateWithLLM:
    """Test LLM-based deduplication."""

    def test_llm_clusters_applied(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "clusters": [
                {"indices": [0, 2], "reason": "Both about form validation"},
            ],
        }

        orch = Orchestrator(mock_llm)
        issues = [
            Issue(title="Form has no validation", agent="agent-1", confidence=0.9),
            Issue(title="Button color wrong", agent="agent-2", confidence=0.8),
            Issue(title="Missing input validation on signup", agent="agent-3", confidence=0.7),
            Issue(title="Slow page load", agent="agent-4", confidence=0.6),
        ]

        result = orch._deduplicate_with_llm(issues)

        # Should have 3: one from cluster (highest confidence) + 2 unclustered
        assert len(result) == 3
        titles = {r.title for r in result}
        assert "Form has no validation" in titles  # Highest confidence in cluster
        assert "Button color wrong" in titles
        assert "Slow page load" in titles

    def test_llm_no_clusters_keeps_all(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {"clusters": []}

        orch = Orchestrator(mock_llm)
        issues = [
            Issue(title="A", agent="1", confidence=0.5),
            Issue(title="B", agent="2", confidence=0.5),
        ]

        result = orch._deduplicate_with_llm(issues)
        assert len(result) == 2

    def test_llm_annotates_also_reported_by(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "clusters": [
                {"indices": [0, 1], "reason": "Same issue"},
            ],
        }

        orch = Orchestrator(mock_llm)
        issues = [
            Issue(title="Issue A", agent="agent-alpha", confidence=0.9),
            Issue(title="Issue A variant", agent="agent-beta", confidence=0.7),
        ]

        result = orch._deduplicate_with_llm(issues)
        assert len(result) == 1
        assert any("agent-beta" in f for f in result[0].observed_facts)

    def test_invalid_indices_handled(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "clusters": [
                {"indices": [0, 99], "reason": "Bad cluster"},  # 99 is out of range
            ],
        }

        orch = Orchestrator(mock_llm)
        issues = [
            Issue(title="A", agent="1", confidence=0.8),
            Issue(title="B", agent="2", confidence=0.7),
        ]

        result = orch._deduplicate_with_llm(issues)
        # Index 0 goes in cluster, index 1 is unclustered
        assert len(result) == 2


class TestDeduplicateIssues:
    """Test the top-level dedup routing."""

    def test_small_list_uses_title_dedup(self):
        mock_llm = MagicMock()
        orch = Orchestrator(mock_llm)

        issues = [
            Issue(title="A", agent="1"),
            Issue(title="B", agent="2"),
        ]
        result = orch._deduplicate_issues(issues)
        assert len(result) == 2
        mock_llm.complete_json.assert_not_called()  # No LLM call for small lists

    def test_large_list_uses_llm(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {"clusters": []}

        orch = Orchestrator(mock_llm)
        issues = [Issue(title=f"Issue {i}", agent=f"agent-{i}") for i in range(5)]

        result = orch._deduplicate_issues(issues)
        assert len(result) == 5
        mock_llm.complete_json.assert_called_once()

    def test_llm_failure_falls_back(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = Exception("API error")

        orch = Orchestrator(mock_llm)
        issues = [
            Issue(title="A", agent="1"),
            Issue(title="B", agent="2"),
            Issue(title="C", agent="3"),
            Issue(title="A", agent="4"),  # Title duplicate
        ]

        result = orch._deduplicate_issues(issues)
        assert len(result) == 3  # Title dedup catches the "A" duplicate

    def test_empty_list(self):
        orch = Orchestrator(MagicMock())
        assert orch._deduplicate_issues([]) == []


# ---------------------------------------------------------------------------
# Comparative evaluation tests
# ---------------------------------------------------------------------------


class TestComparativeEvaluation:
    def _make_orchestrator(self, llm_response):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = llm_response
        return Orchestrator(mock_llm)

    def test_generates_convergence_issues(self):
        orch = self._make_orchestrator({
            "convergence_findings": [
                {
                    "title": "Confusing checkout flow",
                    "description": "Multiple personas struggled with checkout",
                    "personas_affected": ["agent-1", "agent-2", "agent-3"],
                    "convergence_count": 3,
                    "recommended_severity": "high",
                    "evidence_summary": "3 personas could not complete purchase",
                },
            ],
            "persona_specific_findings": [],
            "cross_persona_summary": "Major checkout UX problem.",
        })

        agents = [
            AgentPersona(id="agent-1", name="New User", role="R", persona_type="first_time_user"),
            AgentPersona(id="agent-2", name="Power User", role="R", persona_type="power_user"),
            AgentPersona(id="agent-3", name="Skeptic", role="R", persona_type="skeptical_buyer"),
        ]

        issues = [
            Issue(title="Checkout confusing", agent="agent-1"),
            Issue(title="Cannot buy", agent="agent-2"),
            Issue(title="Purchase unclear", agent="agent-3"),
        ]

        result = orch._comparative_evaluation(issues, agents, CoverageMap())
        assert len(result) == 1
        assert "Convergence" in result[0].title
        assert result[0].severity == Severity.high
        assert "3 of 3 personas" in result[0].observed_facts[0]

    def test_generates_persona_specific_issues(self):
        orch = self._make_orchestrator({
            "convergence_findings": [],
            "persona_specific_findings": [
                {
                    "title": "Missing audit trail",
                    "description": "No audit log visible",
                    "persona": "Compliance Reviewer",
                    "why_only_this_persona": "Only compliance experts look for audit trails",
                    "recommended_severity": "medium",
                },
            ],
            "cross_persona_summary": "Specialist concern found.",
        })

        agents = [
            AgentPersona(id="a1", name="User", role="R", persona_type="user"),
            AgentPersona(id="a2", name="Compliance", role="R", persona_type="compliance"),
        ]

        result = orch._comparative_evaluation(
            [Issue(title="No audit log", agent="a2")],
            agents,
            CoverageMap(),
        )
        assert len(result) == 1
        assert "only" in result[0].title.lower() or "Compliance Reviewer" in result[0].title

    def test_skips_with_single_agent(self):
        orch = self._make_orchestrator({})
        agents = [AgentPersona(id="a1", name="Solo", role="R", persona_type="user")]
        result = orch._comparative_evaluation(
            [Issue(title="X", agent="a1")], agents, CoverageMap(),
        )
        assert result == []

    def test_skips_with_no_issues(self):
        orch = self._make_orchestrator({})
        agents = [
            AgentPersona(id="a1", name="U1", role="R", persona_type="user"),
            AgentPersona(id="a2", name="U2", role="R", persona_type="user"),
        ]
        result = orch._comparative_evaluation([], agents, CoverageMap())
        assert result == []

    def test_llm_failure_returns_empty(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = Exception("API error")
        orch = Orchestrator(mock_llm)

        agents = [
            AgentPersona(id="a1", name="U1", role="R", persona_type="user"),
            AgentPersona(id="a2", name="U2", role="R", persona_type="user"),
        ]
        result = orch._comparative_evaluation(
            [Issue(title="X", agent="a1")], agents, CoverageMap(),
        )
        assert result == []

    def test_convergence_confidence_scales_with_count(self):
        orch = self._make_orchestrator({
            "convergence_findings": [
                {
                    "title": "Universal issue",
                    "description": "Everyone found it",
                    "personas_affected": ["a1", "a2", "a3", "a4", "a5"],
                    "convergence_count": 5,
                    "recommended_severity": "critical",
                    "evidence_summary": "All personas hit this",
                },
            ],
            "persona_specific_findings": [],
        })

        agents = [
            AgentPersona(id=f"a{i}", name=f"U{i}", role="R", persona_type="user")
            for i in range(5)
        ]

        result = orch._comparative_evaluation(
            [Issue(title="X", agent=f"a{i}") for i in range(5)],
            agents,
            CoverageMap(),
        )
        assert result[0].confidence == 1.0  # min(0.5 + 5*0.1, 1.0)


# ---------------------------------------------------------------------------
# Orchestrator performance integration tests
# ---------------------------------------------------------------------------


class TestOrchestratorPerformanceEvaluation:
    def test_evaluate_performance_with_snapshots(self):
        orch = Orchestrator(MagicMock())

        snapshots = [
            PageSnapshot(url="https://test.com", lcp_ms=5000.0, load_time_ms=3000),
            PageSnapshot(url="https://test.com/page2", cls_score=0.3),
        ]

        issues, scores = orch._evaluate_performance(snapshots, "SaaS Dashboard")
        assert len(issues) > 0  # Should have at least one budget violation
        assert "perf_pass_count" in scores or "perf_warn_count" in scores or "perf_fail_count" in scores

    def test_evaluate_performance_deduplicates_urls(self):
        orch = Orchestrator(MagicMock())

        # Same URL twice — should only evaluate once
        snapshots = [
            PageSnapshot(url="https://test.com", lcp_ms=5000.0),
            PageSnapshot(url="https://test.com", lcp_ms=3000.0),
        ]

        issues, _ = orch._evaluate_performance(snapshots, "default")
        # Should only get issues for first snapshot
        urls_in_issues = set()
        for i in issues:
            for fact in i.observed_facts:
                if "test.com" in fact:
                    urls_in_issues.add("test.com")
        assert len(urls_in_issues) <= 1

    def test_evaluate_performance_empty(self):
        orch = Orchestrator(MagicMock())
        issues, scores = orch._evaluate_performance([], "default")
        assert issues == []
        assert scores == {}

    def test_add_snapshots(self):
        orch = Orchestrator(MagicMock())
        orch.add_snapshots([PageSnapshot(url="https://test.com")])
        orch.add_snapshots([PageSnapshot(url="https://test.com/2")])
        assert len(orch._collected_snapshots) == 2


# ---------------------------------------------------------------------------
# Prompt quality tests
# ---------------------------------------------------------------------------


class TestPromptQuality:
    def test_dedup_system_prompt_has_key_instructions(self):
        assert "semantically similar" in DEDUP_SYSTEM_PROMPT
        assert "cluster" in DEDUP_SYSTEM_PROMPT.lower()
        assert "indices" in DEDUP_SYSTEM_PROMPT

    def test_comparative_prompt_has_key_instructions(self):
        assert "convergence" in COMPARATIVE_SYSTEM_PROMPT.lower()
        assert "persona-specific" in COMPARATIVE_SYSTEM_PROMPT.lower()
        assert "convergence_count" in COMPARATIVE_SYSTEM_PROMPT

    def test_web_runner_prompt_requires_evidence(self):
        from preflight.runners.web_runner import EVALUATION_SYSTEM_PROMPT
        assert "EVIDENCE ANCHORING" in EVALUATION_SYSTEM_PROMPT
        assert "rejected" in EVALUATION_SYSTEM_PROMPT
        assert "Screenshot reference" in EVALUATION_SYSTEM_PROMPT
        assert "Observed absence" in EVALUATION_SYSTEM_PROMPT

    def test_design_lens_prompt_requires_evidence(self):
        from preflight.lenses.design_lens import DESIGN_REVIEW_SYSTEM
        assert "EVIDENCE ANCHORING" in DESIGN_REVIEW_SYSTEM
        assert "rejected" in DESIGN_REVIEW_SYSTEM

    def test_institutional_lens_prompt_requires_evidence(self):
        from preflight.lenses.institutional_lens import INSTITUTIONAL_SYSTEM
        assert "EVIDENCE ANCHORING" in INSTITUTIONAL_SYSTEM
        assert "rejected" in INSTITUTIONAL_SYSTEM
