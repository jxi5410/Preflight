"""Quick Check — lightweight, single-pass evaluation for MCP and CI.

Returns a fast assessment of a URL without the full multi-agent pipeline.
Designed for <30s turnaround: scrape + single LLM call + structured result.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from preflight.core.llm import LLMClient
from preflight.core.schemas import Severity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class QuickIssue(BaseModel):
    """A single finding from a quick check."""
    title: str
    severity: str = "medium"
    category: str = "functional"
    confidence: float = 0.7
    user_impact: str = ""
    viewport: str = "both"  # desktop | mobile | both


class QuickCheckResult(BaseModel):
    """Result of a quick check — fast, lightweight assessment."""
    url: str
    product_name: str = ""
    product_type: str = ""
    input_first: bool = False
    input_type: str = ""
    issues: list[QuickIssue] = Field(default_factory=list)
    summary: str = ""
    score: float = 0.0  # 0.0 (terrible) to 1.0 (no issues)
    checked_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Quick check function
# ---------------------------------------------------------------------------

QUICK_CHECK_PROMPT = """\
You are Preflight, an AI QA evaluation system. Analyze this web page content
and provide a quick assessment of quality issues a real user would encounter.

URL: {url}
{focus_section}
Page content:
{content}

Accessibility tree (interactive elements):
{accessibility_tree}
{input_first_section}
Respond with JSON:
{{
  "product_name": "name of the product",
  "product_type": "saas | marketing | ecommerce | content | other",
  "input_first": false,
  "input_type": "",
  "issues": [
    {{
      "title": "short issue title",
      "severity": "critical | high | medium | low | info",
      "category": "functional | ux | ui | performance | trust | accessibility | copy | auth",
      "confidence": 0.8,
      "user_impact": "what the user experiences"
    }}
  ],
  "summary": "1-2 sentence overall assessment",
  "score": 0.75
}}

Set input_first to true if the product's primary interaction requires user input
before showing content (search engines, AI tools, URL analyzers, etc.).
If input_first is true, set input_type to one of: search, prompt, url, code, data, free_text.

Focus on real, observable problems. Be specific and evidence-based.
Do not invent issues you cannot see in the content.
Return 0-10 issues, prioritized by severity.
"""


async def quick_check(
    url: str,
    focus: str | None = None,
    llm: LLMClient | None = None,
    tier: str = "balanced",
) -> QuickCheckResult:
    """Run a quick, single-pass evaluation of a URL.

    Captures both desktop and mobile screenshots and evaluates via vision.

    Args:
        url: The URL to check.
        focus: Optional focus area (e.g. "checkout flow", "accessibility").
        llm: Optional pre-configured LLM client.
        tier: Model tier to use if creating a new LLM client.

    Returns:
        QuickCheckResult with issues and summary.
    """
    import base64
    import time

    from playwright.async_api import async_playwright

    start = time.monotonic()

    if llm is None:
        llm = LLMClient(tier=tier)

    # Step 1: Capture desktop + mobile screenshots and page content
    desktop_screenshot = b""
    mobile_screenshot = b""
    content = ""
    accessibility_tree = ""

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            # Desktop capture
            desktop_ctx = await browser.new_context(
                viewport={"width": 1440, "height": 900}
            )
            desktop_page = await desktop_ctx.new_page()
            await desktop_page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await desktop_page.wait_for_timeout(2000)
            content = await desktop_page.evaluate("() => document.body.innerText") or ""
            desktop_screenshot = await desktop_page.screenshot(full_page=True)
            await desktop_ctx.close()

            # Mobile capture
            mobile_ctx = await browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
            )
            mobile_page = await mobile_ctx.new_page()
            await mobile_page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await mobile_page.wait_for_timeout(2000)
            mobile_screenshot = await mobile_page.screenshot(full_page=True)
            await mobile_ctx.close()

            await browser.close()

    except Exception as e:
        logger.warning("Quick check screenshot capture failed for %s: %s", url, e)
        content = f"(Failed to load page: {e})"

    if not content:
        content = "(Page returned no visible content)"
    if len(content) > 6000:
        content = content[:6000] + "\n...(truncated)"

    # Step 2: Vision-based evaluation with both screenshots
    focus_section = f"Focus area: {focus}\n" if focus else ""

    vision_prompt = f"""You are Preflight, an AI QA evaluation system. You are looking at TWO screenshots of the same page:

