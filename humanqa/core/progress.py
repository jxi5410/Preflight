"""Progress tracker for HumanQA pipeline.

Shows a clear visual progress indicator so users know:
- What the overall plan is (all steps)
- Which step is currently running
- How far through the process they are
- What's been found so far
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskID
from rich.table import Table
from rich.live import Live
from rich.text import Text

console = Console()


class PipelineProgress:
    """Visual progress tracker for the evaluation pipeline."""

    STEPS = [
        ("repo", "Analyzing repository", "Understanding what you're building"),
        ("scrape", "Scraping product", "Loading visible product surfaces"),
        ("intent", "Building intent model", "Figuring out what this product is for"),
        ("personas", "Generating test team", "Creating realistic user personas"),
        ("evaluate", "Running evaluation", "Testing as multiple users"),
        ("design", "Design review", "Checking visual quality and polish"),
        ("trust", "Trust assessment", "Verifying trust signals"),
        ("institutional", "Institutional review", "Checking governance and provenance"),
        ("reports", "Generating reports", "Building human and machine reports"),
        ("handoff", "Building handoff", "Preparing tasks for coding tools"),
    ]

    def __init__(self, has_repo: bool = False, has_design: bool = True, has_institutional: bool = True):
        self.has_repo = has_repo
        self.has_design = has_design
        self.has_institutional = has_institutional
        self.issues_found = 0
        self.agents_count = 0
        self.product_name = ""
        self.current_step_idx = -1

        # Filter steps based on config
        self.active_steps = []
        for key, label, desc in self.STEPS:
            if key == "repo" and not has_repo:
                continue
            if key == "design" and not has_design:
                continue
            if key == "institutional" and not has_institutional:
                continue
            self.active_steps.append((key, label, desc))

        self.total_steps = len(self.active_steps)

    def show_plan(self):
        """Show the overall evaluation plan before starting."""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("num", style="dim", width=4)
        table.add_column("step", style="bold")
        table.add_column("detail", style="dim")

        for i, (key, label, desc) in enumerate(self.active_steps, 1):
            table.add_row(f"  {i}.", label, desc)

        panel = Panel(
            table,
            title="[bold]Evaluation Plan[/bold]",
            subtitle=f"[dim]{self.total_steps} steps[/dim]",
            border_style="blue",
        )
        console.print(panel)
        console.print()

    def start_step(self, step_key: str, detail: str = ""):
        """Mark a step as started."""
        # Find step index
        for i, (key, label, desc) in enumerate(self.active_steps):
            if key == step_key:
                self.current_step_idx = i
                step_num = i + 1
                progress_bar = self._make_progress_bar(step_num)
                msg = f"{progress_bar}  [bold cyan]Step {step_num}/{self.total_steps}:[/bold cyan] {label}"
                if detail:
                    msg += f" — [dim]{detail}[/dim]"
                console.print(msg)
                return

    def complete_step(self, step_key: str, result_msg: str = ""):
        """Mark a step as done with optional result."""
        for i, (key, label, desc) in enumerate(self.active_steps):
            if key == step_key:
                if result_msg:
                    console.print(f"         [green]✓[/green] {result_msg}")
                return

    def update_stats(self, issues: int = 0, agents: int = 0, product: str = ""):
        """Update running stats."""
        if issues:
            self.issues_found = issues
        if agents:
            self.agents_count = agents
        if product:
            self.product_name = product

    def show_agent_progress(self, agent_name: str, agent_num: int, total_agents: int, journey: str = ""):
        """Show which agent is currently running."""
        msg = f"         [dim]Agent {agent_num}/{total_agents}:[/dim] {agent_name}"
        if journey:
            msg += f" [dim]→ {journey}[/dim]"
        console.print(msg)

    def show_summary(self, duration: str = ""):
        """Show final summary."""
        console.print()

        # Severity breakdown
        summary_parts = []
        if self.product_name:
            summary_parts.append(f"Product: [bold]{self.product_name}[/bold]")
        if self.agents_count:
            summary_parts.append(f"Tested by: [bold]{self.agents_count} personas[/bold]")
        summary_parts.append(f"Issues found: [bold]{self.issues_found}[/bold]")
        if duration:
            summary_parts.append(f"Duration: [bold]{duration}[/bold]")

        panel = Panel(
            "\n".join(summary_parts),
            title="[bold green]Evaluation Complete[/bold green]",
            border_style="green",
        )
        console.print(panel)

    def _make_progress_bar(self, current: int) -> str:
        """Make a simple text progress bar."""
        filled = current
        empty = self.total_steps - current
        bar = "█" * filled + "░" * empty
        return f"[blue]{bar}[/blue]"
