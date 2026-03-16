"""Tests for emotional state model and persona baselines."""

import pytest
from pydantic import ValidationError

from preflight.core.schemas import (
    AgentPersona,
    EmotionalEvent,
    EmotionalState,
    Platform,
)
from preflight.core.persona_generator import compute_emotional_baseline


class TestEmotionalState:
    """Test EmotionalState model validation."""

    def test_defaults(self):
        state = EmotionalState()
        assert state.confidence == 0.7
        assert state.frustration == 0.0
        assert state.trust == 0.5
        assert state.engagement == 0.7
        assert state.delight == 0.0

    def test_valid_bounds(self):
        state = EmotionalState(
            confidence=0.0, frustration=1.0, trust=0.0, engagement=1.0, delight=0.5,
        )
        assert state.confidence == 0.0
        assert state.frustration == 1.0

    def test_rejects_below_zero(self):
        with pytest.raises(ValidationError):
            EmotionalState(confidence=-0.1)

    def test_rejects_above_one(self):
        with pytest.raises(ValidationError):
            EmotionalState(trust=1.1)

    def test_rejects_frustration_above_one(self):
        with pytest.raises(ValidationError):
            EmotionalState(frustration=1.5)

    def test_rejects_engagement_below_zero(self):
        with pytest.raises(ValidationError):
            EmotionalState(engagement=-0.01)


class TestEmotionalEvent:
    """Test EmotionalEvent captures state transitions."""

    def test_basic_event(self):
        event = EmotionalEvent(
            step_index=3,
            trigger="confusing label",
            dimension="frustration",
            old_value=0.2,
            new_value=0.5,
            persona_thought="I don't understand what this button does.",
        )
        assert event.step_index == 3
        assert event.dimension == "frustration"
        assert event.new_value > event.old_value

    def test_delight_event(self):
        event = EmotionalEvent(
            step_index=1,
            trigger="fast load time",
            dimension="delight",
            old_value=0.0,
            new_value=0.3,
            persona_thought="Wow, that loaded instantly!",
        )
        assert event.trigger == "fast load time"
        assert event.persona_thought.startswith("Wow")


class TestComputeEmotionalBaseline:
    """Test that different persona types get different baselines."""

    def test_first_time_user(self):
        persona = AgentPersona(
            name="Alex", role="New user", persona_type="first_time_user",
            expertise_level="novice", patience_level="moderate",
        )
        baseline = compute_emotional_baseline(persona)
        assert baseline.confidence == 0.4
        assert baseline.trust == 0.5
        assert baseline.engagement == 0.8

    def test_power_user(self):
        persona = AgentPersona(
            name="Sam", role="Developer", persona_type="power_user",
            expertise_level="expert", patience_level="moderate",
        )
        baseline = compute_emotional_baseline(persona)
        assert baseline.confidence == 0.9
        assert baseline.trust == 0.6
        assert baseline.engagement == 0.5

    def test_skeptical_buyer(self):
        persona = AgentPersona(
            name="Pat", role="Enterprise buyer", persona_type="skeptical_buyer",
            expertise_level="intermediate", patience_level="low",
        )
        baseline = compute_emotional_baseline(persona)
        assert baseline.confidence == 0.8
        assert baseline.trust == 0.3
        assert baseline.engagement == 0.4

    def test_different_personas_get_different_baselines(self):
        first_time = AgentPersona(
            name="A", role="r", persona_type="first_time_user",
            expertise_level="novice",
        )
        power = AgentPersona(
            name="B", role="r", persona_type="power_user",
            expertise_level="expert",
        )
        skeptic = AgentPersona(
            name="C", role="r", persona_type="skeptical_buyer",
            expertise_level="intermediate",
        )

        b1 = compute_emotional_baseline(first_time)
        b2 = compute_emotional_baseline(power)
        b3 = compute_emotional_baseline(skeptic)

        # All three should differ meaningfully
        assert b1.confidence != b2.confidence
        assert b2.trust != b3.trust
        assert b1.engagement != b3.engagement

    def test_novice_caps_confidence(self):
        persona = AgentPersona(
            name="A", role="r", persona_type="generic_user",
            expertise_level="novice",
        )
        baseline = compute_emotional_baseline(persona)
        assert baseline.confidence <= 0.4

    def test_low_patience_limits_engagement(self):
        persona = AgentPersona(
            name="A", role="r", persona_type="generic_user",
            patience_level="low",
        )
        baseline = compute_emotional_baseline(persona)
        assert baseline.engagement <= 0.5

    def test_persona_has_emotional_state_field(self):
        persona = AgentPersona(
            name="A", role="r", persona_type="first_time_user",
        )
        assert isinstance(persona.emotional_state, EmotionalState)
        assert isinstance(persona.emotional_timeline, list)

    def test_compliance_reviewer_baseline(self):
        persona = AgentPersona(
            name="R", role="Compliance", persona_type="risk_compliance_reviewer",
            expertise_level="expert",
        )
        baseline = compute_emotional_baseline(persona)
        assert baseline.trust <= 0.3
        assert baseline.confidence >= 0.8