IMAGE 1: Desktop viewport (1440x900)
IMAGE 2: Mobile viewport (390x844, iPhone)

URL: {url}
{focus_section}
Page text (truncated):
{content}

Focus primarily on the MOBILE screenshot (Image 2). Most users are on phones. Desktop is secondary.

═══════════════════════════════════════════════════════
MOBILE EVALUATION (Image 2) — DO THIS FIRST AND IN DETAIL
═══════════════════════════════════════════════════════

Look carefully at the mobile screenshot and check ALL of the following:

1. CONTENT HIDDEN BY HEADERS: Look at the TOP of the mobile screenshot. Is any page
   content (text, cards, list items) partially hidden behind a fixed navigation bar,
   search bar, or sticky header? If the first visible content item appears to start
   mid-sentence or mid-element, this is a cut-off bug.

2. OVERLAPPING ELEMENTS: Look at every button and interactive element. Does any element
   OVERLAP another element? Are there any buttons, panels, filters, or UI components
   that visually overlap each other? Elements should never stack on top of each other
   unless it's an intentional modal with a backdrop.

3. PANELS WITHOUT CLOSE BUTTONS: Are there any open panels, sidebars, filter drawers,
   or overlays that have no visible close button (X) and no obvious way to dismiss them?

4. ZOOM/SCALE ISSUES: Does the initial view show an appropriate amount of content?
   If a map or content area appears extremely zoomed in with very little visible,
   the default zoom is wrong for mobile. Check if the viewport shows a reasonable
   amount of information.

5. TOUCH TARGET PROBLEMS: Are any clickable elements so close together that a finger
   tap would likely hit the wrong one? Buttons should have adequate spacing.

6. HORIZONTAL OVERFLOW: Can you see a horizontal scrollbar or content extending
   beyond the right edge of the 390px viewport?

7. NAVIGATION ADAPTATION: Does the desktop nav properly collapse into a hamburger
   menu or mobile-friendly navigation? Or is the full desktop nav crammed in?

8. TEXT READABILITY: Is text readable without zooming on a phone screen?

9. CONTENT VISIBILITY: Is critical information visible above the fold on mobile,
   or is it pushed too far down?

═══════════════════════════════════════════════════════
DESKTOP EVALUATION (Image 1) — Secondary
═══════════════════════════════════════════════════════

- Visual design: alignment, spacing, sizing, hierarchy, polish
- Functionality: do elements look clickable/interactive? Any broken layouts?
- Trust: does this look professional and trustworthy?
- Content: is the copy clear and helpful?

Respond with JSON:
{{
  "product_name": "name",
  "product_type": "saas | marketing | ecommerce | content | other",
  "input_first": false,
  "input_type": "",
  "desktop_issues": [
    {{
      "title": "short issue title",
      "severity": "critical | high | medium | low | info",
      "category": "functional | ux | ui | performance | trust | design",
      "confidence": 0.8,
      "user_impact": "what the user experiences"
    }}
  ],
  "mobile_issues": [
    {{
      "title": "short issue title",
      "severity": "critical | high | medium | low | info",
      "category": "responsive | ux | ui | functional",
      "confidence": 0.8,
      "user_impact": "what the user experiences"
    }}
  ],
  "summary": "1-2 sentence overall assessment. MUST mention mobile issues first.",
  "score": 0.75
}}

