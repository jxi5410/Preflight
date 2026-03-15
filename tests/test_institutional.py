"""Tests for Phase 3: Institutional lens, trust lens, provenance scoring, governance."""

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from preflight.core.schemas import (
    ChecklistResult,
    CoverageEntry,
    CoverageMap,
    InstitutionalRelevance,
    Issue,
    IssueCategory,
    Platform,
    ProductIntentModel,
    ProvenanceScore,
    RunConfig,
    RunResult,
    Severity,
    TrustScorecard,
    TrustSignal,
)
from preflight.lenses.institutional_lens import (
    INSTITUTIONAL_CHECKLIST,
    INSTITUTIONAL_SYSTEM,
    InstitutionalLens,
)
from preflight.lenses.trust_lens import (
    TRUST_SIGNALS,
    TRUST_ANALYSIS_SYSTEM,
    TrustLens,
)


# ---------------------------------------------------------------------------
# Schema tests for new models
# ---------------------------------------------------------------------------


class TestChecklistResultSchema:
    def test_defaults(self):
        r = ChecklistResult(check_name="audit_trail")
        assert r.status == "not_checked"
        assert r.evidence == []
        assert r.severity_if_failed == "medium"

    def test_full(self):
        r = ChecklistResult(
            check_name="audit_trail",
            status="fail",
            evidence=["Searched for: history, audit; none found"],
            details="Look for audit logs",
            severity_if_failed="high",
        )
        assert r.status == "fail"
        assert r.severity_if_failed == "high"

    def test_json_roundtrip(self):
        r = ChecklistResult(check_name="test", status="pass", evidence=["found"])
        data = json.loads(r.model_dump_json())
        restored = ChecklistResult(**data)
        assert restored.check_name == "test"


class TestProvenanceScoreSchema:
    def test_defaults(self):
        s = ProvenanceScore(output_name="Dashboard")
        assert s.score == 0
        assert not s.sources_cited

    def test_full(self):
        s = ProvenanceScore(
            output_name="Analytics Report",
            score=4,
            sources_cited=True,
            sources_specific=True,
            freshness_shown=True,
            evidence=["Report header shows 'Source: GA4'"],
        )
        assert s.score == 4
        assert s.sources_specific is True


class TestTrustSignalSchema:
    def test_defaults(self):
        s = TrustSignal(signal_name="ssl")
        assert s.present is None
        assert s.evidence == []

    def test_present(self):
        s = TrustSignal(signal_name="privacy_policy", present=True, details="Found")
        assert s.present is True


class TestTrustScorecardSchema:
    def test_defaults(self):
        sc = TrustScorecard()
        assert sc.overall_score == 0.0
        assert sc.signals == []

    def test_with_signals(self):
        sc = TrustScorecard(
            signals=[
                TrustSignal(signal_name="ssl", present=True),
                TrustSignal(signal_name="privacy", present=False),
            ],
            overall_score=0.5,
            summary="Moderate trust.",
        )
        assert len(sc.signals) == 2


# ---------------------------------------------------------------------------
# Institutional Lens: Structured Checklist
# ---------------------------------------------------------------------------


