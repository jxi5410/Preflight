"""Tests for Phase 1: Web Runner, Actions, Page Snapshot, LLM Vision."""

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from preflight.core.schemas import (
    Action,
    AgentPersona,
    CoverageMap,
    Evidence,
    Issue,
    IssueCategory,
    JourneyStep,
    PageSnapshot,
    Platform,
    RunConfig,
    Severity,
)
from preflight.core.actions import (
    _format_a11y_node,
    ACTION_PLAN_SYSTEM_PROMPT,
)
from preflight.runners.page_snapshot import snapshot_to_prompt_context


# ---------------------------------------------------------------------------
# Schema tests for new Phase 1 models
# ---------------------------------------------------------------------------


class TestActionSchema:
    def test_basic_action(self):
        action = Action(type="click", target="Submit", reason="Submit the form")
        assert action.type == "click"
        assert action.target == "Submit"
        assert action.value is None

    def test_navigate_action(self):
        action = Action(type="navigate", target="https://example.com/dashboard")
        assert action.type == "navigate"

    def test_fill_form_action(self):
        field_map = json.dumps({"Email": "test@example.com", "Password": "secret"})
        action = Action(type="fill_form", target=field_map, value=None)
        parsed = json.loads(action.target)
        assert parsed["Email"] == "test@example.com"

    def test_scroll_action(self):
        action = Action(type="scroll", target="down", value="600")
        assert action.value == "600"

    def test_action_json_roundtrip(self):
        action = Action(type="search", target="test query", reason="Search for test")
        data = json.loads(action.model_dump_json())
        restored = Action(**data)
        assert restored.type == "search"
        assert restored.target == "test query"


class TestPageSnapshotSchema:
    def test_defaults(self):
        snap = PageSnapshot()
        assert snap.url == ""
        assert snap.console_errors == []
        assert snap.lcp_ms is None
        assert snap.cls_score is None

    def test_full_construction(self):
        snap = PageSnapshot(
            url="https://example.com",
            title="Example",
            accessibility_tree='[WebArea "Example"]\n  [heading "Welcome"]',
            screenshot_base64="aGVsbG8=",
            screenshot_path="/tmp/shot.png",
            console_errors=["[error] Failed to load resource"],
            network_error_count=2,
            load_time_ms=1500,
            lcp_ms=2100.0,
            cls_score=0.05,
            page_text="Welcome to Example",
        )
        assert snap.url == "https://example.com"
        assert snap.network_error_count == 2
        assert snap.lcp_ms == 2100.0

    def test_json_roundtrip(self):
        snap = PageSnapshot(
            url="https://test.com",
            title="Test",
            load_time_ms=500,
        )
        data = json.loads(snap.model_dump_json())
        restored = PageSnapshot(**data)
        assert restored.url == "https://test.com"
        assert restored.load_time_ms == 500


class TestJourneyStepSchema:
    def test_basic_step(self):
        step = JourneyStep(
            step_number=1,
            action=Action(type="navigate", target="https://example.com"),
            screenshot_path="/tmp/step1.png",
            persona_reaction="Page loaded quickly",
            confidence_level=0.8,
        )
        assert step.step_number == 1
        assert step.action.type == "navigate"
        assert step.confidence_level == 0.8

    def test_step_with_issues(self):
        step = JourneyStep(
            step_number=3,
            action=Action(type="click", target="Submit"),
            issues_found=["ISS-ABC123", "ISS-DEF456"],
        )
        assert len(step.issues_found) == 2

    def test_step_with_snapshots(self):
        before = PageSnapshot(url="https://example.com/form")
        after = PageSnapshot(url="https://example.com/result")
        step = JourneyStep(
            step_number=2,
            action=Action(type="click", target="Submit"),
            snapshot_before=before,
            snapshot_after=after,
        )
        assert step.snapshot_before.url == "https://example.com/form"
        assert step.snapshot_after.url == "https://example.com/result"


# ---------------------------------------------------------------------------
# Accessibility tree formatting tests
# ---------------------------------------------------------------------------


