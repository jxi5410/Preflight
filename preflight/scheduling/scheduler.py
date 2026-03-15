"""Scheduler — Cron-based overnight and on-demand runs.

Simple APScheduler-based scheduler. Preserves artifacts per run,
supports comparison against prior runs, produces morning summaries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from preflight.core.schemas import RunConfig

logger = logging.getLogger(__name__)


class RunScheduler:
    """Manages scheduled evaluation runs."""

    def __init__(self, base_output_dir: str = "./artifacts"):
        self.base_output_dir = Path(base_output_dir)
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.scheduler = BackgroundScheduler()
        self._jobs: dict[str, dict] = {}

    def schedule(
        self,
        config: RunConfig,
        cron_expression: str = "0 2 * * *",  # Default: 2 AM daily
        job_id: str | None = None,
    ) -> str:
        """Schedule a recurring evaluation run."""
        job_id = job_id or f"preflight-{config.target_url.replace('/', '_')[:40]}"

        # Parse cron expression (minute hour day month day_of_week)
        parts = cron_expression.split()
        trigger = CronTrigger(
            minute=parts[0] if len(parts) > 0 else "0",
            hour=parts[1] if len(parts) > 1 else "2",
            day=parts[2] if len(parts) > 2 else "*",
            month=parts[3] if len(parts) > 3 else "*",
            day_of_week=parts[4] if len(parts) > 4 else "*",
        )

        self.scheduler.add_job(
            func=self._run_job,
            trigger=trigger,
            id=job_id,
            kwargs={"config": config},
            replace_existing=True,
        )

        self._jobs[job_id] = {
            "config": config.model_dump(),
            "cron": cron_expression,
            "created_at": datetime.now(tz=__import__("datetime").timezone.utc).isoformat(),
        }

        # Persist schedule
        self._save_schedule()

        logger.info("Scheduled job %s with cron '%s'", job_id, cron_expression)
        return job_id

    def start(self) -> None:
        """Start the scheduler."""
        self.scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    def stop(self) -> None:
        """Stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")

    def list_jobs(self) -> list[dict]:
        """List all scheduled jobs."""
        return [
            {"job_id": k, **v}
            for k, v in self._jobs.items()
        ]

    def remove_job(self, job_id: str) -> bool:
        """Remove a scheduled job."""
        try:
            self.scheduler.remove_job(job_id)
            self._jobs.pop(job_id, None)
            self._save_schedule()
            return True
        except Exception:
            return False

    def _run_job(self, config: RunConfig) -> None:
        """Execute a scheduled run."""
        # Create timestamped output directory
        timestamp = datetime.now(tz=__import__("datetime").timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_dir = self.base_output_dir / f"run_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Update config output dir
        run_config = config.model_copy(update={"output_dir": str(run_dir)})

        # Run the evaluation pipeline
        # Import here to avoid circular imports
        from preflight.core.pipeline import run_pipeline

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(run_pipeline(run_config))
            loop.close()

            # Save run metadata for comparison
            meta = {
                "run_id": result.run_id,
                "timestamp": timestamp,
                "target": config.target_url,
                "issues_count": len(result.issues),
                "severity_counts": {},
            }
            for issue in result.issues:
                sev = issue.severity.value
                meta["severity_counts"][sev] = meta["severity_counts"].get(sev, 0) + 1

            meta_path = run_dir / "run_meta.json"
            meta_path.write_text(json.dumps(meta, indent=2))

            logger.info(
                "Scheduled run complete: %d issues found, output at %s",
                len(result.issues), run_dir,
            )
        except Exception as e:
            logger.error("Scheduled run failed: %s", e)
            error_path = run_dir / "error.txt"
            error_path.write_text(str(e))

    def _save_schedule(self) -> None:
        """Persist schedule to disk."""
        schedule_path = self.base_output_dir / "schedule.json"
        schedule_path.write_text(json.dumps(self._jobs, indent=2, default=str))

    def load_schedule(self) -> None:
        """Load persisted schedule and re-register jobs."""
        schedule_path = self.base_output_dir / "schedule.json"
        if not schedule_path.exists():
            return

        try:
            data = json.loads(schedule_path.read_text())
            for job_id, info in data.items():
                config = RunConfig(**info["config"])
                self.schedule(config, cron_expression=info["cron"], job_id=job_id)
            logger.info("Loaded %d scheduled jobs from disk", len(data))
        except Exception as e:
            logger.error("Failed to load schedule: %s", e)