class TestInstitutionalChecklist:
    def _make_lens(self):
        return InstitutionalLens(MagicMock())

    def test_checklist_all_pass(self):
        lens = self._make_lens()
        a11y = (
            "Page: Dashboard\n"
            "[button 'View History']\n"
            "[link 'Version 2.1']\n"
            "[text 'Source: API v3']\n"
            "[text 'Updated 5 minutes ago']\n"
            "[text 'Role: Admin']\n"
            "[dialog 'Are you sure you want to delete?']\n"
            "[text 'Error: Please check your input']\n"
            "[link 'Privacy Policy']\n"
            "[button 'Export CSV']\n"
        )
        results = lens.run_checklist(a11y, "")
        assert all(r.status == "pass" for r in results), [
            f"{r.check_name}: {r.status}" for r in results if r.status != "pass"
        ]

    def test_checklist_all_fail_empty_content(self):
        lens = self._make_lens()
        results = lens.run_checklist("", "")
        assert all(r.status == "fail" for r in results)
        assert len(results) == len(INSTITUTIONAL_CHECKLIST)

    def test_checklist_partial_pass(self):
        lens = self._make_lens()
        a11y = "Page: Settings\n[link 'Privacy Policy']\n[button 'Export data']"
        results = lens.run_checklist(a11y, "")

        result_map = {r.check_name: r for r in results}
        assert result_map["privacy_indicators"].status == "pass"
        assert result_map["export_capability"].status == "pass"
        assert result_map["audit_trail"].status == "fail"

    def test_checklist_evidence_includes_found_terms(self):
        lens = self._make_lens()
        results = lens.run_checklist("[text 'Updated 2 hours ago']", "")
        freshness = next(r for r in results if r.check_name == "data_freshness")
        assert freshness.status == "pass"
        assert any("updated" in e.lower() for e in freshness.evidence)

    def test_checklist_evidence_includes_searched_terms_on_fail(self):
        lens = self._make_lens()
        results = lens.run_checklist("nothing relevant here", "")
        audit = next(r for r in results if r.check_name == "audit_trail")
        assert audit.status == "fail"
        assert any("history" in e.lower() for e in audit.evidence)

    def test_checklist_to_issues_only_failures(self):
        lens = self._make_lens()
        results = [
            ChecklistResult(check_name="audit_trail", status="pass", evidence=["found"]),
            ChecklistResult(
                check_name="version_history", status="fail",
                evidence=["Searched for: version; none found"],
                severity_if_failed="medium",
            ),
        ]
        issues = lens._checklist_to_issues(results)
        assert len(issues) == 1
        assert issues[0].severity == Severity.medium
        assert issues[0].category == IssueCategory.institutional_trust
        assert "Version history" in issues[0].title

    def test_checklist_to_issues_empty_on_all_pass(self):
        lens = self._make_lens()
        results = [
            ChecklistResult(check_name="audit_trail", status="pass"),
            ChecklistResult(check_name="version_history", status="pass"),
        ]
        issues = lens._checklist_to_issues(results)
        assert issues == []


class TestInstitutionalChecklistDefinitions:
    def test_all_checks_have_required_fields(self):
        for check in INSTITUTIONAL_CHECKLIST:
            assert "name" in check
            assert "label" in check
            assert "search_terms" in check
            assert "severity_if_failed" in check
            assert "description" in check
            assert len(check["search_terms"]) > 0

    def test_check_names_unique(self):
        names = [c["name"] for c in INSTITUTIONAL_CHECKLIST]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Institutional Lens: Provenance Scoring
# ---------------------------------------------------------------------------