class TestA11yFormatting:
    def test_simple_node(self):
        node = {"role": "button", "name": "Submit"}
        result = _format_a11y_node(node)
        assert 'button "Submit"' in result

    def test_nested_nodes(self):
        node = {
            "role": "WebArea",
            "name": "Example",
            "children": [
                {"role": "heading", "name": "Welcome"},
                {"role": "button", "name": "Sign Up"},
            ],
        }
        result = _format_a11y_node(node)
        assert "WebArea" in result
        assert "heading" in result
        assert "Sign Up" in result

    def test_node_with_state(self):
        node = {"role": "checkbox", "name": "Agree", "checked": True}
        result = _format_a11y_node(node)
        assert "checked" in result

    def test_max_depth_limiting(self):
        # Build a deeply nested tree
        node = {"role": "root", "name": "r", "children": [
            {"role": "l1", "name": "1", "children": [
                {"role": "l2", "name": "2", "children": [
                    {"role": "l3", "name": "3"}
                ]}
            ]}
        ]}
        result = _format_a11y_node(node, max_depth=2)
        assert "l2" in result
        assert "l3" not in result

    def test_node_with_value(self):
        node = {"role": "textbox", "name": "Email", "value": "test@example.com"}
        result = _format_a11y_node(node)
        assert 'value="test@example.com"' in result


# ---------------------------------------------------------------------------
# Snapshot prompt formatting tests
# ---------------------------------------------------------------------------


class TestSnapshotToPrompt:
    def test_basic_formatting(self):
        snap = PageSnapshot(
            url="https://example.com",
            title="Example Page",
            accessibility_tree='[WebArea "Example"]\n  [heading "Welcome"]',
        )
        result = snapshot_to_prompt_context(snap)
        assert "https://example.com" in result
        assert "Example Page" in result
        assert "Accessibility Tree" in result
        assert "heading" in result

    def test_with_performance_metrics(self):
        snap = PageSnapshot(
            url="https://test.com",
            title="Test",
            load_time_ms=2500,
            lcp_ms=3100.0,
            cls_score=0.12,
            network_error_count=3,
        )
        result = snapshot_to_prompt_context(snap)
        assert "2500ms" in result
        assert "3100ms" in result
        assert "0.120" in result
        assert "Network errors: 3" in result

    def test_with_console_errors(self):
        snap = PageSnapshot(
            url="https://test.com",
            title="Test",
            console_errors=["[error] 404 resource.js", "[warning] Deprecated API"],
        )
        result = snapshot_to_prompt_context(snap)
        assert "Console Errors" in result
        assert "404 resource.js" in result

    def test_fallback_to_page_text(self):
        snap = PageSnapshot(
            url="https://test.com",
            title="Test",
            accessibility_tree="(accessibility tree unavailable)",
            page_text="Welcome to the test application.",
        )
        result = snapshot_to_prompt_context(snap)
        assert "Page Text" in result
        assert "Welcome to the test" in result

    def test_empty_snapshot(self):
        snap = PageSnapshot()
        result = snapshot_to_prompt_context(snap)
        assert "URL:" in result


# ---------------------------------------------------------------------------
# LLM vision method tests (mocked)
# ---------------------------------------------------------------------------


class TestLLMVision:
    def test_complete_with_vision_anthropic(self):
        """Test that vision method formats Anthropic API call correctly."""
        from preflight.core.llm import LLMClient

        mock_client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Vision response")]
        mock_client.messages.create.return_value = mock_msg

        llm = LLMClient.__new__(LLMClient)
        llm.provider = "anthropic"
        llm.model = "claude-sonnet-4-20250514"
        llm._client = mock_client

        # 1x1 PNG pixel
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )

        result = llm.complete_with_vision(
            "Describe this image",
            images=[(tiny_png, "image/png")],
            system="Test system",
        )

        assert result == "Vision response"
        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs["messages"]
        content = messages[0]["content"]
        # Should have image block + text block
        assert len(content) == 2
        assert content[0]["type"] == "image"
        assert content[1]["type"] == "text"

    def test_complete_with_vision_openai(self):
        """Test that vision method formats OpenAI API call correctly."""
        from preflight.core.llm import LLMClient

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "OpenAI vision response"
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        llm = LLMClient.__new__(LLMClient)
        llm.provider = "openai"
        llm.model = "gpt-4o"
        llm._client = mock_client

        tiny_png = b"\x89PNG\r\n\x1a\n"  # PNG header

        result = llm.complete_with_vision(
            "Describe this",
            images=[(tiny_png, "image/png")],
        )

        assert result == "OpenAI vision response"
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = messages[-1]
        content = user_msg["content"]
        assert any(c["type"] == "image_url" for c in content)
        assert any(c["type"] == "text" for c in content)

    def test_complete_json_with_vision(self):
        """Test JSON extraction from vision response."""
        from preflight.core.llm import LLMClient

        mock_client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text='{"issues": [], "status": "ok"}')]
        mock_client.messages.create.return_value = mock_msg

        llm = LLMClient.__new__(LLMClient)
        llm.provider = "anthropic"
        llm.model = "claude-sonnet-4-20250514"
        llm._client = mock_client

        result = llm.complete_json_with_vision(
            "Evaluate",
            images=[(b"\x89PNG", "image/png")],
        )

        assert result["status"] == "ok"
        assert result["issues"] == []

    def test_extract_json_with_markdown_fences(self):
        """Test JSON extraction handles markdown code fences."""
        from preflight.core.llm import LLMClient

        result = LLMClient._extract_json('```json\n{"key": "value"}\n```')
        assert result["key"] == "value"

    def test_extract_json_plain(self):
        from preflight.core.llm import LLMClient

        result = LLMClient._extract_json('{"a": 1}')
        assert result["a"] == 1


