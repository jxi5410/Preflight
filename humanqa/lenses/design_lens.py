"""Design Lens — Specialist design critique.

Evaluates UI/design quality from captured screenshots and page content.
Assesses hierarchy, spacing, readability, CTA prominence, visual polish,
consistency, and brand coherence. No code inspection.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from humanqa.core.llm import LLMClient
from humanqa.core.schemas import (
    Evidence,
    Issue,
    IssueCategory,
    Platform,
    ProductIntentModel,
    RunResult,
    Severity,
)

logger = logging.getLogger(__name__)

DESIGN_REVIEW_SYSTEM = """You are a senior product designer conducting a design review for HumanQA.

You evaluate products from their visible UI only — screenshots and page descriptions.
You never reference source code.

Assess across these dimensions:
1. Visual hierarchy — Is it clear what's most important?
2. Spacing and alignment — Is layout consistent and well-structured?
3. Readability — Is text legible, well-sized, properly contrasted?
4. CTA prominence — Are calls to action obvious and well-placed?
5. Visual polish — Does it look professional or rough/unfinished?
6. Consistency — Are patterns, colors, typography consistent across screens?
7. Brand coherence — Does the visual language match the product's positioning?
8. Mobile ergonomics — Are touch targets adequate? Is content usable on small screens?
9. Component states — Do interactive elements show proper hover/active/disabled states?
10. Information density — Is the density appropriate for the audience?

## EVIDENCE ANCHORING (MANDATORY)

Every finding MUST cite specific evidence. Findings without anchored evidence will be rejected.

Each finding must reference at least ONE of:
- **Screenshot reference**: "In screenshot {filename}, [specific observation]"
- **Element reference**: A specific UI element with measurable detail
  Example: "The submit button has 8px padding on a 390px viewport"
- **Measurement**: A quantifiable observation
  Example: "Heading text is 12px on desktop, below readable minimum of 16px"
- **Observed absence**: An explicit negative observation
  Example: "No hover state visible on any interactive element in the navigation bar"

Bad (will be rejected):
- "The design feels cluttered" (no specific element)
- "Colors don't work well" (no specific reference)

Good:
- "In screenshot hero-page.png, the primary CTA 'Get Started' uses the same visual weight as secondary links, reducing its prominence"
- "The navigation bar has 6 items plus a dropdown, but on the 390px mobile viewport they overflow without a hamburger menu"

Severity scale:
- critical: unusable (blocks core function)
- high: significantly hurts experience
- medium: noticeable quality issue
- low: polish item
- info: suggestion

Respond with JSON: {"design_issues": [...], "design_strengths": [...], "overall_assessment": "..."}"""

DESIGN_REVIEW_PROMPT = """Review the design quality of this product.

## Product
{product_name} ({product_type})
Target audience: {target_audience}

## Screenshots available
{screenshot_list}

## Page descriptions from evaluation
{page_descriptions}

## Design guidance (if provided)
{design_guidance}

For each issue found, provide:
- title: Clear design issue title
- severity: critical | high | medium | low | info
- confidence: 0.0-1.0
- user_impact: How this affects real users
- observed_facts: What you literally see (list)
- inferred_judgment: Your design assessment
- likely_product_area: Where in the product
- repair_brief: What to fix

Also provide:
- design_strengths: What's working well (list of strings)
- overall_assessment: 2-3 sentence summary of design quality"""


class DesignLens:
    """Specialist design review from captured artifacts."""

    def __init__(self, llm: LLMClient, output_dir: str = "./artifacts"):
        self.llm = llm
        self.output_dir = Path(output_dir)

    async def review(
        self,
        run_result: RunResult,
        design_guidance: str | None = None,
    ) -> list[Issue]:
        """Run design review on collected artifacts from a run."""
        intent = run_result.intent_model

        # Gather screenshot references
        screenshots = []
        for issue in run_result.issues:
            screenshots.extend(issue.evidence.screenshots)
        # Also check artifact dir for additional screenshots
        if self.output_dir.exists():
            for f in self.output_dir.glob("*.png"):
                if f.name not in screenshots:
                    screenshots.append(f.name)

        # Build page descriptions from coverage and issues
        page_descs = []
        for entry in run_result.coverage.entries:
            page_descs.append(
                f"- {entry.screen_name or entry.url} (status: {entry.status}, "
                f"issues: {entry.issues_found})"
            )

        prompt = DESIGN_REVIEW_PROMPT.format(
            product_name=intent.product_name,
            product_type=intent.product_type,
            target_audience=", ".join(intent.target_audience),
            screenshot_list="\n".join(f"- {s}" for s in screenshots[:20]) or "(none captured)",
            page_descriptions="\n".join(page_descs[:30]) or "(none)",
            design_guidance=design_guidance or "(none provided)",
        )

        try:
            data = self.llm.complete_json(prompt, system=DESIGN_REVIEW_SYSTEM)

            design_issues = []
            for raw in data.get("design_issues", []):
                sev = raw.get("severity", "medium")
                try:
                    severity = Severity(sev)
                except ValueError:
                    severity = Severity.medium

                design_issues.append(Issue(
                    title=raw.get("title", "Design issue"),
                    severity=severity,
                    confidence=raw.get("confidence", 0.7),
                    platform=Platform.web,
                    category=IssueCategory.design,
                    agent="design_lens",
                    user_impact=raw.get("user_impact", ""),
                    observed_facts=raw.get("observed_facts", []),
                    inferred_judgment=raw.get("inferred_judgment", ""),
                    likely_product_area=raw.get("likely_product_area", ""),
                    repair_brief=raw.get("repair_brief", ""),
                ))

            logger.info(
                "Design review complete: %d issues, %d strengths noted",
                len(design_issues),
                len(data.get("design_strengths", [])),
            )
            return design_issues

        except Exception as e:
            logger.error("Design review failed: %s", e)
            return []
