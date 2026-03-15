"""Mobile Runner — Mobile evaluation for critical paths.

Uses Maestro for native app testing when available, falls back to
Playwright mobile emulation for mobile web evaluation.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from preflight.core.llm import LLMClient
from preflight.core.schemas import (
    AgentPersona,
    CoverageMap,
    Issue,
    Platform,
    RunConfig,
)
from preflight.runners.web_runner import WebRunner

logger = logging.getLogger(__name__)


class MobileRunner:
    """Runs mobile evaluation — Maestro for native, Playwright emulation for mobile web."""

    def __init__(self, llm: LLMClient, output_dir: str = "./artifacts"):
        self.llm = llm
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._maestro_available = shutil.which("maestro") is not None

    @property
    def has_maestro(self) -> bool:
        return self._maestro_available

    async def evaluate_mobile_web(
        self,
        config: RunConfig,
        persona: AgentPersona,
        journeys: list[str],
        coverage: CoverageMap,
    ) -> tuple[list[Issue], CoverageMap]:
        """Evaluate using Playwright mobile emulation."""
        # Force mobile viewport via persona
        mobile_persona = persona.model_copy(
            update={"device_preference": Platform.mobile_web}
        )

        web_runner = WebRunner(self.llm, str(self.output_dir))
        return await web_runner.evaluate(config, mobile_persona, journeys, coverage)

    async def evaluate_native_app(
        self,
        config: RunConfig,
        persona: AgentPersona,
        journeys: list[str],
        coverage: CoverageMap,
    ) -> tuple[list[Issue], CoverageMap]:
        """Evaluate native mobile app via Maestro (if available)."""
        if not self.has_maestro:
            logger.warning(
                "Maestro not installed. Falling back to mobile web emulation. "
                "Install Maestro for native app testing: https://maestro.mobile.dev"
            )
            return await self.evaluate_mobile_web(config, persona, journeys, coverage)

        # Generate Maestro flow from journey descriptions
        issues: list[Issue] = []
        for journey in journeys:
            flow_file = self._generate_maestro_flow(persona, journey, config)
            if flow_file:
                result = self._run_maestro_flow(flow_file)
                if result:
                    issues.extend(result)

        return issues, coverage

    def _generate_maestro_flow(
        self,
        persona: AgentPersona,
        journey: str,
        config: RunConfig,
    ) -> Path | None:
        """Use LLM to generate a Maestro YAML flow for a journey."""
        prompt = f"""Generate a Maestro mobile test flow for this journey.

Persona: {persona.name} ({persona.role})
Journey: {journey}
App target: {config.mobile_target or config.target_url}

Respond with valid Maestro YAML flow syntax. Include:
- appId or URL launch
- Tap, type, scroll, assert actions
- Screenshot capture steps

Respond with ONLY the YAML content, no markdown fences."""

        try:
            yaml_content = self.llm.complete(prompt, tier="fast")
            flow_path = self.output_dir / f"maestro-{persona.id}-{journey[:20]}.yaml"
            flow_path.write_text(yaml_content)
            return flow_path
        except Exception as e:
            logger.error("Failed to generate Maestro flow: %s", e)
            return None

    def _run_maestro_flow(self, flow_file: Path) -> list[Issue]:
        """Execute a Maestro flow file and parse results."""
        try:
            result = subprocess.run(
                ["maestro", "test", str(flow_file), "--format", "json"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.warning("Maestro flow failed: %s", result.stderr[:500])
                return [Issue(
                    title=f"Mobile flow failed: {flow_file.stem}",
                    severity="high",
                    platform=Platform.mobile_app,
                    category="functional",
                    observed_facts=[result.stderr[:500]],
                    user_impact="Mobile critical path could not be completed",
                )]
            return []
        except subprocess.TimeoutExpired:
            return [Issue(
                title=f"Mobile flow timed out: {flow_file.stem}",
                severity="high",
                platform=Platform.mobile_app,
                category="performance",
                user_impact="Mobile flow did not complete within timeout",
            )]
        except Exception as e:
            logger.error("Maestro execution error: %s", e)
            return []
