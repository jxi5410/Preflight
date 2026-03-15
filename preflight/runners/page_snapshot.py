"""Page Snapshot — structured capture of page state.

Combines accessibility tree, screenshot, performance metrics, console errors,
and page metadata into a single PageSnapshot for LLM evaluation.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

from playwright.async_api import Page

from preflight.core.actions import get_accessibility_tree
from preflight.core.schemas import PageSnapshot

logger = logging.getLogger(__name__)


async def capture_snapshot(
    page: Page,
    output_dir: Path,
    snapshot_name: str,
    console_errors: list[str] | None = None,
    network_error_count: int = 0,
    load_time_ms: int = 0,
) -> PageSnapshot:
    """Capture a full PageSnapshot of the current page state.

    Combines:
    - Accessibility tree (semantic page structure)
    - Screenshot as base64 + saved to disk
    - Performance metrics (LCP, CLS from Performance API)
    - Console errors
    - Current URL and page title
    - Visible page text (fallback)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Capture URL and title
    url = page.url
    try:
        title = await page.title()
    except Exception:
        title = ""

    # Capture accessibility tree
    a11y_tree = await get_accessibility_tree(page)

    # Capture screenshot
    screenshot_path = output_dir / f"{snapshot_name}.png"
    screenshot_base64 = ""
    try:
        screenshot_bytes = await page.screenshot(full_page=True, timeout=10000)
        screenshot_path.write_bytes(screenshot_bytes)
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    except Exception as e:
        logger.warning("Screenshot capture failed: %s", e)

    # Capture performance metrics
    lcp_ms = None
    cls_score = None
    try:
        perf_data = await page.evaluate("""() => {
            const result = {};
            // LCP
            const lcpEntries = performance.getEntriesByType('largest-contentful-paint');
            if (lcpEntries.length > 0) {
                result.lcp = lcpEntries[lcpEntries.length - 1].startTime;
            }
            // CLS
            let clsValue = 0;
            const clsEntries = performance.getEntriesByType('layout-shift');
            for (const entry of clsEntries) {
                if (!entry.hadRecentInput) {
                    clsValue += entry.value;
                }
            }
            result.cls = clsValue;
            return result;
        }""")
        lcp_ms = perf_data.get("lcp")
        cls_score = perf_data.get("cls")
    except Exception as e:
        logger.debug("Performance metrics unavailable: %s", e)

    # Capture visible page text as fallback
    page_text = ""
    try:
        page_text = await page.evaluate("() => document.body.innerText")
        page_text = page_text[:8000]
    except Exception:
        pass

    return PageSnapshot(
        url=url,
        title=title,
        accessibility_tree=a11y_tree,
        screenshot_base64=screenshot_base64,
        screenshot_path=str(screenshot_path),
        console_errors=list(console_errors or []),
        network_error_count=network_error_count,
        load_time_ms=load_time_ms,
        lcp_ms=lcp_ms,
        cls_score=cls_score,
        page_text=page_text,
    )


def snapshot_to_prompt_context(snapshot: PageSnapshot) -> str:
    """Format a PageSnapshot into text suitable for inclusion in an LLM prompt.

    This is the text representation — screenshots are sent separately as vision input.
    """
    parts: list[str] = []

    parts.append(f"URL: {snapshot.url}")
    parts.append(f"Title: {snapshot.title}")

    if snapshot.accessibility_tree and "unavailable" not in snapshot.accessibility_tree:
        # Truncate very large a11y trees
        tree = snapshot.accessibility_tree[:6000]
        parts.append(f"\n## Accessibility Tree\n{tree}")
    elif snapshot.page_text:
        parts.append(f"\n## Page Text\n{snapshot.page_text[:4000]}")

    if snapshot.console_errors:
        errors = "\n".join(snapshot.console_errors[-10:])
        parts.append(f"\n## Console Errors\n{errors}")

    perf_parts: list[str] = []
    if snapshot.load_time_ms:
        perf_parts.append(f"Load time: {snapshot.load_time_ms}ms")
    if snapshot.lcp_ms is not None:
        perf_parts.append(f"LCP: {snapshot.lcp_ms:.0f}ms")
    if snapshot.cls_score is not None:
        perf_parts.append(f"CLS: {snapshot.cls_score:.3f}")
    if snapshot.network_error_count:
        perf_parts.append(f"Network errors: {snapshot.network_error_count}")
    if perf_parts:
        parts.append(f"\n## Performance\n" + " | ".join(perf_parts))

    return "\n".join(parts)
