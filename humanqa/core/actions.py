"""Deterministic Action Engine for HumanQA.

The LLM plans what to do; this module executes it reliably using Playwright.
Actions are deterministic browser operations identified by accessibility
labels and roles, not fragile CSS selectors.
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from humanqa.core.schemas import Action

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts for action planning
# ---------------------------------------------------------------------------

ACTION_PLAN_SYSTEM_PROMPT = """You are a QA action planner for HumanQA.

Given the current page state (accessibility tree, URL, title, screenshot) and a persona's goals,
plan the next actions the persona would take.

Action types:
- navigate: Go to a URL. target = the URL.
- click: Click an element. target = the accessible name or role description.
- fill_form: Fill form fields. target = JSON object mapping field labels to values.
- search: Find the search input, type a query, submit. target = search query.
- scroll: Scroll the page. target = "down" or "up". value = pixel amount (default 600).
- wait_for: Wait for a condition. target = description of what to wait for.
- screenshot: Capture the current state. No target needed.
- go_back: Navigate back. No target needed.

Rules:
- Use accessible names (button text, labels, ARIA labels) for click targets, NOT CSS selectors
- Plan 1-3 actions at a time — you will be called again after execution
- If the journey goal is achieved or you cannot proceed, return an empty actions list
- Reference elements by their accessible name/role from the accessibility tree
- Always explain WHY the persona would take each action in the reason field,
  citing the specific element from the accessibility tree or screenshot

Respond with JSON: {"actions": [{"type": "...", "target": "...", "value": "...", "reason": "..."}], "journey_complete": false, "persona_reaction": "..."}"""

ACTION_PLAN_PROMPT_TEMPLATE = """## Persona
Name: {persona_name} | Role: {persona_role}
Goals: {persona_goals}
Patience: {patience_level} | Expertise: {expertise_level}

## Current Journey
{journey}

## Current Page
URL: {url}
Title: {title}

## Accessibility Tree (semantic page structure)
{accessibility_tree}

## Previous Actions Taken
{previous_actions}

## Step {step_number} of max {max_steps}

