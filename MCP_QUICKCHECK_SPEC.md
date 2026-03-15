# MCP Server & Quick-Check Mode Spec

**Purpose:** Make Preflight callable from inside any AI coding tool (Claude Code, Cursor, Codex) via MCP, and add a fast single-persona evaluation mode for mid-development spot checks.

---

## Part 1: MCP Server

### What It Does

Preflight runs as an MCP (Model Context Protocol) server that AI coding tools can discover and call. When a developer inside Claude Code says "check my deploy" or "run preflight on staging", the coding tool calls Preflight's MCP tools directly. No context switching, no separate terminal.

### MCP Tools to Expose

#### Tool 1: `preflight_evaluate`
Full evaluation run. This is the same as `preflight run` but callable from any MCP client.

```json
{
  "name": "preflight_evaluate",
  "description": "Run a full Preflight evaluation against a product URL. Analyzes the repo for product understanding, generates realistic user personas, evaluates the product through the UI, and returns prioritized findings with evidence.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "url": {"type": "string", "description": "Product URL to evaluate"},
      "repo_url": {"type": "string", "description": "GitHub repo URL for product context (optional)"},
      "brief": {"type": "string", "description": "Brief product description (optional)"},
      "focus_flows": {"type": "array", "items": {"type": "string"}, "description": "Specific flows to test (optional)"},
      "tier": {"type": "string", "enum": ["balanced", "budget", "premium", "openai"], "default": "balanced"}
    },
    "required": ["url"]
  }
}
```

Returns: Full report summary + issue list + handoff tasks as structured JSON.

#### Tool 2: `preflight_quick_check`
Fast single-persona evaluation. Takes < 2 minutes. For mid-development "does this look right?" checks.

```json
{
  "name": "preflight_quick_check",
  "description": "Quick single-persona evaluation of a URL. Fast spot check (~1-2 minutes) for mid-development use. Checks basic functionality, visual quality, and common-sense issues.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "url": {"type": "string", "description": "URL to check"},
      "focus": {"type": "string", "description": "What to focus on, e.g. 'login page', 'checkout flow', 'mobile layout'"},
      "mobile": {"type": "boolean", "default": false, "description": "Check as mobile user"}
    },
    "required": ["url"]
  }
}
```

Returns: 3-5 quick findings with screenshots, no full report.

#### Tool 3: `preflight_get_report`
Retrieve the latest report or a specific run's report.

```json
{
  "name": "preflight_get_report",
  "description": "Get the latest Preflight evaluation report, or a specific run's report.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "run_id": {"type": "string", "description": "Specific run ID (optional, defaults to latest)"},
      "format": {"type": "string", "enum": ["summary", "full", "handoff"], "default": "summary"}
    }
  }
}
```

#### Tool 4: `preflight_compare`
Compare two runs to see what changed.

```json
{
  "name": "preflight_compare",
  "description": "Compare two Preflight runs. Shows new issues, resolved issues, and regressions.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "baseline_run": {"type": "string", "description": "Path or run ID of baseline"},
      "current_run": {"type": "string", "description": "Path or run ID of current run"}
    },
    "required": ["baseline_run", "current_run"]
  }
}
```

### Implementation

#### File: `preflight/mcp_server.py`