# ---------------------------------------------------------------------------
# WebRunner tests (mocked Playwright + LLM)
# ---------------------------------------------------------------------------


class TestWebRunnerIssueParser:
    """Test the issue parsing logic in WebRunner."""

    def test_parse_issues_basic(self):
        from preflight.runners.web_runner import WebRunner

        mock_llm = MagicMock()
        runner = WebRunner(mock_llm, "/tmp/test-artifacts")

        persona = AgentPersona(
            name="Test User",
            role="Tester",
            persona_type="first_time_user",
        )
        snapshot = PageSnapshot(
            url="https://example.com",
            screenshot_path="/tmp/step1.png",
        )

        data = {
            "issues": [
                {
                    "title": "Button not clickable",
                    "severity": "high",
                    "confidence": 0.9,
                    "category": "functional",
                    "user_impact": "User cannot submit form",
                    "observed_facts": ["Submit button has no click handler"],
                    "inferred_judgment": "Form submission is broken",
                    "evidence_ref": "step-1",
                },
            ],
        }

        issues = runner._parse_issues(data, persona, snapshot, step_number=1)
        assert len(issues) == 1
        assert issues[0].title == "Button not clickable"
        assert issues[0].severity == Severity.high
        assert issues[0].confidence == 0.9
        assert "step1.png" in issues[0].evidence.screenshots[0]

    def test_parse_issues_invalid_category_fallback(self):
        from preflight.runners.web_runner import WebRunner

        runner = WebRunner(MagicMock(), "/tmp/test-artifacts")
        persona = AgentPersona(name="T", role="R", persona_type="pt")
        snapshot = PageSnapshot(url="https://test.com")

        data = {
            "issues": [
                {"title": "Issue", "category": "nonexistent_category"},
            ],
        }

        issues = runner._parse_issues(data, persona, snapshot, 1)
        assert issues[0].category == IssueCategory.functional

    def test_parse_issues_mobile_platform(self):
        from preflight.runners.web_runner import WebRunner

        runner = WebRunner(MagicMock(), "/tmp/test-artifacts")
        persona = AgentPersona(
            name="Mobile User",
            role="Tester",
            persona_type="mobile",
            device_preference=Platform.mobile_web,
        )
        snapshot = PageSnapshot(url="https://test.com")

        data = {"issues": [{"title": "Mobile issue"}]}
        issues = runner._parse_issues(data, persona, snapshot, 1)
        assert issues[0].platform == Platform.mobile_web

    def test_parse_issues_empty(self):
        from preflight.runners.web_runner import WebRunner

        runner = WebRunner(MagicMock(), "/tmp/test-artifacts")
        persona = AgentPersona(name="T", role="R", persona_type="pt")
        snapshot = PageSnapshot(url="https://test.com")

        data = {"issues": []}
        issues = runner._parse_issues(data, persona, snapshot, 1)
        assert issues == []


