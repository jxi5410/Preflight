"""Trust Lens — Trust signal inventory and scorecard.

Systematically checks and catalogs trust signals visible in the product UI:
SSL, privacy policy, terms of service, contact information, error quality,
data handling transparency, and third-party trust indicators.

Reports as a trust scorecard with pass/fail for each signal.
"""

from __future__ import annotations

import logging
from typing import Any

from preflight.core.llm import LLMClient
from preflight.core.schemas import (
    Issue,
    IssueCategory,
    Platform,
    ProductIntentModel,
    RunResult,
    Severity,
    TrustScorecard,
    TrustSignal,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust signal definitions
# ---------------------------------------------------------------------------

TRUST_SIGNALS = [
    {
        "name": "ssl_certificate",
        "label": "SSL certificate (HTTPS)",
        "check_type": "url",
        "description": "Product is served over HTTPS with a valid certificate",
        "severity_if_missing": "critical",
    },
    {
        "name": "privacy_policy",
        "label": "Privacy policy link",
        "check_type": "content",
        "search_terms": ["privacy policy", "privacy notice", "data protection"],
        "description": "A visible, accessible privacy policy link exists",
        "severity_if_missing": "high",
    },
    {
        "name": "terms_of_service",
        "label": "Terms of service",
        "check_type": "content",
        "search_terms": ["terms of service", "terms of use", "terms and conditions", "user agreement"],
        "description": "Terms of service or use are linked and accessible",
        "severity_if_missing": "medium",
    },
    {
        "name": "contact_information",
        "label": "Contact information",
        "check_type": "content",
        "search_terms": [
            "contact us", "contact", "support", "help", "email us",
            "get in touch", "feedback",
        ],
        "description": "Contact information or support channels are visible",
        "severity_if_missing": "medium",
    },
    {
        "name": "error_handling",
        "label": "Error message quality",
        "check_type": "content",
        "search_terms": [
            "error", "failed", "oops", "something went wrong",
            "try again", "unable to",
        ],
        "description": "Error messages are present and helpful (not just generic)",
        "severity_if_missing": "info",  # Absence may mean no errors, which is fine
    },
    {
        "name": "data_handling_transparency",
        "label": "Data handling transparency",
        "check_type": "content",
        "search_terms": [
            "data", "cookie", "consent", "gdpr", "ccpa",
            "how we use", "data policy", "data processing",
        ],
        "description": "The product explains how it handles user data",
        "severity_if_missing": "medium",
    },
    {
        "name": "third_party_trust",
        "label": "Third-party trust indicators",
        "check_type": "content",
        "search_terms": [
            "soc 2", "iso 27001", "hipaa", "gdpr compliant",
            "certified", "verified", "badge", "security",
            "encryption", "secure",
        ],
        "description": "Third-party certifications, badges, or security claims are present",
        "severity_if_missing": "low",
    },
    {
        "name": "company_identity",
        "label": "Company identity visible",
        "check_type": "content",
        "search_terms": [
            "about us", "about", "our team", "our company",
            "who we are", "founded", "© 20",
        ],
        "description": "The company behind the product is identifiable",
        "severity_if_missing": "medium",
    },
]

# ---------------------------------------------------------------------------
# LLM prompt for deeper trust analysis
# ---------------------------------------------------------------------------

TRUST_ANALYSIS_SYSTEM = """You are a trust signal analyst for Preflight.

You evaluate how trustworthy a product appears to new users, enterprise buyers,
and risk-conscious professionals based solely on its visible UI.

## EVIDENCE ANCHORING (MANDATORY)
Every finding must cite specific UI elements, page content, or observed absences.

Respond with JSON: {
  "trust_assessment": "2-3 sentence overall trust impression",
  "additional_signals": [
    {"name": "...", "present": true/false, "details": "...", "evidence": ["..."]}
  ],
  "trust_gaps": [
    {"title": "...", "severity": "...", "user_impact": "...", "observed_facts": ["..."],
     "repair_brief": "..."}
  ]
}"""

TRUST_ANALYSIS_PROMPT = """Analyze the trust signals for this product.

## Product: {product_name} ({product_type})

## Automated Trust Signal Check Results
{signal_results}

## Page content from visited pages
{page_content}

## Coverage
{coverage_summary}

Based on the automated checks above and the page content, provide:
1. An overall trust assessment
2. Any additional trust signals not covered by the automated checks
3. Trust gaps that would concern an enterprise buyer or risk officer"""


class TrustLens:
    """Trust signal inventory and scorecard."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def should_run(self, intent: ProductIntentModel) -> bool:
        """Trust review is useful for all products — always run."""
        return True

    async def review(self, run_result: RunResult) -> tuple[list[Issue], TrustScorecard]:
        """Run trust signal inventory and return issues + scorecard."""
        intent = run_result.intent_model

        # Step 1: Automated trust signal checks
        target_url = run_result.config.target_url
        page_content = self._gather_page_content(run_result)

        signals = self._check_signals(target_url, page_content)
        scorecard = self._build_scorecard(signals)

        # Step 2: Convert missing signals to issues
        signal_issues = self._signals_to_issues(signals)

        # Step 3: LLM-based deeper analysis
        llm_issues = await self._llm_analysis(intent, signals, page_content, run_result)

        all_issues = signal_issues + llm_issues

        # Store scores
        run_result.scores["trust_score"] = scorecard.overall_score
        run_result.scores["trust_signals_present"] = float(
            sum(1 for s in signals if s.present is True)
        )
        run_result.scores["trust_signals_total"] = float(len(signals))

        logger.info(
            "Trust review: %.0f%% signals present, %d issues",
            scorecard.overall_score * 100, len(all_issues),
        )

        return all_issues, scorecard

    def _check_signals(
        self,
        target_url: str,
        page_content: str,
    ) -> list[TrustSignal]:
        """Run automated trust signal checks."""
        content_lower = page_content.lower()
        signals: list[TrustSignal] = []

        for signal_def in TRUST_SIGNALS:
            if signal_def["check_type"] == "url":
                # SSL check: is the URL HTTPS?
                present = target_url.startswith("https://")
                signals.append(TrustSignal(
                    signal_name=signal_def["name"],
                    present=present,
                    details="URL uses HTTPS" if present else "URL does not use HTTPS",
                    evidence=[f"Target URL: {target_url}"],
                ))
            elif signal_def["check_type"] == "content":
                found_terms: list[str] = []
                for term in signal_def.get("search_terms", []):
                    if term.lower() in content_lower:
                        found_terms.append(term)

                present = len(found_terms) > 0
                signals.append(TrustSignal(
                    signal_name=signal_def["name"],
                    present=present,
                    details=(
                        f"Found: {', '.join(found_terms)}"
                        if present
                        else f"Searched for: {', '.join(signal_def.get('search_terms', []))}; none found"
                    ),
                    evidence=(
                        [f"Found indicator(s): {', '.join(found_terms)}"]
                        if present
                        else [f"None of {signal_def.get('search_terms', [])} found in visited pages"]
                    ),
                ))

        return signals

    @staticmethod
    def _build_scorecard(signals: list[TrustSignal]) -> TrustScorecard:
        """Build a trust scorecard from signal check results."""
        if not signals:
            return TrustScorecard(summary="No signals checked")

        present_count = sum(1 for s in signals if s.present is True)
        total = len(signals)
        score = present_count / total if total > 0 else 0.0

        if score >= 0.8:
            summary = f"Strong trust signals: {present_count}/{total} checks passed."
        elif score >= 0.5:
            summary = f"Moderate trust signals: {present_count}/{total} checks passed. Some gaps exist."
        else:
            summary = f"Weak trust signals: {present_count}/{total} checks passed. Significant gaps."

        return TrustScorecard(
            signals=signals,
            overall_score=score,
            summary=summary,
        )

    def _signals_to_issues(self, signals: list[TrustSignal]) -> list[Issue]:
        """Convert missing trust signals to Issue objects."""
        issues: list[Issue] = []

        for signal in signals:
            if signal.present is not False:
                continue

            # Find severity from definition
            signal_def = next(
                (s for s in TRUST_SIGNALS if s["name"] == signal.signal_name),
                None,
            )
            if not signal_def:
                continue

            sev_str = signal_def.get("severity_if_missing", "medium")
            try:
                severity = Severity(sev_str)
            except ValueError:
                severity = Severity.medium

            label = signal_def.get("label", signal.signal_name)

            issues.append(Issue(
                title=f"Missing trust signal: {label}",
                severity=severity,
                confidence=0.9,
                platform=Platform.web,
                category=IssueCategory.trust,
                agent="trust_lens",
                user_impact=f"Users and enterprise buyers expect {label.lower()} to be visible",
                observed_facts=signal.evidence,
                inferred_judgment=f"{label} was not found on any visited page",
                likely_product_area="Trust & Compliance",
                repair_brief=signal_def.get("description", f"Add {label.lower()}"),
            ))

        return issues

    async def _llm_analysis(
        self,
        intent: ProductIntentModel,
        signals: list[TrustSignal],
        page_content: str,
        run_result: RunResult,
    ) -> list[Issue]:
        """Use LLM for deeper trust analysis beyond automated checks."""
        signal_text = "\n".join(
            f"[{'PRESENT' if s.present else 'MISSING'}] {s.signal_name}: {s.details}"
            for s in signals
        )

        coverage_summary = f"{len(run_result.coverage.entries)} pages visited"

        prompt = TRUST_ANALYSIS_PROMPT.format(
            product_name=intent.product_name,
            product_type=intent.product_type,
            signal_results=signal_text,
            page_content=page_content[:6000],
            coverage_summary=coverage_summary,
        )

        try:
            data = self.llm.complete_json(prompt, system=TRUST_ANALYSIS_SYSTEM)
            issues: list[Issue] = []

            for gap in data.get("trust_gaps", []):
                sev_str = gap.get("severity", "medium")
                try:
                    severity = Severity(sev_str)
                except ValueError:
                    severity = Severity.medium

                issues.append(Issue(
                    title=gap.get("title", "Trust gap"),
                    severity=severity,
                    confidence=0.75,
                    platform=Platform.web,
                    category=IssueCategory.trust,
                    agent="trust_lens",
                    user_impact=gap.get("user_impact", ""),
                    observed_facts=gap.get("observed_facts", []),
                    inferred_judgment=gap.get("title", ""),
                    likely_product_area="Trust & Compliance",
                    repair_brief=gap.get("repair_brief", ""),
                ))

            return issues

        except Exception as e:
            logger.warning("LLM trust analysis failed: %s", e)
            return []

    @staticmethod
    def _gather_page_content(run_result: RunResult) -> str:
        """Gather text content from run results for trust signal analysis."""
        parts: list[str] = []
        for entry in run_result.coverage.entries[:20]:
            parts.append(f"Page: {entry.screen_name or entry.url} (status: {entry.status})")
        for issue in run_result.issues[:30]:
            for fact in issue.observed_facts:
                parts.append(fact)
        return "\n".join(parts) if parts else ""