class TestProvenanceScoring:
    @pytest.mark.asyncio
    async def test_score_provenance_success(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "outputs": [
                {
                    "output_name": "Dashboard",
                    "score": 2,
                    "sources_cited": False,
                    "sources_specific": False,
                    "freshness_shown": True,
                    "evidence": ["Dashboard shows timestamps but no source attribution"],
                    "details": "Partial provenance",
                },
                {
                    "output_name": "Report",
                    "score": 4,
                    "sources_cited": True,
                    "sources_specific": True,
                    "freshness_shown": True,
                    "evidence": ["Report cites 'Source: Google Analytics'"],
                    "details": "Good provenance",
                },
            ]
        }

        lens = InstitutionalLens(mock_llm)
        intent = ProductIntentModel(product_name="Test", product_type="SaaS")
        result = RunResult(config=RunConfig(target_url="https://test.com"))

        scores = await lens.score_provenance(intent, "some a11y content", result)
        assert len(scores) == 2
        assert scores[0].score == 2
        assert scores[1].score == 4

    @pytest.mark.asyncio
    async def test_score_provenance_llm_failure(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = Exception("API error")

        lens = InstitutionalLens(mock_llm)
        intent = ProductIntentModel(product_name="Test")
        result = RunResult(config=RunConfig(target_url="https://test.com"))

        scores = await lens.score_provenance(intent, "", result)
        assert scores == []

    def test_provenance_to_issues_low_scores(self):
        lens = InstitutionalLens(MagicMock())
        scores = [
            ProvenanceScore(output_name="Dashboard", score=1, evidence=["No sources"]),
            ProvenanceScore(output_name="Report", score=4, evidence=["Good"]),
            ProvenanceScore(output_name="Feed", score=0, evidence=["Nothing"]),
        ]
        issues = lens._provenance_to_issues(scores)
        assert len(issues) == 2  # Dashboard (score=1) and Feed (score=0)
        # Score 0-1 = high severity
        assert any(i.severity == Severity.high for i in issues)

    def test_provenance_to_issues_all_good(self):
        lens = InstitutionalLens(MagicMock())
        scores = [ProvenanceScore(output_name="X", score=4)]
        assert lens._provenance_to_issues(scores) == []

    @pytest.mark.asyncio
    async def test_provenance_score_clamped(self):
        """Score should be clamped 0-5 even if LLM returns out of range."""
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "outputs": [{"output_name": "X", "score": 10}]
        }

        lens = InstitutionalLens(mock_llm)
        intent = ProductIntentModel(product_name="Test")
        result = RunResult(config=RunConfig(target_url="https://test.com"))

        scores = await lens.score_provenance(intent, "", result)
        assert scores[0].score == 5


# ---------------------------------------------------------------------------
# Institutional Lens: Governance Flow Testing
# ---------------------------------------------------------------------------


class TestGovernanceFlowTesting:
    @pytest.mark.asyncio
    async def test_governance_finds_missing_gates(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "governance_results": [
                {
                    "action": "Delete user account",
                    "has_confirmation": False,
                    "has_undo": False,
                    "has_role_check": True,
                    "has_approval_flow": False,
                    "evidence": ["Delete button has no confirmation dialog"],
                    "missing_gates": ["confirmation dialog", "undo option"],
                    "severity": "high",
                    "details": "Destructive action without safeguards",
                },
            ]
        }

        lens = InstitutionalLens(mock_llm)
        intent = ProductIntentModel(
            product_name="Test",
            trust_sensitive_actions=["Delete user account"],
        )
        result = RunResult(config=RunConfig(target_url="https://test.com"))

        issues = await lens.test_governance_flows(intent, "", "", result)
        assert len(issues) == 1
        assert "Delete user account" in issues[0].title
        assert issues[0].severity == Severity.high

    @pytest.mark.asyncio
    async def test_governance_no_missing_gates(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "governance_results": [
                {
                    "action": "Delete item",
                    "has_confirmation": True,
                    "missing_gates": [],
                },
            ]
        }

        lens = InstitutionalLens(mock_llm)
        intent = ProductIntentModel(product_name="Test")
        result = RunResult(config=RunConfig(target_url="https://test.com"))

        issues = await lens.test_governance_flows(intent, "", "", result)
        assert issues == []

    @pytest.mark.asyncio
    async def test_governance_llm_failure(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = Exception("timeout")

        lens = InstitutionalLens(mock_llm)
        intent = ProductIntentModel(product_name="Test")
        result = RunResult(config=RunConfig(target_url="https://test.com"))

        issues = await lens.test_governance_flows(intent, "", "", result)
        assert issues == []


# ---------------------------------------------------------------------------
# Institutional Lens: Should Run Logic
# ---------------------------------------------------------------------------


class TestInstitutionalShouldRun:
    def test_auto_runs_for_high(self):
        lens = InstitutionalLens(MagicMock())
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.high)
        assert lens.should_run(intent) is True

    def test_auto_runs_for_moderate(self):
        lens = InstitutionalLens(MagicMock())
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.moderate)
        assert lens.should_run(intent) is True

    def test_auto_skips_none(self):
        lens = InstitutionalLens(MagicMock())
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.none)
        assert lens.should_run(intent) is False

    def test_auto_skips_low(self):
        lens = InstitutionalLens(MagicMock())
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.low)
        assert lens.should_run(intent) is False

    def test_override_on(self):
        lens = InstitutionalLens(MagicMock())
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.none)
        assert lens.should_run(intent, override="on") is True

    def test_override_off(self):
        lens = InstitutionalLens(MagicMock())
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.high)
        assert lens.should_run(intent, override="off") is False


