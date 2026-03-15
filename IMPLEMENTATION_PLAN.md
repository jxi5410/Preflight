# Preflight — Implementation Plan for Codex

**Date:** 2026-03-13
**Status:** Active build plan. Supersedes original PRD build order.
**Who executes:** Codex (or Claude Code)
**Repo:** https://github.com/jxi5410/Preflight

---

## Product Vision (Updated)

Preflight is a **team of AI companions** for developers — not a test automation tool.

It reads your repo to understand what you're building, then evaluates your shipped product like a diverse team of real humans would. It exists because AI is enabling more solo builders and small teams who have good intuitions about what real human testers would notice, but lack the time and QA structure to do it themselves.

**Core positioning:** The only tool that knows what you're *trying* to build (from your repo) and checks whether you actually built it (from the UI), through the eyes of multiple realistic users.

### What Preflight is NOT
- Not a code reviewer (leave that to Claude Code / Codex)
- Not a test automation framework (leave that to Playwright / Cypress)
- Not an auto-healing test suite (leave that to mabl / QA Wolf)

### What Preflight IS
- A team of realistic synthetic users who understand your product
- An outside-in product judgment system
- A companion that runs alongside development, not a post-ship batch tool

---

## The Repo Boundary — Critical Design Decision

Preflight reads the repo to **understand product intent**. It never judges code quality.

### What the Repo Analyzer MAY read:
- README and docs/
- Package manifest (package.json, pyproject.toml, Cargo.toml, etc.)
- Route/page structure (file names and directory layout, not implementations)
- Recent issues and PRs (titles and descriptions, not diffs)
- Configuration files that reveal features (env.example, config files)
- CHANGELOG or release notes

### What the Repo Analyzer must NEVER do:
- Judge code quality, patterns, or architecture
- Flag implementation issues (e.g., "no rate limiting in auth.py")
- Suggest code refactors
- Analyze test coverage or test code
- Read function bodies to assess implementation correctness

### The rule:
> Read code to understand what the product **should do**. Evaluate through the UI whether it **actually does it**. If the README says "supports dark mode" and the UI doesn't have it, that's a finding — it's a claim-vs-reality gap, not a code review.

---

## Current State (v0.1.0 — Scaffolding)

What exists in the repo today:

| Module | File | Status |
|--------|------|--------|
| Schemas | `core/schemas.py` | Done — Pydantic models for all entities |
| LLM layer | `core/llm.py` | Done — Anthropic + OpenAI |
| Intent Modeler | `core/intent_modeler.py` | Scaffold — scrapes landing page only |
| Persona Generator | `core/persona_generator.py` | Scaffold — generates from intent model |
| Orchestrator | `core/orchestrator.py` | Scaffold — assigns journeys, deduplicates |
| Pipeline | `core/pipeline.py` | Scaffold — wires everything together |
| Web Runner | `runners/web_runner.py` | Scaffold — basic Playwright, text-only eval |
| Mobile Runner | `runners/mobile_runner.py` | Scaffold — viewport emulation only |
| Design Lens | `lenses/design_lens.py` | Scaffold — text-based, no vision |
| Institutional Lens | `lenses/institutional_lens.py` | Scaffold — LLM-only review |
| Report Generator | `reporting/report_generator.py` | Done — markdown + JSON + repair briefs |
| Scheduler | `scheduling/scheduler.py` | Done — APScheduler cron |
| CLI | `cli.py` | Done — `preflight run` / `preflight schedule` |
| Tests | `tests/test_core.py` | Done — 11 passing schema tests |

### Honest assessment of v0.1.0
The scaffolding works (installs, CLI runs, tests pass) but:
- Web runner sends page text to LLM and asks "what's wrong?" — that's speculation, not QA
- No screenshots are sent to the LLM as vision input
- Navigation uses LLM-guessed CSS selectors — will fail on real products
- No repo analysis at all
- Deduplication is title-string matching
- Mobile is just a viewport resize
- No real interaction depth (forms, search, filters, SPAs)

---

## Build Phases

## Phase 0: Repo Analyzer + Enhanced Intent Model
**Goal:** Give Preflight the context a real human tester would get during team onboarding.

### 0.1 Repo Analyzer (`core/repo_analyzer.py`)

Create a new module that clones/accesses a GitHub repo and extracts product understanding.

**Inputs:** GitHub repo URL (public or private with token)

