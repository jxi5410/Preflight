"""Self-learning product memory.

Stores per-URL feedback, dismissed issues, and learned context so that
repeat evaluations of the same product improve over time.

Storage: JSON files under ~/.preflight/memory/<url_key>.json
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class IssueFeedback(BaseModel):
    """User feedback on a single issue from a run."""
    issue_id: str
    issue_title: str
    rating: str  # "valid" | "false_positive" | "wont_fix" | "duplicate"
    comment: str = ""
    severity_override: str = ""  # User can adjust severity
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RunFeedback(BaseModel):
    """Feedback for a complete run."""
    run_id: str
    overall_rating: int = 0  # 1-5 stars
    useful_issues: list[str] = Field(default_factory=list)  # Issue IDs user marked valid
    false_positives: list[str] = Field(default_factory=list)  # Issue IDs dismissed
    comments: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class ProductMemoryData(BaseModel):
    """Persisted memory for a single product URL."""
    url: str
    product_name: str = ""
    run_count: int = 0
    last_run: datetime | None = None
    issue_feedback: list[IssueFeedback] = Field(default_factory=list)
    run_feedback: list[RunFeedback] = Field(default_factory=list)
    known_false_positives: list[str] = Field(default_factory=list)  # Titles/signatures
    custom_guidance: str = ""  # User-provided product-specific guidance
    learned_product_context: str = ""  # Accumulated understanding


# ---------------------------------------------------------------------------
# ProductMemory class
# ---------------------------------------------------------------------------


class ProductMemory:
    """Per-URL learning memory backed by JSON files.

    Usage:
        memory = ProductMemory()
        data = memory.load("https://example.com")
        memory.record_run_feedback(data, run_feedback)
        memory.save(data)
    """

    def __init__(self, base_dir: str | Path | None = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path.home() / ".preflight" / "memory"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _url_key(url: str) -> str:
        """Deterministic filename from URL."""
        normalized = url.rstrip("/").lower()
        h = hashlib.sha256(normalized.encode()).hexdigest()[:12]
        # Sanitize for filesystem: take domain + hash
        domain = normalized.split("://")[-1].split("/")[0].replace(".", "_")
        return f"{domain}_{h}"

    def _path_for(self, url: str) -> Path:
        return self.base_dir / f"{self._url_key(url)}.json"

    def load(self, url: str) -> ProductMemoryData:
        """Load memory for a URL, or return empty if none exists."""
        path = self._path_for(url)
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                return ProductMemoryData(**raw)
            except Exception as e:
                logger.warning("Failed to load memory from %s: %s", path, e)
        return ProductMemoryData(url=url)

    def save(self, data: ProductMemoryData) -> None:
        """Persist memory to disk."""
        path = self._path_for(data.url)
        path.write_text(data.model_dump_json(indent=2))
        logger.debug("Memory saved to %s", path)

    def record_issue_feedback(
        self,
        data: ProductMemoryData,
        issue_id: str,
        issue_title: str,
        rating: str,
        comment: str = "",
        severity_override: str = "",
    ) -> None:
        """Record feedback for a single issue."""
        fb = IssueFeedback(
            issue_id=issue_id,
            issue_title=issue_title,
            rating=rating,
            comment=comment,
            severity_override=severity_override,
        )
        data.issue_feedback.append(fb)

        # Track persistent false positives by title
        if rating == "false_positive" and issue_title not in data.known_false_positives:
            data.known_false_positives.append(issue_title)

    def record_run_feedback(self, data: ProductMemoryData, feedback: RunFeedback) -> None:
        """Record feedback for a complete run."""
        data.run_feedback.append(feedback)
        data.run_count += 1
        data.last_run = feedback.timestamp

    def get_false_positive_titles(self, data: ProductMemoryData) -> list[str]:
        """Get all known false positive issue titles for filtering."""
        return list(data.known_false_positives)

    def get_context_for_prompts(self, data: ProductMemoryData) -> str:
        """Build a context string to inject into LLM prompts.

        Summarizes what we've learned from prior runs:
        - Known false positives to avoid
        - Product-specific guidance
        - Prior feedback patterns
        """
        if data.run_count == 0:
            return ""

        parts: list[str] = []

        parts.append(
            f"This product ({data.product_name or data.url}) has been evaluated "
            f"{data.run_count} time(s) before."
        )

        if data.known_false_positives:
            fp_list = ", ".join(f'"{t}"' for t in data.known_false_positives[:15])
            parts.append(
                f"\nKNOWN FALSE POSITIVES — Do NOT report these again:\n{fp_list}"
            )

        if data.custom_guidance:
            parts.append(f"\nProduct-specific guidance from the user:\n{data.custom_guidance}")

        if data.learned_product_context:
            parts.append(f"\nLearned context from prior runs:\n{data.learned_product_context}")

        # Summarize recent feedback patterns
        valid_count = sum(
            1 for fb in data.issue_feedback if fb.rating == "valid"
        )
        fp_count = sum(
            1 for fb in data.issue_feedback if fb.rating == "false_positive"
        )
        if valid_count + fp_count > 0:
            total = valid_count + fp_count
            accuracy = valid_count / total
            parts.append(
                f"\nPrior accuracy: {accuracy:.0%} of rated issues were valid "
                f"({valid_count} valid, {fp_count} false positives)."
            )
            if accuracy < 0.5:
                parts.append(
                    "Accuracy is LOW — be more conservative, raise confidence thresholds, "
                    "and only report issues you are highly certain about."
                )

        return "\n".join(parts)

    def update_learned_context(self, data: ProductMemoryData, context: str) -> None:
        """Update the accumulated product understanding."""
        data.learned_product_context = context

    def set_custom_guidance(self, data: ProductMemoryData, guidance: str) -> None:
        """Set user-provided product-specific evaluation guidance."""
        data.custom_guidance = guidance

    def reset(self, url: str) -> bool:
        """Delete all memory for a URL. Returns True if memory existed."""
        path = self._path_for(url)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_products(self) -> list[dict[str, Any]]:
        """List all products with stored memory."""
        products = []
        for f in self.base_dir.glob("*.json"):
            try:
                raw = json.loads(f.read_text())
                products.append({
                    "url": raw.get("url", ""),
                    "product_name": raw.get("product_name", ""),
                    "run_count": raw.get("run_count", 0),
                    "last_run": raw.get("last_run"),
                    "false_positives": len(raw.get("known_false_positives", [])),
                    "file": str(f),
                })
            except Exception:
                continue
        return products
