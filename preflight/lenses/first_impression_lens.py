"""First-Impression Lens — Simulates the first 10 seconds of a new user landing on the product.

Evaluates clarity, trust signals, call-to-action, relevance, and emotional reaction
from each persona's perspective.
"""

from __future__ import annotations

import base64
import logging

from preflight.core.llm import LLMClient
from preflight.core.schemas import (
    AgentPersona,
    FirstImpressionResult,
    Issue,
    IssueCategory,
    PageSnapshot,
    Platform,
    ProductIntentModel,
    Severity,
)

logger = logging.getLogger(__name__)

FIRST_IMPRESSION_SYSTEM_PROMPT = """You are simulating a real user's FIRST IMPRESSION of a product.

You will receive:
- A screenshot of the landing page
- A persona profile (who you are, your goals, expertise, cognitive behavior)
- A product intent model (what the product claims to do)

You must respond AS the persona, in first person. Be honest and specific.

Evaluate these dimensions:
1. **Clarity** (0-10): Can you tell what this product does within 5 seconds?
2. **Trust signals** (0-10): Do you see HTTPS, professional design, social proof, clear pricing, privacy info?
3. **Call to action** (0-10): Is there one clear thing to do? Or are you confused about where to start?
4. **Relevance** (0-10): Based on your goals, does this product feel like it's for you?
5. **Gut reaction**: Your honest first-person reaction ("I landed on this page and my first thought was...")
6. **Would continue**: Would you stick around or bounce?
7. **Time to understand**: How many seconds to understand what this product does?

Also list trust signals found and missing.

Respond with JSON:
{
  "clarity_score": 0-10,
  "clarity_explanation": "...",
  "trust_score": 0-10,
  "trust_signals_found": ["..."],
  "trust_signals_missing": ["..."],
  "cta_score": 0-10,
  "cta_explanation": "...",
  "relevance_score": 0-10,
  "relevance_explanation": "...",
  "gut_reaction": "I landed on this page and...",
  "would_continue": true/false,
  "time_to_understand_seconds": N
}"""

FIRST_IMPRESSION_PROMPT = """You are {persona_name}, a {persona_role}.

## Your Profile
- Expertise: {expertise_level}
- Goals: {persona_goals}
- Attention span: {attention_span}
- Exploration style: {exploration_style}
- Jargon comfort: {jargon_comfort}
- You compare products to: {comparison_anchors}

## What this product claims to be
- Name: {product_name}
- Type: {product_type}
- Target audience: {target_audience}
- Primary jobs: {primary_jobs}

## Landing Page Content
{page_content}

Look at this screenshot and give your HONEST first impression. You have 10 seconds.
Respond in first person as {persona_name}. Be specific about what you see."""


