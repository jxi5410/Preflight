"""Tests for first-impression evaluation lens."""

import pytest
from pydantic import ValidationError

from preflight.core.schemas import (
    AgentPersona,
    CognitiveBehavior,
    FirstImpressionResult,
    PageSnapshot,
    ProductIntentModel,
    RunResult,
    RunConfig,
)
from preflight.lenses.first_impression_lens import FirstImpressionLens


class TestFirstImpressionResult:
    """Test FirstImpressionResult model."""

    def test_valid_result(self):
        result = FirstImpressionResult(
            persona_id="agent-abc123",
            clarity_score=8,
            clarity_explanation="Product purpose is immediately clear",
            trust_score=7,
            trust_signals_found=["HTTPS", "Privacy policy link"],
            trust_signals_missing=["Social proof"],
            cta_score=9,
            cta_explanation="Single prominent 'Get Started' button",
            relevance_score=6,
            relevance_explanation="Seems relevant but not specifically for my role",
            gut_reaction="I landed on this page and immediately understood what it does.",
            would_continue=True,
            time_to_understand_seconds=3,
        )
        assert result.clarity_score == 8
        assert result.would_continue is True
        assert len(result.trust_signals_found) == 2

    def test_score_bounds_enforced(self):
        with pytest.raises(ValidationError):
            FirstImpressionResult(
                persona_id="agent-x",
                clarity_score=11,  # Over 10
                clarity_explanation="",
                trust_score=5,
                cta_score=5,
                cta_explanation="",
                relevance_score=5,
                relevance_explanation="",
                gut_reaction="",
                would_continue=True,
                time_to_understand_seconds=5,
            )

    def test_score_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            FirstImpressionResult(
                persona_id="agent-x",
                clarity_score=-1,
                clarity_explanation="",
                trust_score=5,
                cta_score=5,
                cta_explanation="",
                relevance_score=5,
                relevance_explanation="",
                gut_reaction="",
                would_continue=True,
                time_to_understand_seconds=5,
            )

    def test_bounce_result(self):
        result = FirstImpressionResult(
            persona_id="agent-bounce",
            clarity_score=2,
            clarity_explanation="No idea what this does",
            trust_score=3,
            trust_signals_found=[],
            trust_signals_missing=["HTTPS", "Privacy policy", "Social proof"],
            cta_score=1,
            cta_explanation="Too many buttons, no clear path",
            relevance_score=2,
            relevance_explanation="This doesn't seem meant for me",
            gut_reaction="I landed on this page and I'm confused. I'd leave.",
            would_continue=False,
            time_to_understand_seconds=30,
        )
        assert result.would_continue is False
        assert result.time_to_understand_seconds == 30

    def test_serialization_roundtrip(self):
        result = FirstImpressionResult(
            persona_id="agent-rt",
            clarity_score=7,
            clarity_explanation="Clear enough",
            trust_score=6,
            cta_score=8,
            cta_explanation="Good CTA",
            relevance_score=5,
            relevance_explanation="Somewhat relevant",
            gut_reaction="Looks professional.",
            would_continue=True,
            time_to_understand_seconds=5,
        )
        data = result.model_dump()
        restored = FirstImpressionResult(**data)
        assert restored.clarity_score == 7
        assert restored.gut_reaction == "Looks professional."


class TestFirstImpressionLensIssueConversion:
    """Test that low scores generate appropriate issues."""

    def test_low_clarity_generates_issue(self):
        lens = FirstImpressionLens.__new__(FirstImpressionLens)
        results = [FirstImpressionResult(
            persona_id="agent-test",
            clarity_score=2,
            clarity_explanation="Cannot tell what this product does",
            trust_score=7,
            cta_score=7,
            cta_explanation="OK CTA",
            relevance_score=5,
            relevance_explanation="Maybe relevant",
            gut_reaction="Confused.",
            would_continue=True,
            time_to_understand_seconds=20,
        )]
        issues = lens.results_to_issues(results)
        clarity_issues = [i for i in issues if "clarity" in i.title.lower()]
        assert len(clarity_issues) == 1
        assert clarity_issues[0].severity.value == "high"

    def test_low_cta_generates_issue(self):
        lens = FirstImpressionLens.__new__(FirstImpressionLens)
        results = [FirstImpressionResult(
            persona_id="agent-test",
            clarity_score=8,
            clarity_explanation="Clear",
            trust_score=7,
            cta_score=3,
            cta_explanation="Too many options",
            relevance_score=5,
            relevance_explanation="Maybe relevant",
            gut_reaction="Where do I click?",
            would_continue=True,
            time_to_understand_seconds=5,
        )]
        issues = lens.results_to_issues(results)
        cta_issues = [i for i in issues if "call-to-action" in i.title.lower()]
        assert len(cta_issues) == 1

    def test_bounce_generates_issue(self):
        lens = FirstImpressionLens.__new__(FirstImpressionLens)
        results = [FirstImpressionResult(
            persona_id="agent-test",
            clarity_score=8,
            clarity_explanation="Clear",
            trust_score=7,
            cta_score=7,
            cta_explanation="Good CTA",
            relevance_score=5,
            relevance_explanation="OK",
            gut_reaction="Not for me.",
            would_continue=False,
            time_to_understand_seconds=5,
        )]
        issues = lens.results_to_issues(results)
        bounce_issues = [i for i in issues if "bounce" in i.title.lower()]
        assert len(bounce_issues) == 1
        assert bounce_issues[0].severity.value == "high"

    def test_good_scores_no_issues(self):
        lens = FirstImpressionLens.__new__(FirstImpressionLens)
        results = [FirstImpressionResult(
            persona_id="agent-test",
            clarity_score=9,
            clarity_explanation="Crystal clear",
            trust_score=8,
            cta_score=9,
            cta_explanation="Perfect CTA",
            relevance_score=8,
            relevance_explanation="Very relevant",
            gut_reaction="Love it!",
            would_continue=True,
            time_to_understand_seconds=2,
        )]
        issues = lens.results_to_issues(results)
        assert len(issues) == 0


class TestRunResultWithFirstImpressions:
    """Test RunResult includes first impressions."""

    def test_run_result_has_first_impressions(self):
        result = RunResult(
            config=RunConfig(target_url="https://example.com"),
        )
        assert result.first_impressions == []

    def test_run_result_with_first_impressions(self):
        fi = FirstImpressionResult(
            persona_id="agent-x",
            clarity_score=7,
            clarity_explanation="Good",
            trust_score=6,
            cta_score=8,
            cta_explanation="Clear",
            relevance_score=5,
            relevance_explanation="OK",
            gut_reaction="Looks good.",
            would_continue=True,
            time_to_understand_seconds=4,
        )
        result = RunResult(
            config=RunConfig(target_url="https://example.com"),
            first_impressions=[fi],
        )
        assert len(result.first_impressions) == 1
        assert result.first_impressions[0].persona_id == "agent-x"