Plan the next action(s) for this persona. What would they do next?"""


# ---------------------------------------------------------------------------
# Action executor
# ---------------------------------------------------------------------------

async def execute_action(page: Page, action: Action) -> bool:
    """Execute a single action on the page. Returns True on success."""
    try:
        if action.type == "navigate":
            await page.goto(action.target, wait_until="domcontentloaded", timeout=30000)
            return True

        elif action.type == "click":
            # Try accessible name first, then text content, then role
            clicked = await _click_by_accessible_name(page, action.target)
            if clicked:
                # Wait for any navigation or DOM changes
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            return clicked

        elif action.type == "fill_form":
            return await _fill_form(page, action.target, action.value)

        elif action.type == "search":
            return await _perform_search(page, action.target)

        elif action.type == "scroll":
            amount = int(action.value or "600")
            direction = -1 if action.target == "up" else 1
            await page.evaluate(f"window.scrollBy(0, {direction * amount})")
            return True

        elif action.type == "wait_for":
            timeout = int(action.value or "5000")
            await page.wait_for_timeout(min(timeout, 10000))
            return True

        elif action.type == "screenshot":
            # Screenshots are handled by the caller, but we acknowledge the action
            return True

        elif action.type == "go_back":
            await page.go_back(wait_until="domcontentloaded", timeout=15000)
            return True

        else:
            logger.warning("Unknown action type: %s", action.type)
            return False

    except Exception as e:
        logger.warning("Action %s failed: %s", action.type, e)
        return False


async def _click_by_accessible_name(page: Page, target: str) -> bool:
    """Click an element by its accessible name, text, or role.

    Tries multiple strategies in order of reliability:
    1. get_by_role with name matching
    2. get_by_text (exact, then substring)
    3. get_by_label
    4. ARIA label attribute selector
    """
    strategies = [
        # Strategy 1: role-based with name
        lambda: page.get_by_role("link", name=target).first.click(timeout=3000),
        lambda: page.get_by_role("button", name=target).first.click(timeout=3000),
        lambda: page.get_by_role("menuitem", name=target).first.click(timeout=3000),
        lambda: page.get_by_role("tab", name=target).first.click(timeout=3000),
        # Strategy 2: text content
        lambda: page.get_by_text(target, exact=True).first.click(timeout=3000),
        lambda: page.get_by_text(target).first.click(timeout=3000),
        # Strategy 3: label
        lambda: page.get_by_label(target).first.click(timeout=3000),
        # Strategy 4: ARIA
        lambda: page.locator(f'[aria-label="{target}"]').first.click(timeout=3000),
    ]

    for strategy in strategies:
        try:
            await strategy()
            logger.debug("Clicked element matching: %s", target)
            return True
        except Exception:
            continue

    logger.warning("Could not find clickable element: %s", target)
    return False


async def _fill_form(page: Page, target: str, value: str | None) -> bool:
    """Fill form fields using accessible labels.

    target can be:
    - A single field label (value comes from action.value)
    - A JSON string mapping labels to values
    """
    import json

    try:
        # Try parsing target as JSON field map
        field_map: dict[str, str] = json.loads(target)
    except (json.JSONDecodeError, TypeError):
        # Single field: target is the label, value is the text
        field_map = {target: value or ""}

    success = True
    for label, text in field_map.items():
        filled = False
        # Try by label
        try:
            await page.get_by_label(label).first.fill(text, timeout=3000)
            filled = True
        except Exception:
            pass

        if not filled:
            # Try by placeholder
            try:
                await page.get_by_placeholder(label).first.fill(text, timeout=3000)
                filled = True
            except Exception:
                pass

        if not filled:
            # Try by role textbox with name
            try:
                await page.get_by_role("textbox", name=label).first.fill(text, timeout=3000)
                filled = True
            except Exception:
                pass

        if not filled:
            logger.warning("Could not fill field: %s", label)
            success = False

    return success


async def _perform_search(page: Page, query: str) -> bool:
    """Find the search input, type a query, and submit."""
    search_strategies = [
        lambda: page.get_by_role("searchbox").first,
        lambda: page.get_by_role("textbox", name="search").first,
        lambda: page.get_by_placeholder("Search").first,
        lambda: page.get_by_label("Search").first,
        lambda: page.locator('input[type="search"]').first,
        lambda: page.locator('input[name*="search" i]').first,
        lambda: page.locator('input[placeholder*="search" i]').first,
    ]

    for get_element in search_strategies:
        try:
            element = get_element()
            await element.fill(query, timeout=3000)
            await element.press("Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            logger.debug("Search performed: %s", query)
            return True
        except Exception:
            continue

    logger.warning("Could not find search input")
    return False


async def get_accessibility_tree(page: Page, max_depth: int = 5) -> str:
    """Get the accessibility tree of the current page as a string.

    Uses Playwright's accessibility snapshot for a semantic, structured
    representation of the page — much better than raw HTML or innerText.
    """
    try:
        snapshot = await page.accessibility.snapshot()  # type: ignore[union-attr]
        if not snapshot:
            return "(accessibility tree unavailable)"
        return _format_a11y_node(snapshot, depth=0, max_depth=max_depth)
    except Exception as e:
        logger.warning("Failed to get accessibility tree: %s", e)
        return "(accessibility tree unavailable)"


def _format_a11y_node(node: dict[str, Any], depth: int = 0, max_depth: int = 5) -> str:
    """Recursively format an accessibility tree node into a readable string."""
    if depth > max_depth:
        return ""

    indent = "  " * depth
    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")

    parts = [role]
    if name:
        parts.append(f'"{name}"')
    if value:
        parts.append(f'value="{value}"')

    # Add useful state info
    for attr in ("checked", "disabled", "expanded", "pressed", "selected", "required"):
        if node.get(attr):
            parts.append(attr)

    line = f"{indent}[{' '.join(parts)}]"
    lines = [line]

    children = node.get("children", [])
    for child in children:
        child_text = _format_a11y_node(child, depth + 1, max_depth)
        if child_text:
            lines.append(child_text)

    return "\n".join(lines)