class TestWebRunnerPlanActions:
    """Test the action planning flow (mocked LLM)."""

    @pytest.mark.asyncio
    async def test_plan_with_vision(self):
        from preflight.runners.web_runner import WebRunner

        mock_llm = MagicMock()
        mock_llm.complete_json_with_vision.return_value = {
            "actions": [
                {"type": "click", "target": "Sign Up", "reason": "Start registration"},
            ],
            "journey_complete": False,
            "persona_reaction": "Looking for sign up button",
        }

        runner = WebRunner(mock_llm, "/tmp/test-artifacts")
        persona = AgentPersona(
            name="New User",
            role="First-time visitor",
            persona_type="first_time_user",
            goals=["Create an account"],
        )
        snapshot = PageSnapshot(
            url="https://example.com",
            title="Example",
            accessibility_tree='[WebArea]\n  [button "Sign Up"]',
            screenshot_base64=base64.b64encode(b"\x89PNG").decode(),
        )

        plan = await runner._plan_actions(
            snapshot=snapshot,
            persona=persona,
            journey="registration",
            step_number=1,
            max_steps=10,
            previous_actions=["Navigated to https://example.com"],
        )

        assert len(plan["actions"]) == 1
        assert plan["actions"][0]["type"] == "click"
        assert not plan["journey_complete"]
        # Verify vision was used
        mock_llm.complete_json_with_vision.assert_called_once()

    @pytest.mark.asyncio
    async def test_plan_without_screenshot_falls_back(self):
        from preflight.runners.web_runner import WebRunner

        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "actions": [],
            "journey_complete": True,
            "persona_reaction": "Done",
        }

        runner = WebRunner(mock_llm, "/tmp/test-artifacts")
        persona = AgentPersona(name="T", role="R", persona_type="pt")
        snapshot = PageSnapshot(url="https://test.com", screenshot_base64="")

        plan = await runner._plan_actions(snapshot, persona, "test", 1, 10, [])
        assert plan["journey_complete"] is True
        mock_llm.complete_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_plan_llm_failure_graceful(self):
        from preflight.runners.web_runner import WebRunner

        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = Exception("API error")

        runner = WebRunner(mock_llm, "/tmp/test-artifacts")
        persona = AgentPersona(name="T", role="R", persona_type="pt")
        snapshot = PageSnapshot(url="https://test.com")

        plan = await runner._plan_actions(snapshot, persona, "test", 1, 10, [])
        assert plan["journey_complete"] is True
        assert plan["actions"] == []


class TestWebRunnerJudge:
    """Test the snapshot evaluation (judging) flow."""

    @pytest.mark.asyncio
    async def test_judge_with_vision_produces_issues(self):
        from preflight.runners.web_runner import WebRunner

        mock_llm = MagicMock()
        mock_llm.complete_json_with_vision.return_value = {
            "issues": [
                {
                    "title": "Missing alt text on hero image",
                    "severity": "medium",
                    "confidence": 0.85,
                    "category": "accessibility",
                    "user_impact": "Screen reader users cannot understand the hero image",
                    "observed_facts": ["img element has no alt attribute"],
                    "inferred_judgment": "Accessibility issue",
                    "evidence_ref": "step-2",
                },
            ],
            "persona_reaction": "Noticed accessibility gap",
            "confidence_level": 0.8,
        }

        runner = WebRunner(mock_llm, "/tmp/test-artifacts")
        persona = AgentPersona(
            name="A11y Tester", role="QA", persona_type="accessibility_expert",
        )
        snapshot = PageSnapshot(
            url="https://test.com",
            screenshot_base64=base64.b64encode(b"\x89PNG").decode(),
            screenshot_path="/tmp/step2.png",
        )

        issues = await runner._judge_snapshot(
            snapshot=snapshot,
            persona=persona,
            journey="homepage review",
            step_number=2,
            max_steps=10,
            action_description="Scrolled down",
            previous_actions=["Navigated", "Scrolled"],
        )

        assert len(issues) == 1
        assert issues[0].title == "Missing alt text on hero image"
        assert issues[0].category == IssueCategory.accessibility

    @pytest.mark.asyncio
    async def test_judge_llm_failure_returns_empty(self):
        from preflight.runners.web_runner import WebRunner

        mock_llm = MagicMock()
        mock_llm.complete_json_with_vision.side_effect = Exception("API down")

        runner = WebRunner(mock_llm, "/tmp/test-artifacts")
        persona = AgentPersona(name="T", role="R", persona_type="pt")
        snapshot = PageSnapshot(
            url="https://test.com",
            screenshot_base64=base64.b64encode(b"\x89PNG").decode(),
        )

        issues = await runner._judge_snapshot(
            snapshot, persona, "test", 1, 10, "action", [],
        )
        assert issues == []


# ---------------------------------------------------------------------------
# Action prompt constants test
# ---------------------------------------------------------------------------


class TestActionPrompts:
    def test_system_prompt_has_action_types(self):
        assert "navigate" in ACTION_PLAN_SYSTEM_PROMPT
        assert "click" in ACTION_PLAN_SYSTEM_PROMPT
        assert "fill_form" in ACTION_PLAN_SYSTEM_PROMPT
        assert "search" in ACTION_PLAN_SYSTEM_PROMPT
        assert "scroll" in ACTION_PLAN_SYSTEM_PROMPT
        assert "go_back" in ACTION_PLAN_SYSTEM_PROMPT

    def test_system_prompt_requires_accessible_names(self):
        assert "accessible name" in ACTION_PLAN_SYSTEM_PROMPT.lower() or \
               "CSS selector" in ACTION_PLAN_SYSTEM_PROMPT
