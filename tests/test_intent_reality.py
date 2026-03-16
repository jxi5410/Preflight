"""Tests for intent-vs-reality gap detection."""

import pytest

from preflight.core.schemas import (
    AgentPersona,
    IntentRealityGap,
    RunConfig,
    RunResult,
)


class TestIntentRealityGap:
    """Test IntentRealityGap model."""

    def test_critical_gap(self):
        gap = IntentRealityGap(
            claim_source="README",
            claim_text="One-click export to PDF",
            reality="Export required 4 steps and a settings change",
            severity="critical",
            persona_who_found_it="agent-abc",
        )
        assert gap.severity == "critical"
        assert gap.claim_source == "README"
        assert gap.evidence_screenshot is None

    def test_notable_gap(self):
        gap = IntentRealityGap(
            claim_source="landing page",
            claim_text="Works on all devices",
            reality="Mobile layout was broken with overlapping elements",
            severity="notable",
            evidence_screenshot="screenshot-mobile-01.png",
            persona_who_found_it="agent-mobile",
        )
        assert gap.severity == "notable"
        assert gap.evidence_screenshot == "screenshot-mobile-01.png"

    def test_minor_gap(self):
        gap = IntentRealityGap(
            claim_source="docs",
            claim_text="Beautiful, modern interface",
            reality="Interface is functional but dated-looking",
            severity="minor",
            persona_who_found_it="agent-design",
        )
        assert gap.severity == "minor"

    def test_serialization_roundtrip(self):
        gap = IntentRealityGap(
            claim_source="README",
            claim_text="Real-time collaboration",
            reality="Changes took 5+ seconds to sync",
            severity="notable",
            persona_who_found_it="agent-collab",
        )
        data = gap.model_dump()
        restored = IntentRealityGap(**data)
        assert restored.claim_text == "Real-time collaboration"
        assert restored.reality == "Changes took 5+ seconds to sync"


class TestRunResultWithGaps:
    """Test RunResult includes intent-reality gaps."""

    def test_default_empty(self):
        result = RunResult(config=RunConfig(target_url="https://example.com"))
        assert result.intent_reality_gaps == []

    def test_with_gaps(self):
        result = RunResult(
            config=RunConfig(target_url="https://example.com"),
            intent_reality_gaps=[
                IntentRealityGap(
                    claim_source="README",
                    claim_text="Fast search",
                    reality="Search took 8 seconds",
                    severity="critical",
                    persona_who_found_it="agent-a",
                ),
                IntentRealityGap(
                    claim_source="landing page",
                    claim_text="Easy to use",
                    reality="3 personas found the UX confusing",
                    severity="notable",
                    persona_who_found_it="agent-b",
                ),
            ],
        )
        assert len(result.intent_reality_gaps) == 2
        critical = [g for g in result.intent_reality_gaps if g.severity == "critical"]
        assert len(critical) == 1


class TestIntentRealityInReport:
    """Test report generator includes claims vs reality section."""

    def test_report_includes_gaps_section(self):
        from preflight.reporting.report_generator import ReportGenerator
        import tempfile

        result = RunResult(
            config=RunConfig(target_url="https://example.com"),
            agents=[
                AgentPersona(name="Alex", role="User", persona_type="first_time_user"),
            ],
            intent_reality_gaps=[
                IntentRealityGap(
                    claim_source="README",
                    claim_text="One-click export",
                    reality="Export requires 4 steps",
                    severity="critical",
                    persona_who_found_it="agent-alex",
                ),
                IntentRealityGap(
                    claim_source="landing page",
                    claim_text="Lightning fast",
                    reality="Page loaded in 5 seconds",
                    severity="notable",
                    persona_who_found_it="agent-alex",
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ReportGenerator(output_dir=tmpdir)
            path = gen.generate_markdown(result)
            with open(path) as f:
                content = f.read()

            assert "Claims vs. Reality" in content
            assert "One-click export" in content
            assert "CRITICAL GAP" in content

    def test_report_no_gaps_section_when_empty(self):
        from preflight.reporting.report_generator import ReportGenerator
        import tempfile

        result = RunResult(
            config=RunConfig(target_url="https://example.com"),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ReportGenerator(output_dir=tmpdir)
            path = gen.generate_markdown(result)
            with open(path) as f:
                content = f.read()

            assert "Claims vs. Reality" not in content
