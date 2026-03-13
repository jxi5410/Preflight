"""Agent Team Orchestrator.

Coordinates agent runs across the product. Assigns journeys, manages coverage map,
minimises redundant exploration, and deduplicates findings across agents.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from humanqa.core.llm import LLMClient
from humanqa.core.performance import (
    evaluate_snapshot_performance,
    performance_results_to_issues,
    summarize_performance,
)
from humanqa.core.schemas import (
    AgentPersona,
    CoverageMap,
    Issue,
    PageSnapshot,
    Platform,
    ProductIntentModel,
    RunConfig,
    RunResult,
)
from humanqa.runners.web_runner import WebRunner
from humanqa.runners.mobile_runner import MobileRunner

logger = logging.getLogger(__name__)

DEDUP_SYSTEM_PROMPT = """You are a deduplication engine for HumanQA.

Your job is to cluster semantically similar issues from multiple QA agents.
Two issues are duplicates if they describe the same underlying problem, even if:
- They use different wording
- They were found by different agents
- They have different severity levels
- One is more specific than the other

Two issues are NOT duplicates if they:
- Affect different pages or flows
- Describe different root causes
- Are in different categories AND about different UI elements

Respond with JSON: {"clusters": [{"indices": [0, 3], "reason": "Both describe the same missing form validation"}]}

Each issue index should appear in at most one cluster. Issues that are unique should NOT appear in any cluster."""

DEDUP_PROMPT = """Cluster these issues by semantic similarity. Group duplicates together.

## Issues
{issue_list}

Return clusters of duplicate issues. Each cluster should contain the indices of issues
that describe the same underlying problem. Issues with no duplicates should be omitted."""

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
        self._collected_snapshots: list[PageSnapshot] = []

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

        # Step 4: Evaluate performance budgets from collected snapshots
        perf_issues, perf_scores = self._evaluate_performance(
            self._collected_snapshots, intent.product_type,
        )
        all_issues.extend(perf_issues)

        # Step 5: Deduplicate
        deduped = self._deduplicate_issues(all_issues)

        # Step 6: Rank by severity and confidence
        ranked = sorted(
            deduped,
            key=lambda i: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(i.severity.value, 5),
                -i.confidence,
            ),
        )

        result.issues = ranked
        result.coverage = coverage
        result.scores.update(perf_scores)
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

    def add_snapshots(self, snapshots: list[PageSnapshot]) -> None:
        """Register snapshots collected during evaluation for performance analysis."""
        self._collected_snapshots.extend(snapshots)

    def _evaluate_performance(
        self,
        snapshots: list[PageSnapshot],
        product_type: str,
    ) -> tuple[list[Issue], dict]:
        """Evaluate performance metrics from collected snapshots against budgets."""
        if not snapshots:
            return [], {}

        all_perf_results = []
        all_perf_issues: list[Issue] = []
        seen_urls: set[str] = set()

        for snapshot in snapshots:
            # Evaluate each unique URL only once (use first snapshot per URL)
            if snapshot.url in seen_urls:
                continue
            seen_urls.add(snapshot.url)

            results = evaluate_snapshot_performance(snapshot, product_type)
            all_perf_results.extend(results)
            issues = performance_results_to_issues(results, snapshot)
            all_perf_issues.extend(issues)

        scores = summarize_performance(all_perf_results) if all_perf_results else {}
        return all_perf_issues, scores

    def _deduplicate_issues(self, issues: list[Issue]) -> list[Issue]:
        """Remove near-duplicate issues using LLM-based semantic clustering.

        Sends issues to the LLM in batches for semantic similarity grouping.
        For each cluster, keeps the highest-confidence version and annotates
        with "also found by" info. Falls back to title-string matching if
        LLM dedup fails.
        """
        if not issues:
            return []

        # Small lists don't need LLM dedup
        if len(issues) <= 3:
            return self._deduplicate_by_title(issues)

        try:
            return self._deduplicate_with_llm(issues)
        except Exception as e:
            logger.warning("LLM dedup failed, falling back to title matching: %s", e)
            return self._deduplicate_by_title(issues)

    def _deduplicate_with_llm(self, issues: list[Issue]) -> list[Issue]:
        """Use LLM to cluster semantically similar issues."""
        # Process in batches of 30 to stay within token limits
        batch_size = 30
        all_deduped: list[Issue] = []

        for batch_start in range(0, len(issues), batch_size):
            batch = issues[batch_start:batch_start + batch_size]
            if len(batch) <= 1:
                all_deduped.extend(batch)
                continue

            issue_summaries = []
            for i, issue in enumerate(batch):
                issue_summaries.append(
                    f'{i}: [{issue.severity.value}] "{issue.title}" '
                    f'(agent={issue.agent}, category={issue.category.value}, '
                    f'confidence={issue.confidence})'
                )

            prompt = DEDUP_PROMPT.format(
                issue_list="\n".join(issue_summaries),
            )

            data = self.llm.complete_json(prompt, system=DEDUP_SYSTEM_PROMPT)
            clusters = data.get("clusters", [])

            if not clusters:
                # LLM returned no clusters — keep all
                all_deduped.extend(batch)
                continue

            used_indices: set[int] = set()
            for cluster in clusters:
                indices = cluster.get("indices", [])
                if not indices:
                    continue

                # Validate indices
                valid_indices = [idx for idx in indices if 0 <= idx < len(batch)]
                if not valid_indices:
                    continue

                # Pick the highest-confidence issue as the representative
                cluster_issues = [batch[idx] for idx in valid_indices]
                best = max(cluster_issues, key=lambda x: x.confidence)

                # Annotate with other agents who found similar issues
                other_agents = [
                    ci.agent for ci in cluster_issues
                    if ci.id != best.id and ci.agent != best.agent
                ]
                for agent_name in other_agents:
                    best.observed_facts.append(f"Also reported by agent: {agent_name}")

                all_deduped.append(best)
                used_indices.update(valid_indices)

            # Add any issues not included in any cluster
            for i, issue in enumerate(batch):
                if i not in used_indices:
                    all_deduped.append(issue)

        return all_deduped

    @staticmethod
    def _deduplicate_by_title(issues: list[Issue]) -> list[Issue]:
        """Fallback dedup: group by normalized title string."""
        if not issues:
            return []

        seen: dict[str, Issue] = {}
        for issue in issues:
            key = issue.title.lower().strip()
            if key in seen:
                if issue.confidence > seen[key].confidence:
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
