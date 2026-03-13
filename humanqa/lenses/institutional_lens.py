"""Institutional Lens — Governance, provenance, and auditability review.

Evaluates whether a product meets the standards a serious professional or
institution would require: source verification, provenance, data freshness,
audit trails, role separation, and governance controls.

Operates only on visible product surfaces and captured artifacts.
"""

from __future__ import annotations

import logging

from humanqa.core.llm import LLMClient
from humanqa.core.schemas import (
    Evidence,
    InstitutionalRelevance,
    Issue,
    IssueCategory,
    Platform,
    ProductIntentModel,
    RunResult,
    Severity,
)

logger = logging.getLogger(__name__)

INSTITUTIONAL_SYSTEM = """You are an institutional/governance reviewer for HumanQA.

You evaluate whether a product is trustworthy enough for professional and institutional use.
You look at the product ONLY through its visible UI — never source code.

Your review dimensions:
1. Source verification — Can important outputs be traced to their sources?
2. Provenance — Is it clear where data/content comes from?
3. Data freshness — Are timestamps, update indicators, or freshness markers visible?
4. Fact vs inference vs generated — Does the product distinguish these clearly?
5. Traceability — Can a user reconstruct what happened and why?
6. Audit trail — Are there logs, version history, or change records?
7. Governance controls — Are risky actions gated with approvals, confirmations, or role checks?
8. Role separation — Are admin/user/viewer boundaries clear?
9. Professional trust — Would a risk officer, compliance reviewer, or procurement team accept this?

For each issue, categorize as:
- source_provenance: Missing or inadequate source trails
- data_integrity: Freshness, accuracy, or reliability concerns
- auditability: Missing audit trails, logs, or history
- governance_control: Missing gates, approvals, or role boundaries
- professional_trust: General trustworthiness for institutional adoption

Respond with JSON: {"institutional_issues": [...], "institutional_strengths": [...],
"readiness_level": "not_ready|early|developing|mature", "readiness_summary": "..."}"""

INSTITUTIONAL_PROMPT = """Conduct an institutional/governance review of this product.

## Product
{product_name} ({product_type})
Institutional relevance: {institutional_relevance}
Reasoning: {institutional_reasoning}

## Trust-sensitive actions identified
{trust_actions}

## Issues already found (for context)
{existing_issues}

## Page descriptions from evaluation
{page_descriptions}

For each institutional issue, provide:
- title: Clear issue title
- severity: critical | high | medium | low | info
- confidence: 0.0-1.0
- subcategory: source_provenance | data_integrity | auditability | governance_control | professional_trust
- user_impact: How this affects an institutional user
- observed_facts: What you literally see or don't see (list)
- inferred_judgment: Your governance assessment
- hypotheses: Possible explanations (list)
- likely_product_area: Where in the product
- repair_brief: What to fix for institutional readiness

Also provide:
- institutional_strengths: What governance aspects work well
- readiness_level: "not_ready" | "early" | "developing" | "mature"
- readiness_summary: 2-3 sentence assessment"""


class InstitutionalLens:
    """Governance, provenance, and auditability review."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def should_run(self, intent: ProductIntentModel, override: str = "auto") -> bool:
        """Determine if institutional review is relevant."""
        if override == "on":
            return True
        if override == "off":
            return False
        # Auto: run if relevance is moderate or high
        return intent.institutional_relevance in (
            InstitutionalRelevance.moderate,
            InstitutionalRelevance.high,
        )

    async def review(self, run_result: RunResult) -> list[Issue]:
        """Run institutional review on evaluation results."""
        intent = run_result.intent_model

        if not self.should_run(intent, run_result.config.institutional_review):
            logger.info(
                "Institutional review skipped (relevance: %s)",
                intent.institutional_relevance.value,
            )
            return []

        # Summarize existing issues for context
        existing_summary = "\n".join(
            f"- [{i.severity.value}] {i.title} ({i.category.value})"
            for i in run_result.issues[:20]
        )

        # Page descriptions
        page_descs = "\n".join(
            f"- {e.screen_name or e.url} (status: {e.status})"
            for e in run_result.coverage.entries[:20]
        )

        prompt = INSTITUTIONAL_PROMPT.format(
            product_name=intent.product_name,
            product_type=intent.product_type,
            institutional_relevance=intent.institutional_relevance.value,
            institutional_reasoning=intent.institutional_reasoning,
            trust_actions="\n".join(f"- {a}" for a in intent.trust_sensitive_actions) or "(none)",
            existing_issues=existing_summary or "(none yet)",
            page_descriptions=page_descs or "(none)",
        )

        try:
            data = self.llm.complete_json(prompt, system=INSTITUTIONAL_SYSTEM)

            issues = []
            for raw in data.get("institutional_issues", []):
                sev = raw.get("severity", "medium")
                try:
                    severity = Severity(sev)
                except ValueError:
                    severity = Severity.medium

                issues.append(Issue(
                    title=raw.get("title", "Institutional issue"),
                    severity=severity,
                    confidence=raw.get("confidence", 0.7),
                    platform=Platform.web,
                    category=IssueCategory.institutional_trust,
                    agent="institutional_lens",
                    user_impact=raw.get("user_impact", ""),
                    observed_facts=raw.get("observed_facts", []),
                    inferred_judgment=raw.get("inferred_judgment", ""),
                    hypotheses=raw.get("hypotheses", []),
                    likely_product_area=raw.get("likely_product_area", ""),
                    repair_brief=raw.get("repair_brief", ""),
                ))

            readiness = data.get("readiness_level", "unknown")
            summary = data.get("readiness_summary", "")
            logger.info(
                "Institutional review: %d issues, readiness=%s",
                len(issues), readiness,
            )

            # Store readiness in run scores
            readiness_scores = {
                "not_ready": 0.0,
                "early": 0.25,
                "developing": 0.5,
                "mature": 0.85,
            }
            run_result.scores["institutional_readiness"] = readiness_scores.get(readiness, 0.0)
            run_result.scores["institutional_readiness_label"] = readiness

            return issues

        except Exception as e:
            logger.error("Institutional review failed: %s", e)
            return []