IMPORTANT: You MUST report mobile issues separately in "mobile_issues".
Every mobile layout problem is at least "high" severity.
Return 0-15 issues total, prioritized by severity."""

    try:
        images = []
        if desktop_screenshot:
            images.append((desktop_screenshot, "image/png"))
            logger.info("Desktop screenshot captured: %d bytes", len(desktop_screenshot))
        if mobile_screenshot:
            images.append((mobile_screenshot, "image/png"))
            logger.info("Mobile screenshot captured: %d bytes", len(mobile_screenshot))

        if images:
            logger.info("Sending %d screenshots to LLM via vision...", len(images))
            data = llm.complete_json_with_vision(
                vision_prompt, images=images, tier="fast"
            )
            # Merge desktop_issues and mobile_issues into unified issues list
            merged_issues = []
            for raw in data.get("desktop_issues", []):
                raw["viewport"] = "desktop"
                merged_issues.append(raw)
            for raw in data.get("mobile_issues", []):
                raw["viewport"] = "mobile"
                merged_issues.append(raw)
            # Also include any legacy "issues" key for backwards compat
            for raw in data.get("issues", []):
                if raw not in merged_issues:
                    merged_issues.append(raw)
            data["issues"] = merged_issues
            logger.info("Vision evaluation complete, %d issues found", len(data.get("issues", [])))
        else:
            logger.warning("No screenshots captured — falling back to text-only evaluation")
            data = llm.complete_json(vision_prompt, tier="fast")

        # Step 2b: Dedicated mobile detail check — second vision call on mobile only
        if mobile_screenshot:
            mobile_detail_issues = _run_mobile_detail_check(
                llm, mobile_screenshot, url
            )
            if mobile_detail_issues:
                data.setdefault("issues", []).extend(mobile_detail_issues)
                logger.info(
                    "Mobile detail check found %d additional issues",
                    len(mobile_detail_issues),
                )

    except Exception as e:
        logger.warning("Quick check LLM call failed: %s", e)
        elapsed = time.monotonic() - start
        return QuickCheckResult(
            url=url,
            summary=f"Quick check failed: {e}",
            score=0.5,
            duration_seconds=round(elapsed, 1),
        )

    is_input_first = data.get("input_first", False)
    input_type = data.get("input_type", "")

    # Step 2b: If input-first detected, run a heuristic seed input and re-evaluate
    if is_input_first and input_type:
        from preflight.core.seed_input import get_heuristic_seed_input

        seed = get_heuristic_seed_input(input_type)
        try:
            seed_content = await _quick_check_seed_input(None, url, seed.input_text)
            if seed_content:
                seed_prompt = QUICK_CHECK_PROMPT.format(
                    url=url,
                    content=seed_content,
                    accessibility_tree="(after seed input submission)",
                    focus_section=focus_section,
                    input_first_section=(
                        f"\nThis is an input-first product. A seed input '{seed.input_text}' "
                        f"was typed and submitted. The content below shows the results.\n"
                        f"Evaluate both the input UX and the quality of results.\n"
                    ),
                )
                try:
                    seed_data = llm.complete_json(seed_prompt, tier="fast")
                    for raw_issue in seed_data.get("issues", []):
                        data.setdefault("issues", []).append(raw_issue)
                    if seed_data.get("summary"):
                        data["summary"] = (
                            data.get("summary", "")
                            + f" After typing '{seed.input_text}': "
                            + seed_data["summary"]
                        )
                except Exception as e:
                    logger.warning("Quick check seed evaluation failed: %s", e)
        except Exception as e:
            logger.warning("Quick check seed input failed: %s", e)

    elapsed = time.monotonic() - start

    # Step 3: Parse into schema (dedup by title)
    seen_titles: set[str] = set()
    issues = []
    for raw_issue in data.get("issues", []):
        title = raw_issue.get("title", "Unknown issue")
        if title in seen_titles:
            continue
        seen_titles.add(title)
        issues.append(QuickIssue(
            title=title,
            severity=raw_issue.get("severity", "medium"),
            category=raw_issue.get("category", "functional"),
            confidence=raw_issue.get("confidence", 0.7),
            user_impact=raw_issue.get("user_impact", ""),
            viewport=raw_issue.get("viewport", "both"),
        ))

    return QuickCheckResult(
        url=url,
        product_name=data.get("product_name", ""),
        product_type=data.get("product_type", ""),
        input_first=is_input_first,
        input_type=input_type,
        issues=issues,
        summary=data.get("summary", ""),
        score=max(0.0, min(1.0, data.get("score", 0.5))),
        duration_seconds=round(elapsed, 1),
    )


MOBILE_DETAIL_PROMPT = """Look at this mobile screenshot (390px wide, iPhone).

