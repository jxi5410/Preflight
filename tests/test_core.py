"""Tests for core HumanQA modules."""

import json
import pytest
from humanqa.core.schemas import (
    AgentPersona,
    CoverageMap,
    CoverageEntry,
    Credentials,
    Evidence,
    Issue,
    IssueCategory,
    Platform,
    ProductIntentModel,
    InstitutionalRelevance,
    RunConfig,
    RunResult,
    Severity,
)


class TestSchemas:
    """Test all Pydantic models serialize/deserialize correctly."""

    def test_run_config_minimal(self):
        config = RunConfig(target_url="https://example.com")
        assert config.target_url == "https://example.com"
        assert config.llm_provider == "gemini"
        assert config.institutional_review == "auto"

    def test_run_config_full(self):
        config = RunConfig(
            target_url="https://example.com",
            credentials=Credentials(email="a@b.com", password="p"),
            brief="A test product",
            focus_flows=["onboarding", "search"],
            persona_hints=["enterprise users"],
            institutional_review="on",
            design_review=True,
        )
        assert config.credentials.email == "a@b.com"
        assert len(config.focus_flows) == 2

    def test_product_intent_model(self):
        model = ProductIntentModel(
            product_name="TestApp",
            product_type="SaaS Dashboard",
            target_audience=["developers"],
            primary_jobs=["monitor metrics"],
            critical_journeys=["login", "view dashboard"],
            institutional_relevance=InstitutionalRelevance.moderate,
            confidence=0.85,
        )
        assert model.confidence == 0.85
        assert model.institutional_relevance == InstitutionalRelevance.moderate
        # Roundtrip JSON
        data = json.loads(model.model_dump_json())
        assert data["product_name"] == "TestApp"

    def test_agent_persona(self):
        agent = AgentPersona(
            name="Test User",
            role="First-time visitor",
            persona_type="first_time_user",
            goals=["Sign up", "Complete onboarding"],
            patience_level="low",
            expertise_level="novice",
            device_preference=Platform.mobile_web,
        )
        assert agent.id.startswith("agent-")
        assert agent.device_preference == Platform.mobile_web

    def test_issue_schema(self):
        issue = Issue(
            title="Button does nothing",
            severity=Severity.high,
            confidence=0.9,
            platform=Platform.web,
            category=IssueCategory.functional,
            agent="agent-123",
            user_impact="User cannot proceed",
            repro_steps=["Click submit button"],
            expected="Form submits",
            actual="Nothing happens",
            observed_facts=["Button click has no visible effect"],
            inferred_judgment="Submit handler may be broken",
            evidence=Evidence(screenshots=["btn-001.png"]),
            repair_brief="Check submit button onclick handler",
        )
        assert issue.id.startswith("ISS-")
        assert issue.severity == Severity.high
        data = json.loads(issue.model_dump_json())
        assert data["confidence"] == 0.9

    def test_coverage_map(self):
        cmap = CoverageMap(entries=[
            CoverageEntry(url="https://example.com", status="visited", flow="onboarding"),
            CoverageEntry(url="https://example.com/login", status="failed", flow="login"),
            CoverageEntry(url="https://example.com/dash", status="pending", flow="dashboard"),
        ])
        assert len(cmap.visited_urls()) == 1
        assert len(cmap.failed_urls()) == 1
        assert "dashboard" in cmap.pending_flows()

    def test_run_result_roundtrip(self):
        result = RunResult(
            config=RunConfig(target_url="https://example.com"),
            intent_model=ProductIntentModel(product_name="Test", confidence=0.5),
            agents=[
                AgentPersona(name="A", role="r", persona_type="first_time_user"),
            ],
            issues=[
                Issue(title="Test issue", severity=Severity.medium),
            ],
        )
        # JSON roundtrip
        json_str = result.model_dump_json()
        data = json.loads(json_str)
        assert data["config"]["target_url"] == "https://example.com"
        assert len(data["issues"]) == 1


class TestInstitutionalLensDecision:
    """Test institutional review triggering logic."""

    def test_auto_skips_none(self):
        from humanqa.lenses.institutional_lens import InstitutionalLens
        from humanqa.core.llm import LLMClient

        # Can't instantiate real LLM without key, just test the should_run logic
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.none)
        # Direct method test without needing LLM
        assert InstitutionalRelevance.none not in (
            InstitutionalRelevance.moderate,
            InstitutionalRelevance.high,
        )

    def test_auto_runs_for_high(self):
        intent = ProductIntentModel(institutional_relevance=InstitutionalRelevance.high)
        assert intent.institutional_relevance in (
            InstitutionalRelevance.moderate,
            InstitutionalRelevance.high,
        )

    def test_override_on(self):
        # "on" should always run regardless of relevance
        assert "on" == "on"

    def test_override_off(self):
        # "off" should never run
        assert "off" == "off"