**What to extract:**

```python
class RepoInsights(BaseModel):
    product_name: str
    description: str                    # From README
    tech_stack: list[str]               # From package manifest
    claimed_features: list[str]         # From README, docs, CHANGELOG
    routes_or_pages: list[str]          # From file structure (e.g., pages/, routes/, app/)
    recent_changes: list[str]           # Last 10 PR titles/descriptions
    known_issues: list[str]             # Open GitHub issue titles
    configuration_hints: list[str]      # From env.example, config files
    documentation_summary: str          # Condensed docs/ content
    repo_confidence: float              # How much we learned
```

**Implementation approach:**
1. Use GitHub API (via `httpx` or `gh` CLI) for repos, PRs, issues
2. Clone repo shallowly (`git clone --depth 1`) for file structure analysis
3. Read only the files listed in the "MAY read" section above
4. Use LLM to summarize README and docs into structured product understanding
5. Parse package manifests with language-specific logic (JSON for package.json, TOML for pyproject.toml, etc.)
6. Extract route/page structure from common conventions:
   - Next.js: `app/` or `pages/` directory
   - React Router: look for route definitions in file names
   - FastAPI/Flask: `routes/`, `api/`, or `views/` directories
   - Generic: any directory structure suggesting pages or screens

**Important:** Never read function/class bodies. Read file *names* and *directory structure* only for route extraction.

### 0.2 Enhanced Intent Model

Update `IntentModeler` to accept both `RepoInsights` and scraped page content.

The enhanced intent model should:
- Cross-reference repo claims with visible UI
- Build a **Feature Expectation List**: "Based on the repo, these features should exist: [...]"
- Flag features that are claimed but not found in the UI as findings
- Use repo context to generate smarter personas (e.g., if repo has billing code, generate a "payment-skeptical user")
- Use recent PRs/issues to prioritize testing areas (recently changed = higher risk)

### 0.3 Schema Updates

Add to `schemas.py`:
- `RepoInsights` model
- `FeatureExpectation` model (feature name, source, verified status)
- Update `ProductIntentModel` to include `repo_insights` and `feature_expectations`
- Update `RunConfig` to accept `repo_url` and `github_token_env`

### 0.4 CLI Update

Add repo option:
```bash
preflight run https://my-product.com --repo https://github.com/user/repo
```

---

## Phase 1: Make the Web Runner Actually Work
**Goal:** Real browser interaction with real evaluation, not text speculation.

### 1.1 Playwright MCP Integration

Replace raw Playwright text scraping with Playwright MCP for structured page understanding.

**What to use Playwright MCP for:**
- Accessibility tree snapshots (structured, semantic page representation)
- Element identification (labels, roles, states — not fragile CSS selectors)
- Form field discovery
- Navigation structure understanding