Check these specific issues — they are the most common mobile bugs:

A. CONTENT HIDDEN BY HEADERS: Is any page content (text, cards, list items) partially hidden behind a fixed navigation bar, search bar, or sticky header at the top of the screen? If the first visible content item appears to start mid-sentence or mid-element, this is a cut-off bug.

B. OVERLAPPING ELEMENTS: Are there any buttons, panels, filters, or UI components that visually overlap each other? Elements should never stack on top of each other unless it's an intentional modal.

C. PANELS WITHOUT CLOSE BUTTONS: Are there any open panels, sidebars, filter drawers, or overlays that have no visible close button (X) and no obvious way to dismiss them?

D. ZOOM/SCALE ISSUES: Does the initial view show an appropriate amount of content? If a map or content area appears extremely zoomed in with very little visible, the default zoom is wrong for mobile.

E. TOUCH TARGET OVERLAP: Are any clickable elements so close together that a finger tap would likely hit the wrong one?

F. HORIZONTAL OVERFLOW: Can you see a horizontal scrollbar or content extending beyond the right edge?

For each issue found, describe EXACTLY where on the screen it is (top-left, center, behind the nav bar, etc.) and what element is affected.

Respond with JSON: {"mobile_issues": [{"title": "...", "severity": "critical|high|medium|low", "category": "responsive", "confidence": 0.0-1.0, "user_impact": "..."}], "mobile_score": 0.0-1.0}"""


def _run_mobile_detail_check(
    llm: LLMClient,
    mobile_screenshot: bytes,
    url: str,
) -> list[dict]:
    """Run a dedicated mobile-only vision check for overlap/cutoff issues.

    Returns a list of raw issue dicts with viewport='mobile'.
    """
    try:
        data = llm.complete_json_with_vision(
            MOBILE_DETAIL_PROMPT,
            images=[(mobile_screenshot, "image/png")],
            tier="fast",
        )
        issues = []
        for raw in data.get("mobile_issues", []):
            raw["viewport"] = "mobile"
            # Ensure mobile layout issues are at least high severity
            if raw.get("category") == "responsive" and raw.get("severity") in (
                "low", "info",
            ):
                raw["severity"] = "medium"
            issues.append(raw)
        return issues
    except Exception as e:
        logger.warning("Mobile detail check failed for %s: %s", url, e)
        return []


async def _quick_check_seed_input(
    runner: "WebRunner",
    url: str,
    seed_text: str,
) -> str:
    """Navigate to URL, type seed input, submit, return resulting page content.

    Uses Playwright directly for deterministic interaction (no LLM).
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)

            # Find input field
            input_el = None
            for selector in [
                'input[type="search"]',
                'input[type="text"]',
                "textarea",
                "input:not([type])",
                '[role="searchbox"]',
                '[role="textbox"]',
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=1000):
                        input_el = el
                        break
                except Exception:
                    continue

            if not input_el:
                return ""

            await input_el.clear()
            await input_el.fill(seed_text)

            # Try submit button, then Enter
            submitted = False
            for selector in [
                'button[type="submit"]',
                "form button",
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                await input_el.press("Enter")

            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await page.wait_for_timeout(2000)

            text = await page.evaluate("() => document.body.innerText")
            return text[:8000]
        except Exception as e:
            logger.warning("Seed input interaction failed: %s", e)
            return ""
        finally:
            await browser.close()