# ---------------------------------------------------------------------------
# Institutional Lens: Full Review Integration
# ---------------------------------------------------------------------------


class TestInstitutionalFullReview:
    @pytest.mark.asyncio
    async def test_review_skips_when_not_relevant(self):
        lens = InstitutionalLens(MagicMock())
        result = RunResult(
            config=RunConfig(target_url="https://test.com", institutional_review="auto"),
            intent_model=ProductIntentModel(institutional_relevance=InstitutionalRelevance.none),
        )
        issues = await lens.review(result)
        assert issues == []

    @pytest.mark.asyncio
    async def test_review_runs_full_pipeline(self):
        mock_llm = MagicMock()
        # Provenance scoring response
        # Governance response
        # LLM review response
        mock_llm.complete_json.side_effect = [
            # Provenance
            {"outputs": [{"output_name": "Dashboard", "score": 1, "evidence": ["no sources"]}]},
            # Governance
            {"governance_results": []},
            # LLM review
            {
                "institutional_issues": [
                    {"title": "LLM found issue", "severity": "medium", "confidence": 0.7},
                ],
                "readiness_level": "early",
                "readiness_summary": "Early stage",
            },
        ]

        lens = InstitutionalLens(mock_llm)
        result = RunResult(
            config=RunConfig(target_url="https://test.com", institutional_review="on"),
            intent_model=ProductIntentModel(
                product_name="TestApp",
                institutional_relevance=InstitutionalRelevance.high,
            ),
        )

        issues = await lens.review(result)
        # Should have checklist failures + provenance issue + LLM issue
        assert len(issues) > 0
        # Should have stored scores
        assert "institutional_checklist_total" in result.scores
        assert "provenance_avg_score" in result.scores

    @pytest.mark.asyncio
    async def test_update_scores(self):
        lens = InstitutionalLens(MagicMock())
        result = RunResult(config=RunConfig(target_url="https://test.com"))

        checklist = [
            ChecklistResult(check_name="a", status="pass"),
            ChecklistResult(check_name="b", status="fail"),
            ChecklistResult(check_name="c", status="pass"),
        ]
        provenance = [
            ProvenanceScore(output_name="X", score=3),
            ProvenanceScore(output_name="Y", score=5),
        ]

        lens._update_scores(result, checklist, provenance)
        assert result.scores["institutional_checklist_total"] == 3
        assert result.scores["institutional_checklist_passed"] == 2
        assert result.scores["institutional_checklist_ratio"] == pytest.approx(2 / 3)
        assert result.scores["provenance_avg_score"] == 4.0


# ---------------------------------------------------------------------------
# Institutional Lens: Helpers
# ---------------------------------------------------------------------------


class TestInstitutionalHelpers:
    def test_gather_a11y_content(self):
        result = RunResult(
            config=RunConfig(target_url="https://test.com"),
            coverage=CoverageMap(entries=[
                CoverageEntry(url="https://test.com", screen_name="Home", status="visited"),
            ]),
            issues=[
                Issue(title="Test", observed_facts=["Button missing label"]),
            ],
        )
        content = InstitutionalLens._gather_a11y_content(result)
        assert "Home" in content
        assert "Button missing label" in content

    def test_gather_a11y_content_empty(self):
        result = RunResult(config=RunConfig(target_url="https://test.com"))
        content = InstitutionalLens._gather_a11y_content(result)
        assert "no accessibility" in content.lower()

    def test_format_checklist_results(self):
        results = [
            ChecklistResult(check_name="audit_trail", status="pass", evidence=["Found: history"]),
            ChecklistResult(check_name="version_history", status="fail", evidence=["None found"]),
        ]
        text = InstitutionalLens._format_checklist_results(results)
        assert "[PASS]" in text
        assert "[FAIL]" in text
        assert "Audit trail" in text