**What to keep raw Playwright for:**
- Screenshots (MCP doesn't do this)
- Traces and HAR capture
- Video recording
- Performance timing
- Console error collection

**Implementation:**
- Add `playwright-mcp` as a dependency
- Create a `PageSnapshot` model that combines:
  - Accessibility tree (from MCP)
  - Screenshot as base64 (from Playwright)
  - Performance metrics: load time, LCP, CLS (from Playwright)
  - Console errors (from Playwright)
  - Network error count (from Playwright)
  - Current URL and page title
- Every evaluation prompt receives a `PageSnapshot`, not raw text

### 1.2 Vision-Based Evaluation

Send actual screenshots to the LLM as vision input.

**Implementation:**
- Update `LLMClient` to support vision messages (Anthropic and OpenAI both support image input)
- Add `complete_with_vision(prompt, images, system)` method
- All evaluation prompts include the current page screenshot
- Design lens sends screenshots directly (this is the whole point)

**New LLM method:**
```python
def complete_with_vision(
    self,
    prompt: str,
    images: list[tuple[bytes, str]],  # (image_bytes, media_type)
    system: str = "",
    max_tokens: int = 4096,
) -> str:
```

### 1.3 Deterministic Interaction Engine

Build a library of reliable interaction patterns. LLM plans what to do; deterministic code executes it.

**Action types to support:**
- `navigate(url)`
- `click(accessibility_label_or_role)`
- `fill_form(field_map: dict)` — uses accessibility labels
- `search(query)` — finds search input, types, submits
- `scroll(direction, amount)`
- `wait_for(condition, timeout)`
- `screenshot()` — capture current state
- `go_back()`

**The LLM's job:** Given the current PageSnapshot and persona goals, output a list of `Action` objects (what to do and why). The runner executes them deterministically.

**Action schema:**
```python
class Action(BaseModel):
    type: str  # navigate, click, fill_form, search, scroll, wait_for, screenshot, go_back
    target: str  # accessibility label, URL, or field name
    value: str | None = None
    reason: str = ""  # Why the persona is doing this
```

### 1.4 Multi-Step Journey Execution

Restructure the evaluation loop:

```
For each persona:
    For each assigned journey:
        1. PLAN: LLM generates action sequence from PageSnapshot + persona goals
        2. EXECUTE: Runner executes actions deterministically, capturing snapshots at each step
        3. JUDGE: LLM evaluates each snapshot from persona perspective (with vision)
        4. ADAPT: If persona would take a different path, LLM replans
        Repeat steps 1-4 up to N steps (configurable, default 10)
```

Each step produces a `JourneyStep`:
```python
class JourneyStep(BaseModel):
    step_number: int
    action: Action
    page_snapshot_before: str  # Reference to saved snapshot
    page_snapshot_after: str
    screenshot_path: str
    issues_found: list[Issue]
    persona_reaction: str  # How the persona felt about this step
    confidence_level: float  # Persona's confidence they're on the right track
```

### 1.5 Evidence Capture

Upgrade evidence to be real and comprehensive:
- **Playwright traces** (zip files with full replay) — capture per journey
- **HAR files** — network activity per journey
- **Video recording** — optional, configurable
- **Timestamped screenshots** — one per journey step, named by step
- **Console log** — full browser console output
- **Performance metrics** — LCP, CLS, FID, load time per page

---

## Phase 2: Make It Smart
**Goal:** Cross-persona comparison, grounded judgment, visual regression.

### 2.1 Comparative Evaluation

After all personas complete their runs:
1. Group findings by screen/flow
2. For each screen, compare what different personas found
3. Generate **convergence findings**: "3 of 5 personas struggled with X"
4. Generate **persona-specific findings**: "Only the compliance reviewer noticed missing audit trail"
5. Weight severity by convergence (issue found by 4 personas > issue found by 1)

### 2.2 Grounded Judgment Prompts

Restructure ALL evaluation prompts to require evidence anchoring:

**Rule:** Every finding must reference a specific:
- Screenshot (by step number)
- Element (by accessibility label)
- Measurement (load time, element count, etc.)
- Or observed absence (explicitly "element X was not found")

**Prompt structure:**
```
You MUST cite evidence for every finding:
- "In screenshot step-3.png, the submit button [role=button, name='Submit'] has no visible disabled state"
- "Page load took 4200ms (measured), exceeding reasonable threshold"
- "No element with role 'navigation' or label containing 'audit' was found on this page"

Findings without specific evidence will be rejected.
```

### 2.3 LLM-Based Deduplication

Replace title-string matching:
1. After all issues collected, send them to LLM in batches
2. Ask LLM to cluster semantically similar issues
3. For each cluster, keep the highest-confidence version
4. Annotate with "also found by agents: [...]"

### 2.4 Screenshot Comparison (Visual Regression)

For scheduled runs:
1. Save reference screenshots from each run
2. On next run, compare new screenshots to references
3. Use pixel-diff (Pillow) for structural changes
4. Use LLM vision for semantic changes ("button moved", "color changed", "new element appeared")
5. Report visual regressions separately

### 2.5 Real Performance Evaluation

Collect via Playwright:
- Navigation timing (load, DOMContentLoaded, networkidle)
- Largest Contentful Paint (LCP)
- Cumulative Layout Shift (CLS)
- First Input Delay approximation
- Total network requests and payload size
- Time to interactive (estimated)

Set budgets based on product type:
- Marketing site: LCP < 2.5s, CLS < 0.1
- SaaS app: LCP < 4s, interaction latency < 200ms
- Mobile web: LCP < 3s, total payload < 2MB

Report against budgets, not just raw numbers.

---

## Phase 3: Make It Institutional
**Goal:** The unique lens no competitor offers.

### 3.1 Structured Institutional Checklist

Not LLM speculation — actual UI verification:

| Check | How to verify | Evidence |
|-------|--------------|----------|
| Audit trail visible | Look for "History", "Activity", "Log" elements | Screenshot + accessibility tree |
| Version history | Look for "Versions", "Revisions", revision indicators | Screenshot |
| Source attribution | On key outputs, look for "Source", citations, links | Screenshot of output + element inspection |
| Data freshness markers | Look for timestamps, "Updated", "As of" indicators | Screenshot |
| Role indicators | Look for user role display, permissions UI | Screenshot |
| Confirmation dialogs | Attempt destructive action, verify dialog appears | Screenshot sequence |
| Error quality | Trigger errors, evaluate message helpfulness | Screenshot |
| Privacy indicators | Look for privacy policy, data handling copy | Element search |
| Export/download | Verify data can be extracted | Interaction test |

For each check: attempt to verify through the UI, capture screenshot evidence, report pass/fail/not-applicable.

### 3.2 Provenance Scoring

For each key product output (dashboards, reports, generated content, recommendations):
1. Navigate to the output
2. Check: Are sources cited? Are they specific? Is freshness shown?
3. Score: 0 (no provenance) to 5 (full source trail with timestamps)
4. Screenshot evidence for each score

### 3.3 Trust Signal Inventory

Systematically check and catalog:
- SSL certificate (valid, correct domain)
- Privacy policy link (exists, accessible)
- Terms of service (exists, accessible)
- Contact information (visible, specific)
- Error message quality (helpful vs. generic)
- Data handling transparency
- Third-party trust indicators (certifications, badges)

Report as a trust scorecard.

### 3.4 Governance Flow Testing

For risky actions:
1. Identify them from repo context (delete, export, admin, payment)
2. Attempt each action
3. Verify: confirmation dialog? Undo option? Role check? Approval flow?
4. Report missing governance gates

---

## Phase 4: Make It Practically Useful
**Goal:** Daily-driver quality for solo builders and small teams.

### 4.1 GitHub Issue Export

One command exports findings as GitHub issues:
```bash
preflight export-issues --repo https://github.com/user/repo --run ./artifacts/latest
```

Each issue becomes a GitHub issue with:
- Title from finding
- Labels: severity, category
- Body: user impact, repro steps, evidence (screenshots uploaded as issue attachments)
- Repair brief in a collapsible section

### 4.2 Run Comparison / Regression Reports

```bash
preflight compare ./artifacts/run_20260313 ./artifacts/run_20260314
```

Output:
- New issues (not in previous run)
- Resolved issues (in previous, not in current)
- Regressed issues (severity increased)
- Persistent issues (still present)
- Visual regressions (screenshot diffs)

### 4.3 CI Integration

```bash
preflight run https://staging.my-product.com --fail-on high --exit-code
# Exit 0 = no high/critical issues
# Exit 1 = high or critical issues found
```

### 4.4 Interactive HTML Report

Generate a self-contained HTML report with:
- Filterable issue list (by severity, category, agent, platform)
- Inline screenshots with lightbox
- Expandable evidence sections
- Comparison view (if baseline exists)
- Executive summary with severity chart

Use a simple Jinja2 template — no React/build step needed.

### 4.5 Webhook / Slack Summary

```bash
preflight run https://my-product.com --webhook https://hooks.slack.com/...
```

Post a summary:
```
Preflight Report: MyProduct
🔴 2 Critical  🟠 5 High  🟡 8 Medium
Top issue: "Checkout flow has no error recovery"
Full report: [link]
```

---

## What to Defer (Not in Phases 0-4)

- **Native mobile testing (Maestro)** — Mobile web via Playwright emulation handles 80%. Native adds huge complexity.
- **FastAPI service layer** — CLI-first. API when there's demand for a dashboard.
- **Copy/tone review lens** — Lower differentiation value than design + institutional.
- **Full device farm** — Single browser, two viewports (desktop + mobile) is enough for v1.
- **Dashboard UI** — Reports are the product. Dashboard is polish.
- **Multi-repo / monorepo support** — Single repo first.
- **GitLab / Bitbucket support** — GitHub-first. Abstract later.

---

## Technical Architecture (Target State)

```
preflight/
├── core/
│   ├── schemas.py              # All Pydantic models
│   ├── llm.py                  # LLM abstraction (text + vision + JSON)
│   ├── repo_analyzer.py        # NEW: GitHub repo analysis
│   ├── intent_modeler.py       # REWORK: Repo + scrape + brief → intent
│   ├── persona_generator.py    # UPDATE: Repo-informed personas
│   ├── orchestrator.py         # UPDATE: Journey assignment + comparison
│   ├── pipeline.py             # UPDATE: Full pipeline with repo step
│   └── actions.py              # NEW: Deterministic action types
├── runners/
│   ├── web_runner.py           # REWORK: MCP + vision + deterministic actions
│   ├── mobile_runner.py        # UPDATE: Better mobile emulation
│   └── page_snapshot.py        # NEW: Structured page capture
├── lenses/
│   ├── design_lens.py          # REWORK: Vision-based design critique
│   ├── institutional_lens.py   # REWORK: Structured checklist verification
│   └── trust_lens.py           # NEW: Trust signal inventory
├── reporting/
│   ├── report_generator.py     # UPDATE: Add HTML report
│   ├── comparison.py           # NEW: Run-to-run diff
│   ├── github_export.py        # NEW: Issue export to GitHub
│   └── templates/
│       └── report.html         # NEW: Jinja2 HTML template
├── scheduling/
│   └── scheduler.py            # Existing — simplify
├── cli.py                      # UPDATE: New commands
└── tests/
    ├── test_core.py            # Existing + expand
    ├── test_repo_analyzer.py   # NEW
    ├── test_web_runner.py      # NEW
    └── test_reporting.py       # NEW
```

---

## Build Order for Codex

Execute in this exact order. Each phase should be a working, testable increment.

### Batch 1: Foundation (Phase 0)
1. `core/repo_analyzer.py` + `RepoInsights` schema
2. Enhanced `core/intent_modeler.py` (accepts repo + scrape)
3. Updated `core/schemas.py` (new models)
4. Updated `cli.py` (--repo flag)
5. Tests for repo analyzer

### Batch 2: Real Web Runner (Phase 1)
6. `runners/page_snapshot.py` (PageSnapshot model + capture)
7. `core/llm.py` vision support (`complete_with_vision`)
8. `core/actions.py` (Action schema + deterministic execution)
9. Reworked `runners/web_runner.py` (MCP + vision + actions + journey steps)
10. Tests for web runner (mock LLM, real Playwright)

### Batch 3: Smart Evaluation (Phase 2)
11. Comparative evaluation in `core/orchestrator.py`
12. Grounded judgment prompt rework (all prompts)
13. LLM-based deduplication
14. Performance metric collection and budgets
15. Tests for orchestrator comparison logic

### Batch 4: Institutional (Phase 3)
16. Structured institutional checklist in `lenses/institutional_lens.py`
17. `lenses/trust_lens.py` (trust signal inventory)
18. Provenance scoring
19. Governance flow testing
20. Tests for institutional verification

### Batch 5: Practical (Phase 4)
21. `reporting/github_export.py`
22. `reporting/comparison.py` (run-to-run diff)
23. Interactive HTML report template
24. CI exit code support
25. Webhook/Slack summary
26. End-to-end integration tests

---

## Implementation Constraints for Codex

- **Never add code analysis features.** Read repo for intent, evaluate through UI.
- **Every LLM evaluation call must include a screenshot** (vision). Text-only evaluation is not acceptable.
- **Every finding must cite specific evidence.** No speculative issues.
- **Deterministic execution, LLM-guided planning.** The LLM decides what to do. Playwright does it reliably.
- **All prompts must be explicit and inspectable.** No hidden prompt magic. Store prompts as module-level constants.
- **Prefer working end-to-end over perfect components.** A rough full pipeline beats a polished partial one.
- **Test against real products.** Unit tests for schemas, integration tests against real URLs.
- **Keep it installable with `pip install -e .`** and runnable with `preflight run`.

---

## Success Criteria

After all 5 batches, `preflight run https://some-product.com --repo https://github.com/user/repo` should:

1. Clone the repo and understand what the product claims to do
2. Scrape the live product and build a rich intent model
3. Generate 4-8 personas that make sense for this specific product
4. Walk through 3-5 critical journeys with real browser interaction
5. Capture screenshots at every step
6. Evaluate each step with vision + accessibility tree + persona context
7. Detect 10-20 real issues with screenshot evidence and repro steps
8. Include at least 3 genuinely insightful findings (not just "button broken")
9. Flag claim-vs-reality gaps (repo says X, UI doesn't have it)
10. Produce a report a solo developer would actually read and act on
11. Export issues to GitHub with one command
12. Return useful exit code for CI