Use the `mcp` Python SDK (https://github.com/modelcontextprotocol/python-sdk).

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("preflight")

@server.list_tools()
async def list_tools():
    return [
        Tool(name="preflight_evaluate", description="...", inputSchema={...}),
        Tool(name="preflight_quick_check", description="...", inputSchema={...}),
        Tool(name="preflight_get_report", description="...", inputSchema={...}),
        Tool(name="preflight_compare", description="...", inputSchema={...}),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "preflight_evaluate":
        return await handle_evaluate(arguments)
    elif name == "preflight_quick_check":
        return await handle_quick_check(arguments)
    # ... etc

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write)
```

#### How Users Connect It

For Claude Code, the user adds to their Claude Code MCP config:

```json
{
  "mcpServers": {
    "preflight": {
      "command": "python3",
      "args": ["-m", "preflight.mcp_server"],
      "env": {
        "GOOGLE_API_KEY": "..."
      }
    }
  }
}
```

For Cursor, similar MCP config in Cursor settings.

Then inside their coding session:
```
> Check my staging deploy at https://staging.myapp.com
(Claude Code calls preflight_quick_check automatically)

> Run a full evaluation with repo context
(Claude Code calls preflight_evaluate with the repo URL)

> What changed since last run?
(Claude Code calls preflight_compare)
```

### Dependencies

Add to `pyproject.toml`:
```
"mcp>=1.0.0",
```

### CLI Entry Point

Add to `pyproject.toml` scripts:
```
preflight-mcp = "preflight.mcp_server:main"
```

Also support: `python3 -m preflight.mcp_server`

---

## Part 2: Quick-Check Mode

### What It Does

A fast, lightweight evaluation that takes < 2 minutes. One persona, one viewport, 3-5 steps max. For mid-development use when the developer wants a quick sanity check, not a full report.

### How It Differs from Full Evaluation

| Aspect | Full Run | Quick Check |
|--------|----------|-------------|
| Personas | 4-8 | 1 (smart first-time user) |
| Steps per journey | Up to 10 | Max 3 |
| Journeys | All critical | 1 focus area or auto |
| Lenses | Design + Trust + Institutional | None (just core evaluation) |
| Report | Full markdown + HTML + JSON + handoff | Short text summary + screenshots |
| Repo analysis | Yes (if provided) | No |
| Time | 5-15 minutes | 1-2 minutes |
| Cost | $0.50-0.80 | $0.05-0.15 |

### Implementation

#### File: `preflight/core/quick_check.py`

```python
async def quick_check(
    url: str,
    focus: str | None = None,
    mobile: bool = False,
    tier: str = "balanced",
) -> QuickCheckResult:
    """Fast single-persona evaluation."""
    llm = LLMClient(tier=tier)
    
    # 1. Scrape the target page (no repo analysis)
    web_runner = WebRunner(llm, output_dir)
    page_content = await web_runner.scrape_landing_page(url)
    
    # 2. Quick intent (single LLM call, fast tier)
    # Just ask: what is this page and what should work?
    
    # 3. Single persona: "Smart First-Time User"
    # Hard-coded, not LLM-generated. Saves a call.
    
    # 4. Evaluate the page (1-3 steps with vision)
    # Navigate to URL, optionally navigate to focus area, evaluate
    
    # 5. Return QuickCheckResult
```

#### Schema: `QuickCheckResult`

```python
class QuickCheckResult(BaseModel):
    url: str
    checked_at: datetime
    focus: str | None = None
    mobile: bool = False
    page_title: str = ""
    findings: list[QuickFinding] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    overall_impression: str = ""  # 1-2 sentence summary
    
class QuickFinding(BaseModel):
    title: str
    severity: str  # critical | high | medium | low
    description: str
    screenshot: str | None = None
```

#### CLI Integration

```bash
# Quick check via CLI
preflight check https://staging.myapp.com
preflight check https://staging.myapp.com --focus "login page"
preflight check https://staging.myapp.com --mobile

# Short output, not a full report:
# 
# Quick Check: https://staging.myapp.com
# Checked at: 2026-03-14 21:00 UTC
# 
# ✓ Page loads (1.2s)
# ✓ Navigation visible and functional
# ✗ [HIGH] Login form has no error handling — submitting empty form shows no feedback
# ✗ [MEDIUM] Footer links to /privacy return 404
# 
# Overall: Page works but has gaps in error handling and legal pages.
# Screenshots saved to ./artifacts/quick-check/
```

#### Interactive Mode

When the user runs `preflight` with no args, after the full evaluation option, also offer quick check:

```
Welcome to Preflight — your team of AI QA companions

What would you like to do?
  1. Full evaluation — comprehensive multi-persona review
  2. Quick check — fast spot check (1-2 minutes)

Choose [1-2]:
```

---

## Part 3: Claude Code Skill (Thin Layer)

### File: `HUMANQA_SKILL.md`

This file can be placed in a project's `.claude/` directory or referenced as a skill. It teaches Claude Code about Preflight and when/how to use it.

```markdown
# Preflight Skill

Preflight is a QA companion that evaluates products through the UI like real users would.

## When to Use
- After deploying changes: "check my staging deploy"
- Before merging a PR: "quick check the preview"
- For full reviews: "run a full QA evaluation"
- To compare runs: "what changed since last evaluation?"

## Available Tools (via MCP)
- preflight_quick_check: Fast 1-2 minute spot check
- preflight_evaluate: Full multi-persona evaluation (5-15 min)
- preflight_get_report: Retrieve latest findings
- preflight_compare: Diff between runs

## Usage Patterns
For quick checks, prefer preflight_quick_check. Only use preflight_evaluate for milestone reviews.
When presenting results, format as actionable tasks the developer can address.
If the developer says "fix these", generate implementation code based on the handoff tasks.
```

---

## Build Order

1. `preflight/core/quick_check.py` — QuickCheckResult schema + quick_check function
2. CLI `preflight check` command
3. Update interactive mode with quick check option
4. `preflight/mcp_server.py` — MCP server with all 4 tools
5. Wire quick_check into MCP `preflight_quick_check` tool
6. Wire full pipeline into MCP `preflight_evaluate` tool  
7. Wire report retrieval into MCP `preflight_get_report` tool
8. Wire comparison into MCP `preflight_compare` tool
9. Add `preflight-mcp` entry point to pyproject.toml
10. Create `HUMANQA_SKILL.md` for Claude Code integration
11. Add MCP config example to README
12. Tests for quick_check and MCP server
13. Push to origin

## Dependencies to Add

```toml
"mcp>=1.0.0",
```

## Key Constraints

- Quick check must complete in < 2 minutes. Hard timeout of 120 seconds.
- MCP server must handle concurrent calls gracefully (each tool call runs the pipeline in its own context).
- MCP server should return structured text that the coding tool can parse, not raw JSON blobs.
- Quick check uses only the fast tier model. No smart tier calls.
- Quick check does NOT generate reports, handoff files, or HTML. Just returns findings as text.