# ---------------------------------------------------------------------------
# Trust Lens: Signal Checks
# ---------------------------------------------------------------------------


class TestTrustSignalChecks:
    def _make_lens(self):
        return TrustLens(MagicMock())

    def test_ssl_present(self):
        lens = self._make_lens()
        signals = lens._check_signals("https://example.com", "")
        ssl = next(s for s in signals if s.signal_name == "ssl_certificate")
        assert ssl.present is True

    def test_ssl_missing(self):
        lens = self._make_lens()
        signals = lens._check_signals("http://example.com", "")
        ssl = next(s for s in signals if s.signal_name == "ssl_certificate")
        assert ssl.present is False

    def test_privacy_policy_present(self):
        lens = self._make_lens()
        signals = lens._check_signals(
            "https://test.com",
            "Footer: Privacy Policy | Terms of Service | Contact Us",
        )
        privacy = next(s for s in signals if s.signal_name == "privacy_policy")
        assert privacy.present is True

    def test_privacy_policy_missing(self):
        lens = self._make_lens()
        signals = lens._check_signals("https://test.com", "Just a homepage with no links")
        privacy = next(s for s in signals if s.signal_name == "privacy_policy")
        assert privacy.present is False

    def test_all_signals_checked(self):
        lens = self._make_lens()
        signals = lens._check_signals("https://test.com", "")
        assert len(signals) == len(TRUST_SIGNALS)
        assert all(s.present is not None for s in signals)

    def test_content_search_case_insensitive(self):
        lens = self._make_lens()
        signals = lens._check_signals("https://test.com", "PRIVACY POLICY and TERMS OF SERVICE")
        privacy = next(s for s in signals if s.signal_name == "privacy_policy")
        assert privacy.present is True

    def test_multiple_signals_found(self):
        lens = self._make_lens()
        content = (
            "About Us | Privacy Policy | Terms of Service | Contact Us | "
            "SOC 2 Certified | GDPR Compliant | © 2026 TestCo"
        )
        signals = lens._check_signals("https://test.com", content)
        present = [s for s in signals if s.present is True]
        # SSL + privacy + terms + contact + data handling + third party + company
        assert len(present) >= 6


class TestTrustScorecard:
    def test_build_scorecard_all_present(self):
        signals = [TrustSignal(signal_name=f"s{i}", present=True) for i in range(5)]
        sc = TrustLens._build_scorecard(signals)
        assert sc.overall_score == 1.0
        assert "Strong" in sc.summary

    def test_build_scorecard_all_missing(self):
        signals = [TrustSignal(signal_name=f"s{i}", present=False) for i in range(5)]
        sc = TrustLens._build_scorecard(signals)
        assert sc.overall_score == 0.0
        assert "Weak" in sc.summary

    def test_build_scorecard_mixed(self):
        signals = [
            TrustSignal(signal_name="a", present=True),
            TrustSignal(signal_name="b", present=True),
            TrustSignal(signal_name="c", present=False),
            TrustSignal(signal_name="d", present=False),
        ]
        sc = TrustLens._build_scorecard(signals)
        assert sc.overall_score == 0.5
        assert "Moderate" in sc.summary

    def test_build_scorecard_empty(self):
        sc = TrustLens._build_scorecard([])
        assert sc.overall_score == 0.0


