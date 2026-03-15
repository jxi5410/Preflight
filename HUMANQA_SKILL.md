# HumanQA — Claude Code Integration Guide

## What is HumanQA?

HumanQA is an external-experience AI QA system that evaluates web products like real users would. It generates realistic user personas, drives browser-based evaluations, applies specialist lenses (design, trust, auth, responsiveness), and produces evidence-backed reports with developer-ready handoffs.

## MCP Server Setup

Add HumanQA to your Claude Code MCP configuration:

```json
{
  "mcpServers": {
    "humanqa": {
      "command": "humanqa-mcp",
      "env": {
        "GEMINI_API_KEY": "your-gemini-key"
      }
    }
  }
}
```

For alternative LLM providers, set the appropriate environment variable:
- **Gemini (default):** `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- **Anthropic:** `ANTHROPIC_API_KEY`
- **OpenAI:** `OPENAI_API_KEY`

## Available MCP Tools

### `humanqa_quick_check`
**Fast, single-pass evaluation (~30 seconds)**

Use this for:
- Quick feedback during development
- PR review checks
- Smoke testing after deployments
- Initial triage before a full evaluation

Parameters:
- `url` (required): Product URL to evaluate
- `focus`: Optional focus area (e.g. "login flow", "accessibility", "checkout")
- `tier`: Model tier — balanced (default), budget, premium, openai

### `humanqa_evaluate`
**Full multi-agent evaluation (2-5 minutes)**

Use this for:
- Comprehensive QA before releases
- Scheduled quality audits
- CI/CD gate checks
- Deep-dive evaluations

Parameters:
- `url` (required): Product URL to evaluate
- `repo_url`: GitHub repo URL for deeper product understanding
- `brief`: Product description to guide evaluation
- `focus_flows`: Comma-separated flows (e.g. "login,checkout,settings")
- `tier`: Model tier
- `output_dir`: Report output directory (default: ./artifacts)
- `fail_on`: CI gate threshold (critical, high, medium, low)

### `humanqa_get_report`
**Retrieve a previously generated report**

Parameters:
- `run_dir`: Path to artifacts directory (default: ./artifacts)
- `format`: markdown, json, html, or handoff

### `humanqa_compare`
**Compare two runs for regression detection**

Parameters:
- `baseline_dir` (required): Baseline run artifacts path
- `current_dir` (required): Current run artifacts path

## Workflow Examples

### Quick check during development
```
"Run a quick check on http://localhost:3000 focusing on the login flow"
→ Uses humanqa_quick_check with focus="login flow"
```

### Full evaluation with repo context
```
"Run a full HumanQA evaluation on https://myapp.com with repo https://github.com/org/myapp"
→ Uses humanqa_evaluate with repo_url for deeper analysis
```

### Review findings after evaluation
```
"Show me the HumanQA report"
→ Uses humanqa_get_report with format="markdown"
```

### Regression check
```
"Compare the current HumanQA run against last week's baseline in ./baseline"
→ Uses humanqa_compare with baseline_dir and current_dir
```

### CI gate check
```
"Evaluate https://staging.myapp.com and fail if there are any high-severity issues"
→ Uses humanqa_evaluate with fail_on="high"
```

## CLI Commands

HumanQA also works as a standalone CLI:

```bash
# Quick check
humanqa check https://your-product.com
humanqa check https://your-product.com --focus "checkout flow" --json-output

# Full evaluation
humanqa run https://your-product.com --repo https://github.com/user/repo
humanqa run https://your-product.com --tier premium --fail-on high

# Compare runs
humanqa compare ./baseline ./current

# Get handoff for coding agents
humanqa handoff ./artifacts --format claude-code

# Interactive mode
humanqa
```

## Model Tiers

| Tier | Provider | Fast Model | Smart Model | Best For |
|------|----------|-----------|-------------|----------|
| balanced | Gemini | gemini-2.0-flash | gemini-2.0-flash | Default, good cost/quality |
| budget | Gemini | gemini-2.5-flash | gemini-3-flash | Cost-sensitive, high volume |
| premium | Anthropic | claude-sonnet-4 | claude-sonnet-4 | Maximum quality |
| openai | OpenAI | gpt-4.1 | gpt-5.4 | OpenAI ecosystem |
