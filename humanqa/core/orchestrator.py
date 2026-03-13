"""Agent Team Orchestrator.

Coordinates agent runs across the product. Assigns journeys, manages coverage map,
minimises redundant exploration, and deduplicates findings across agents.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from humanqa.core.llm import LLMClient
from humanqa.core.schemas import (
    AgentPersona,
    CoverageMap,
    Issue,
    Platform,
    ProductIntentModel,
    RunConfig,
    RunResult,
)
from humanqa.runners.web_runner import WebRunner
from humanqa.runners.mobile_runner import MobileRunner

logger = logging.getLogger(__name__)

JOURNEY_ASSIGNMENT_PROMPT = """You are the test planner for HumanQA.

Given the product's critical journeys and a team of agent personas, assign journeys
to agents so that:
1. Every critical journey is covered by at least one agent
2. Different agents test from their unique perspective
3. Minimize redundant coverage — don't assign the same journey to similar personas
4. Assign mobile-focused journeys to agents with mobile device preference
5. Assign trust/institutional journeys to institutional personas

## Critical Journeys
{journeys}

## Agent Team
{agents}

Respond with JSON: {{"assignments": [{{"agent_id": "...", "journeys": ["...", "..."]}}]}}"""


class Orchestrator:
    """Coordinates the full evaluation run across all agents."""

    def __init__(self, llm: LLMClient, output_dir: str = "./artifacts"):
        self.llm = llm
        self.output_dir = output_dir
        self.web_runner = WebRunner(llm, output_dir)
        self.mobile_runner = MobileRunner(llm, output_dir)

    async def run(
        self,
        config: RunConfig,
        intent: ProductIntentModel,
        agents: list[AgentPersona],
    ) -> RunResult:
        """Execute the full evaluation pipeline."""
        result = RunResult(
            config=config,
            intent_model=intent,
            agents=agents,
            started_at=datetime.now(tz=__import__("datetime").timezone.utc),
        )

        # Step 1: Assign journeys to agents
        assignments = await self._assign_journeys(intent, agents)

        # Step 2: Run web evaluations
        coverage = CoverageMap()
        all_issues: list[Issue] = []

        # Run agents sequentially to avoid overwhelming the target
        # (parallel option available but sequential is safer default)
        for agent in agents:
            agent_journeys = assignments.get(agent.id, intent.critical_journeys[:2])
            agent.assigned_journeys = agent_journeys

            if agent.device_preference in (Platform.web, Platform.mobile_web):
                if agent.device_preference == Platform.mobile_web:
                    issues, coverage = await self.mobile_runner.evaluate_mobile_web(
                        config, agent, agent_journeys, coverage,
                    )
                else:
                    issues, coverage = await self.web_runner.evaluate(
                        config, agent, agent_journeys, coverage,
                    )
                all_issues.extend(issues)
                logger.info(
                    "Agent %s found %d issues on %s",
                    agent.name, len(issues), agent.device_preference.value,
                )

            elif agent.device_preference == Platform.mobile_app:
                issues, coverage = await self.mobile_runner.evaluate_native_app(
                    config, agent, agent_journeys, coverage,
                )
                all_issues.extend(issues)

        # Step 3: Run at least one mobile critical path if not already covered
        mobile_covered = any(
            a.device_preference in (Platform.mobile_web, Platform.mobile_app)
            for a in agents
        )
        if not mobile_covered and agents:
            logger.info("No mobile agent assigned — running mobile critical path with first agent")
            mobile_issues, coverage = await self.mobile_runner.evaluate_mobile_web(
                config, agents[0], intent.critical_journeys[:1], coverage,
            )
            all_issues.extend(mobile_issues)

        # Step 4: Deduplicate
        deduped = self._deduplicate_issues(all_issues)

        # Step 5: Rank by severity and confidence
        ranked = sorted(
            deduped,
            key=lambda i: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(i.severity.value, 5),
                -i.confidence,
            ),
        )

        result.issues = ranked
        result.coverage = coverage
        result.completed_at = datetime.now(tz=__import__("datetime").timezone.utc)

        return result

    async def _assign_journeys(
        self,
        intent: ProductIntentModel,
        agents: list[AgentPersona],
    ) -> dict[str, list[str]]:
        """Use LLM to intelligently assign journeys to agents."""
        if not intent.critical_journeys:
            # No journeys inferred — everyone gets generic exploration
            return {a.id: ["general_exploration"] for a in agents}

        agents_desc = "\n".join(
            f"- {a.id}: {a.name} ({a.persona_type}, {a.device_preference.value}, "
            f"patience={a.patience_level}, expertise={a.expertise_level})"
            for a in agents
        )

        prompt = JOURNEY_ASSIGNMENT_PROMPT.format(
            journeys="\n".join(f"- {j}" for j in intent.critical_journeys),
            agents=agents_desc,
        )

        try:
            data = self.llm.complete_json(prompt)
            assignments_raw = data.get("assignments", [])
            result: dict[str, list[str]] = {}
            for entry in assignments_raw:
                agent_id = entry.get("agent_id", "")
                journeys = entry.get("journeys", [])
                result[agent_id] = journeys
            return result
        except Exception as e:
            logger.warning("Journey assignment failed, using round-robin: %s", e)
            # Fallback: round-robin
            result = {}
            for i, agent in enumerate(agents):
                start = i % len(intent.critical_journeys)
                result[agent.id] = [
                    intent.critical_journeys[start],
                    intent.critical_journeys[(start + 1) % len(intent.critical_journeys)],
                ]
            return result

    def _deduplicate_issues(self, issues: list[Issue]) -> list[Issue]:
        """Remove near-duplicate issues found by multiple agents.

        Keeps the highest-confidence version when duplicates are detected.
        Uses title similarity as a simple heuristic; LLM-based dedup is day-2.
        """
        if not issues:
            return []

        seen: dict[str, Issue] = {}
        for issue in issues:
            # Normalize key: lowercase title, strip whitespace
            key = issue.title.lower().strip()
            # Simple dedup: same title → keep higher confidence
            if key in seen:
                if issue.confidence > seen[key].confidence:
                    # Merge agents info
                    existing_agent = seen[key].agent
                    issue.observed_facts.append(
                        f"Also reported by agent: {existing_agent}"
                    )
                    seen[key] = issue
                else:
                    seen[key].observed_facts.append(
                        f"Also reported by agent: {issue.agent}"
                    )
            else:
                seen[key] = issue

        return list(seen.values())