class TestTrustSignalsToIssues:
    def test_missing_signals_become_issues(self):
        lens = TrustLens(MagicMock())
        signals = lens._check_signals("http://test.com", "")  # HTTP = no SSL
        issues = lens._signals_to_issues(signals)
        # At least SSL should be missing
        ssl_issues = [i for i in issues if "SSL" in i.title]
        assert len(ssl_issues) == 1
        assert ssl_issues[0].severity == Severity.critical

    def test_present_signals_no_issues(self):
        lens = TrustLens(MagicMock())
        signals = [TrustSignal(signal_name="ssl_certificate", present=True)]
        issues = lens._signals_to_issues(signals)
        assert issues == []

    def test_severity_mapping(self):
        lens = TrustLens(MagicMock())
        signals = lens._check_signals("http://test.com", "")
        issues = lens._signals_to_issues(signals)
        # SSL missing = critical, others vary
        severities = {i.title: i.severity for i in issues}
        ssl_issue = next((v for k, v in severities.items() if "SSL" in k), None)
        assert ssl_issue == Severity.critical


# ---------------------------------------------------------------------------
# Trust Lens: Full Review
# ---------------------------------------------------------------------------


class TestTrustLensReview:
    @pytest.mark.asyncio
    async def test_review_returns_issues_and_scorecard(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "trust_assessment": "Moderate trust.",
            "additional_signals": [],
            "trust_gaps": [
                {
                    "title": "No security page",
                    "severity": "medium",
                    "user_impact": "Enterprise buyers want security details",
                    "observed_facts": ["No /security page found"],
                    "repair_brief": "Add a security page",
                },
            ],
        }

        lens = TrustLens(mock_llm)
        result = RunResult(
            config=RunConfig(target_url="https://test.com"),
            intent_model=ProductIntentModel(product_name="Test", product_type="SaaS"),
        )

        issues, scorecard = await lens.review(result)
        assert len(issues) > 0  # Signal issues + LLM gap
        assert isinstance(scorecard, TrustScorecard)
        assert "trust_score" in result.scores

    @pytest.mark.asyncio
    async def test_review_llm_failure_still_returns_signal_issues(self):
        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = Exception("API error")

        lens = TrustLens(mock_llm)
        result = RunResult(
            config=RunConfig(target_url="http://test.com"),  # HTTP
            intent_model=ProductIntentModel(product_name="Test"),
        )

        issues, scorecard = await lens.review(result)
        # Should still have signal-based issues (SSL missing at minimum)
        assert len(issues) > 0
        assert scorecard.overall_score < 1.0

    def test_should_run_always_true(self):
        lens = TrustLens(MagicMock())
        assert lens.should_run(ProductIntentModel()) is True


# ---------------------------------------------------------------------------
# Trust Signal Definitions
# ---------------------------------------------------------------------------


class TestTrustSignalDefinitions:
    def test_all_signals_have_required_fields(self):
        for signal in TRUST_SIGNALS:
            assert "name" in signal
            assert "label" in signal
            assert "check_type" in signal
            assert "description" in signal
            assert "severity_if_missing" in signal

    def test_signal_names_unique(self):
        names = [s["name"] for s in TRUST_SIGNALS]
        assert len(names) == len(set(names))

    def test_check_types_valid(self):
        valid_types = {"url", "content"}
        for signal in TRUST_SIGNALS:
            assert signal["check_type"] in valid_types

    def test_content_signals_have_search_terms(self):
        for signal in TRUST_SIGNALS:
            if signal["check_type"] == "content":
                assert "search_terms" in signal
                assert len(signal["search_terms"]) > 0


# ---------------------------------------------------------------------------
# Prompt Quality
# ---------------------------------------------------------------------------


class TestInstitutionalPromptQuality:
    def test_institutional_prompt_requires_evidence(self):
        assert "EVIDENCE ANCHORING" in INSTITUTIONAL_SYSTEM
        assert "rejected" in INSTITUTIONAL_SYSTEM

    def test_trust_prompt_requires_evidence(self):
        assert "EVIDENCE ANCHORING" in TRUST_ANALYSIS_SYSTEM
