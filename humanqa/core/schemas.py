"""Core data schemas for HumanQA.

All structured data models: product intent, personas, issues, evidence, reports.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class Platform(str, Enum):
    web = "web"
    mobile_web = "mobile_web"
    mobile_app = "mobile_app"


class IssueCategory(str, Enum):
    functional = "functional"
    ux = "ux"
    ui = "ui"
    performance = "performance"
    trust = "trust"
    institutional_trust = "institutional_trust"
    design = "design"
    accessibility = "accessibility"
    copy = "copy"


class InstitutionalRelevance(str, Enum):
    none = "none"
    low = "low"
    moderate = "moderate"
    high = "high"


# ---------------------------------------------------------------------------
# Run Configuration
# ---------------------------------------------------------------------------

class Credentials(BaseModel):
    email: str | None = None
    password: str | None = None
    token: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class RunConfig(BaseModel):
    """Everything needed to invoke a run."""
    target_url: str
    mobile_target: str | None = None
    credentials: Credentials | None = None
    brief: str | None = None
    persona_hints: list[str] = Field(default_factory=list)
    focus_flows: list[str] = Field(default_factory=list)
    design_guidance: str | None = None
    institutional_review: str = "auto"  # auto | on | off
    design_review: bool = True
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-20250514"
    output_dir: str = "./artifacts"


# ---------------------------------------------------------------------------
# Product Intent Model
# ---------------------------------------------------------------------------

class ProductIntentModel(BaseModel):
    """What the system infers the product is and does."""
    product_name: str = ""
    product_type: str = ""
    target_audience: list[str] = Field(default_factory=list)
    primary_jobs: list[str] = Field(default_factory=list)
    user_expectations: list[str] = Field(default_factory=list)
    critical_journeys: list[str] = Field(default_factory=list)
    trust_sensitive_actions: list[str] = Field(default_factory=list)
    institutional_relevance: InstitutionalRelevance = InstitutionalRelevance.none
    institutional_reasoning: str = ""
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    raw_signals: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent / Persona
# ---------------------------------------------------------------------------

class AgentPersona(BaseModel):
    """A dynamically generated user agent."""
    id: str = Field(default_factory=lambda: f"agent-{uuid.uuid4().hex[:8]}")
    name: str
    role: str
    persona_type: str  # e.g. first_time_user, power_user, risk_compliance_reviewer
    goals: list[str] = Field(default_factory=list)
    expectations: list[str] = Field(default_factory=list)
    patience_level: str = "moderate"  # low | moderate | high
    expertise_level: str = "intermediate"  # novice | intermediate | expert
    behavioral_style: str = ""
    device_preference: Platform = Platform.web
    assigned_journeys: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    screenshots: list[str] = Field(default_factory=list)
    trace: str | None = None
    logs: list[str] = Field(default_factory=list)
    har: str | None = None
    video: str | None = None


# ---------------------------------------------------------------------------
# Issue
# ---------------------------------------------------------------------------

class Issue(BaseModel):
    """A single finding from an evaluation run."""
    id: str = Field(default_factory=lambda: f"ISS-{uuid.uuid4().hex[:6].upper()}")
    title: str
    severity: Severity = Severity.medium
    confidence: float = 0.8
    platform: Platform = Platform.web
    category: IssueCategory = IssueCategory.functional
    agent: str = ""
    user_impact: str = ""
    repro_steps: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""
    observed_facts: list[str] = Field(default_factory=list)
    inferred_judgment: str = ""
    hypotheses: list[str] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
    likely_product_area: str = ""
    repair_brief: str = ""


# ---------------------------------------------------------------------------
# Coverage Map
# ---------------------------------------------------------------------------

class CoverageEntry(BaseModel):
    url: str = ""
    screen_name: str = ""
    agent_id: str = ""
    flow: str = ""
    status: str = "pending"  # pending | visited | failed | skipped
    issues_found: int = 0
    timestamp: datetime | None = None


class CoverageMap(BaseModel):
    entries: list[CoverageEntry] = Field(default_factory=list)

    def visited_urls(self) -> set[str]:
        return {e.url for e in self.entries if e.status == "visited"}

    def failed_urls(self) -> set[str]:
        return {e.url for e in self.entries if e.status == "failed"}

    def pending_flows(self) -> list[str]:
        return list({e.flow for e in self.entries if e.status == "pending"})


# ---------------------------------------------------------------------------
# Run Result
# ---------------------------------------------------------------------------

class RunResult(BaseModel):
    """Complete output of a single evaluation run."""
    run_id: str = Field(default_factory=lambda: f"run-{uuid.uuid4().hex[:8]}")
    started_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None
    config: RunConfig
    intent_model: ProductIntentModel = Field(default_factory=ProductIntentModel)
    agents: list[AgentPersona] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    coverage: CoverageMap = Field(default_factory=CoverageMap)
    summary: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