class FirstImpressionLens:
    """Evaluates first impressions of a product landing page per persona."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def evaluate(
        self,
        persona: AgentPersona,
        intent: ProductIntentModel,
        snapshot: PageSnapshot,
    ) -> FirstImpressionResult:
        """Run first-impression evaluation for a single persona."""
        cb = persona.cognitive_behavior

        prompt = FIRST_IMPRESSION_PROMPT.format(
            persona_name=persona.name,
            persona_role=persona.role,
            expertise_level=persona.expertise_level,
            persona_goals=", ".join(persona.goals),
            attention_span=cb.attention_span,
            exploration_style=cb.exploration_style,
            jargon_comfort=cb.jargon_comfort,
            comparison_anchors=", ".join(cb.comparison_anchors) if cb.comparison_anchors else "(none)",
            product_name=intent.product_name,
            product_type=intent.product_type,
            target_audience=", ".join(intent.target_audience),
            primary_jobs=", ".join(intent.primary_jobs),
            page_content=snapshot.page_text[:3000] if snapshot.page_text else "(no text captured)",
        )

        try:
            if snapshot.screenshot_base64:
                screenshot_bytes = base64.b64decode(snapshot.screenshot_base64)
                data = self.llm.complete_json_with_vision(
                    prompt,
                    images=[(screenshot_bytes, "image/png")],
                    system=FIRST_IMPRESSION_SYSTEM_PROMPT,
                )
            else:
                data = self.llm.complete_json(
                    prompt, system=FIRST_IMPRESSION_SYSTEM_PROMPT,
                )

            return FirstImpressionResult(
                persona_id=persona.id,
                clarity_score=int(data.get("clarity_score", 5)),
                clarity_explanation=data.get("clarity_explanation", ""),
                trust_score=int(data.get("trust_score", 5)),
                trust_signals_found=data.get("trust_signals_found", []),
                trust_signals_missing=data.get("trust_signals_missing", []),
                cta_score=int(data.get("cta_score", 5)),
                cta_explanation=data.get("cta_explanation", ""),
                relevance_score=int(data.get("relevance_score", 5)),
                relevance_explanation=data.get("relevance_explanation", ""),
                gut_reaction=data.get("gut_reaction", ""),
                would_continue=data.get("would_continue", True),
                time_to_understand_seconds=int(data.get("time_to_understand_seconds", 10)),
            )
        except Exception as e:
            logger.error("First impression evaluation failed for %s: %s", persona.name, e)
            return FirstImpressionResult(
                persona_id=persona.id,
                clarity_score=5,
                clarity_explanation="Evaluation failed",
                trust_score=5,
                cta_score=5,
                cta_explanation="Evaluation failed",
                relevance_score=5,
                relevance_explanation="Evaluation failed",
                gut_reaction="I couldn't evaluate this page due to a technical error.",
                would_continue=True,
                time_to_understand_seconds=10,
            )

    def results_to_issues(
        self,
        results: list[FirstImpressionResult],
    ) -> list[Issue]:
        """Convert first-impression results into issues for low-scoring dimensions."""
        issues: list[Issue] = []
        for result in results:
            # Flag clarity issues
            if result.clarity_score <= 4:
                issues.append(Issue(
                    title=f"Low first-impression clarity (score: {result.clarity_score}/10)",
                    severity=Severity.high if result.clarity_score <= 2 else Severity.medium,
                    confidence=0.85,
                    platform=Platform.web,
                    category=IssueCategory.ux,
                    agent=result.persona_id,
                    user_impact="Users cannot quickly understand what the product does",
                    observed_facts=[result.clarity_explanation],
                    inferred_judgment=f"Persona gut reaction: {result.gut_reaction}",
                    likely_product_area="Landing Page",
                    repair_brief="Improve above-the-fold messaging clarity",
                ))

            # Flag CTA issues
            if result.cta_score <= 4:
                issues.append(Issue(
                    title=f"Unclear call-to-action (score: {result.cta_score}/10)",
                    severity=Severity.high if result.cta_score <= 2 else Severity.medium,
                    confidence=0.85,
                    platform=Platform.web,
                    category=IssueCategory.ux,
                    agent=result.persona_id,
                    user_impact="Users don't know what to do next on the landing page",
                    observed_facts=[result.cta_explanation],
                    likely_product_area="Landing Page",
                    repair_brief="Add a clear, prominent call-to-action",
                ))

            # Flag bounce risk
            if not result.would_continue:
                issues.append(Issue(
                    title=f"Persona would bounce from landing page",
                    severity=Severity.high,
                    confidence=0.80,
                    platform=Platform.web,
                    category=IssueCategory.ux,
                    agent=result.persona_id,
                    user_impact="This persona type would leave without engaging",
                    observed_facts=[
                        f"Gut reaction: {result.gut_reaction}",
                        f"Time to understand: {result.time_to_understand_seconds}s",
                        f"Relevance score: {result.relevance_score}/10",
                    ],
                    likely_product_area="Landing Page",
                    repair_brief="Improve landing page to retain this user segment",
                ))

        return issues
