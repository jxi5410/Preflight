# Preflight Refinement Spec — Report Quality & Handoff UX

**Context:** First real-world run against LondonAI.network produced 140 issues. Many duplicates, weak evidence references, generic scores. This spec addresses the quality issues.

---

## 1. Aggressive Issue Deduplication & Grouping

### Problem
140 issues with many near-duplicates. "Missing privacy policy" and "Missing legal framework" appear as separate issues. "Evaluation blocked" errors appear multiple times.

### Fix

**1a. Semantic clustering before report generation.**
After all issues are collected, before report generation:
1. Group issues by URL + category as a first pass
2. Use LLM to cluster remaining issues by semantic similarity
3. Merge duplicates: keep highest-confidence version, note all reporting agents
4. Group related issues under parent themes (e.g., "Legal & Privacy Gaps" containing privacy policy, data handling, terms of service issues)

**1b. Error deduplication.**
Issues with category "functional" that contain error messages in their title should be deduplicated by error signature (strip variable parts, match on error type + location).

**1c. Add to schemas.py:**
```python
class IssueGroup(BaseModel):
    """A group of related issues under a common theme."""
    group_id: str
    theme: str  # e.g., "Legal & Privacy Gaps"
    description: str
    issues: list[Issue] = Field(default_factory=list)
    combined_severity: str = "high"  # Highest severity in group
```

**1d. Update report generator to output grouped issues, not flat list.**

---

## 2. Fix Evidence References

### Problem
"Step-5" on a blank screen means nothing. Screenshots referenced by filename with no context.

### Fix

**2a. Every screenshot gets a human-readable caption.**
When capturing a screenshot during a journey step, generate a caption:
- "After clicking 'Events' from the homepage — blank page with no content"
- "Login form on /auth/signin — email and password fields visible"

Store caption in Evidence model.

**2b. Update Evidence schema:**
```python
class ScreenshotEvidence(BaseModel):
    path: str
    caption: str  # Human-readable description of what's shown
    step_number: int | None = None
    url: str = ""

class Evidence(BaseModel):
    screenshots: list[ScreenshotEvidence] = Field(default_factory=list)
    # ... rest unchanged
```

**2c. In report.md, show caption with each screenshot:**
```markdown
**Evidence:**
- ![Blank page after navigation](screenshots/step-5-events.png)
  *After clicking 'Events' from homepage — page loaded with no visible content*
```

**2d. In report.html, embed screenshots inline with captions.**

---

## 3. Adaptive Scoring (Not Generic)

### Problem
Trust Score, Institutional Readiness, and Provenance Score appear on every report regardless of product type. A community events website doesn't need provenance scoring the same way a financial dashboard does.

### Fix

**3a. Scores should be selected based on intent model.**
After building the intent model, determine which scores are relevant:
- **Always show:** Trust Score (every product needs trust)
- **Show if institutional_relevance >= moderate:** Institutional Readiness, Provenance Score
- **Show if product_type involves content/data:** Provenance Score
- **Show if product_type is e-commerce/fintech:** Security indicators, Payment trust
- **Show if product_type is community/social:** Community safety, Moderation indicators

**3b. Each score gets a contextual explanation.**
Not just "Trust Score: 62%" but "Trust Score: 62% — For a community platform, this means users may hesitate to share personal info or attend events. Key gaps: no visible privacy policy, no data handling transparency."

**3c. Update ReportGenerator to:**
- Accept intent model when generating
- Filter scores by product type relevance
- Generate per-score explanations using LLM

---

## 4. Clickable Score Cards in HTML Report

### Problem
The score numbers (140 total, 12 critical, etc.) are static. User wants to click "12 CRITICAL" and jump to the critical issues list.

### Fix
Update `report.html` template:
- Each severity count card links to `#severity-critical`, `#severity-high`, etc.
- Each section has corresponding anchor IDs
- Trust Score / Institutional / Provenance cards link to their respective detail sections
- Add smooth scroll behavior

---

## 5. Repo Visibility Indicator

### Problem
User wants to know if the repo is public or private.

### Fix
**5a. Detect in repo analyzer** — check via GitHub API if repo is public or private.
**5b. Store in RepoInsights:** `is_public: bool | None = None`
**5c. Display in report header:** "Repo: https://github.com/jxi5410/AI.LDN (public)"

---

## 6. Handoff Fix Options

### Problem
The HANDOFF.md dumps all tasks. User wants to choose scope.

### Fix
Add a "How to Fix" section at the end of every HANDOFF.md:

```markdown
## How to Fix

### Option A — Critical only ({n} tasks, ~{hours})
```
claude "Read HANDOFF.md and fix only CRITICAL tasks. Show me each fix before applying."
```

### Option B — Critical + High ({n} tasks, ~{hours})
```
claude "Read HANDOFF.md and fix CRITICAL and HIGH tasks in order."
```

### Option C — Everything ({n} tasks, ~{hours})
```
claude "Read HANDOFF.md and fix all tasks in priority order."
```

### Option D — Interactive review
```
claude "Read HANDOFF.md. Present each task, let me approve or skip, then fix approved ones."
```
```

Calculate task counts and hour estimates per option from the issue data.

---

## 7. Reduce Total Run Time

### Problem
Run took too long without progress feedback. Even with the new progress tracker, the underlying run should be faster.

### Fix

**7a. Add timeouts to all LLM calls.** Default 60 seconds per call. If exceeded, log warning and continue with partial results.

**7b. Limit exploration depth.** Currently agents can take up to 3 exploration steps per journey, with each step making an LLM call for planning + evaluation. Cap at 2 steps for non-critical journeys.

**7c. Reduce persona count for simple products.** If the intent model confidence is high and the product is simple (< 5 routes), generate 3-4 personas instead of 6-8.

**7d. Parallelize where safe.** Design lens, trust lens, and institutional lens can run concurrently (they all read the same result, none modify it).

---

## 8. Login / Auth Flow Evaluation (Without Credentials)

### Problem
Preflight skipped the login page entirely because no credentials were provided. A real first-time user would encounter the login/signup page and evaluate it as a product surface — is it clear, trustworthy, functional? This is a high-priority evaluation that was completely missed.

### Fix

**8a. Add a dedicated auth flow evaluation step in the web runner.**
Even without credentials, the runner must:
- Navigate to the login/signup page (find it via navigation links, common URL patterns like /login, /signin, /auth, /register)
- Evaluate the page visually (via screenshot + vision): is the login form clear? professional? trustworthy?
- Check for: social login options, signup link, forgot password link, terms/privacy links
- Test error paths: submit empty form, submit obviously invalid email (test@invalid), observe error handling
- Evaluate: are error messages helpful? Does the form recover gracefully?
- Assess as first-time user: would I feel confident creating an account here?

**8b. This should run as one of the first journeys for the "first_time_user" persona.**
The first-time user's natural flow is: land on homepage → look for signup/login → evaluate whether to create an account. If they wouldn't sign up, that's a critical finding.

**8c. If credentials ARE provided:**
- Test the actual login flow end-to-end
- Test wrong password behavior
- Test account recovery flow if discoverable
- Evaluate post-login experience (does the user know where they are? what to do next?)

**8d. Auth-specific issue categories to detect:**
- No visible signup path
- Login form without password visibility toggle
- No social login options (for consumer products)
- Poor error messages on invalid input
- No "forgot password" flow
- Missing trust indicators (SSL, privacy policy link near form)
- Form doesn't work (submit does nothing, page error)

---

## 9. Mobile Responsiveness & Visual Layout Evaluation

### Problem
Preflight missed critical mobile layout issues: content cut off, misalignment, information failing to show. The mobile evaluation only changes the viewport size but doesn't specifically check for responsive design problems. The evaluation prompts don't consistently send screenshots via vision for mobile, so visual-only problems (overflow, truncation, overlap) are invisible.

### Fix

**9a. Mandatory screenshot-via-vision for ALL mobile evaluations.**
Every evaluation call for a mobile_web persona MUST include the screenshot as a vision input. No exceptions. The accessibility tree alone cannot detect visual layout problems.

**9b. Add a dedicated mobile responsiveness check.**
After the main evaluation completes, run a comparison check:
1. For each key page visited during the run, capture screenshots at both desktop (1440×900) and mobile (390×844) viewports
2. Send both screenshots to the LLM in a single vision call with the prompt:

```
Compare these two screenshots of the same page — desktop (left) and mobile (right).
Identify any responsive design problems:
- Content that is visible on desktop but cut off, hidden, or missing on mobile
- Text that truncates or overflows its container
- Elements that overlap or misalign
- Touch targets that are too small (< 44px)
- Horizontal scrolling (content wider than viewport)
- Navigation that becomes unusable on mobile
- Critical information that fails to display
- Images or media that don't resize properly
- Forms that are difficult to use on mobile
For each issue, describe exactly what's wrong and where on the screen it appears.
```

**9c. Add mobile-specific evaluation prompts for every mobile persona.**
When a persona has device_preference=mobile_web, add to their evaluation prompt:
```
You are on a mobile phone (390px wide). Pay special attention to:
- Can you read all text without zooming?
- Are buttons/links large enough to tap with a finger?
- Does any content extend beyond the screen edge?
- Is the navigation usable with one hand?
- Are there any horizontal scrollbars?
- Does critical information appear above the fold?
```

**9d. New issue category: "responsive" under IssueCategory enum.**
Add `responsive = "responsive"` so mobile layout issues are categorized separately and clearly.

**9e. Design lens must include mobile viewport.**
The design lens currently reviews artifacts generically. It must specifically request and evaluate mobile screenshots, not just desktop ones.

---

## Build Order (Updated)

1. Evidence schema update (ScreenshotEvidence with captions)
2. Issue grouping (IssueGroup schema + clustering logic)
3. Aggressive deduplication (error signature matching + semantic clustering)
4. Adaptive scoring (product-type-aware score selection)
5. HTML report improvements (clickable cards, inline screenshots, score explanations)
6. Repo visibility detection
7. Handoff fix options
8. **Login/auth flow evaluation (without credentials)**
9. **Mobile responsiveness & visual layout evaluation**
10. Run time optimizations (timeouts, exploration caps, lens parallelization)
11. **Multi-provider tiered model support (see section 10 below)**
12. Tests for all of the above

---

## 10. Multi-Provider Tiered Model Support

### Problem
Running Preflight entirely on Claude Sonnet 4.6 ($3/$15 per 1M tokens) is expensive. A single run with 40+ LLM calls costs $3-5. Most of those calls don't need frontier-tier quality — per-page evaluations, action planning, and screenshot analysis can use a cheaper model. Only high-judgment steps (intent modeling, persona generation, final dedup, report narrative) benefit from the best model.

### Architecture: Tiered Model Routing

Every LLM call in the pipeline gets tagged with a "tier" that determines which model handles it. Two tiers:

**Fast tier** — high-volume, per-step calls. Needs good vision, decent JSON output, fast response. Default: `gemini-3-flash` ($0.50/$3.00).

Used for: per-page evaluation, action planning, screenshot analysis, navigation decisions, mobile responsiveness checks, trust signal checks, individual auth flow evaluation steps.

**Smart tier** — low-volume, high-judgment calls. Needs best reasoning, strong instruction following, nuanced output. Default: `gemini-3.1-pro-preview` ($2.00/$12.00).

Used for: product intent modeling, persona generation, journey assignment, issue deduplication/clustering, issue grouping, comparative evaluation synthesis, report narrative generation, handoff task generation.

### Cost Impact
Estimated per-run cost drops from $3-5 (all Claude Sonnet) to $0.50-0.80 (tiered Gemini). That's an 80-85% reduction.

### Schema Changes

Add to `schemas.py`:

```python
class ModelTier(str, Enum):
    fast = "fast"
    smart = "smart"

class TieredModelConfig(BaseModel):
    """Model configuration with separate fast and smart tiers."""
    fast_provider: str = "google"
    fast_model: str = "gemini-3-flash"
    smart_provider: str = "google"
    smart_model: str = "gemini-3.1-pro-preview"
    
    # Presets for common configurations
    @classmethod
    def budget(cls) -> "TieredModelConfig":
        """Cheapest option. Gemini Flash for everything."""
        return cls(
            fast_provider="google", fast_model="gemini-2.5-flash",
            smart_provider="google", smart_model="gemini-3-flash",
        )
    
    @classmethod
    def balanced(cls) -> "TieredModelConfig":
        """Best value. Gemini Flash + Gemini 3.1 Pro."""
        return cls(
            fast_provider="google", fast_model="gemini-3-flash",
            smart_provider="google", smart_model="gemini-3.1-pro-preview",
        )
    
    @classmethod
    def premium(cls) -> "TieredModelConfig":
        """Best quality. Claude Sonnet + Claude Opus."""
        return cls(
            fast_provider="anthropic", fast_model="claude-sonnet-4-20250514",
            smart_provider="anthropic", smart_model="claude-opus-4-6-20250514",
        )
    
    @classmethod
    def openai(cls) -> "TieredModelConfig":
        """OpenAI stack. GPT-4.1 + GPT-5.4."""
        return cls(
            fast_provider="openai", fast_model="gpt-4.1",
            smart_provider="openai", smart_model="gpt-5.4",
        )
```

Update `RunConfig` to accept tiered config:
```python
class RunConfig(BaseModel):
    # ... existing fields ...
    model_tier: TieredModelConfig = Field(default_factory=TieredModelConfig.balanced)
    # Deprecated but kept for backward compat:
    llm_provider: str = "google"
    llm_model: str = "gemini-3-flash"
```

### LLM Client Changes

**Add Google Gemini provider to `llm.py`.**

Install `google-genai` (the official Google Gen AI SDK). Add provider="google" support for both text and vision calls. The Gemini API accepts image bytes directly in the content array, similar to Anthropic's vision API.

```python
# In LLMClient.__init__:
elif provider == "google":
    import google.genai as genai
    self._client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
```

**Update LLMClient to accept a tier parameter:**

```python
class LLMClient:
    def __init__(self, tier_config: TieredModelConfig):
        self.tier_config = tier_config
        self._clients = {}  # Lazy-init per provider
    
    def complete(self, prompt, system="", tier: ModelTier = ModelTier.fast, ...):
        provider, model = self._get_provider_model(tier)
        # Route to correct provider
    
    def complete_with_vision(self, prompt, images, tier: ModelTier = ModelTier.fast, ...):
        # Same routing with vision support
```

### Pipeline Changes

Tag every LLM call in the codebase with the appropriate tier:

| Module | Call | Tier |
|--------|------|------|
| `intent_modeler.py` | Build intent model | smart |
| `persona_generator.py` | Generate personas | smart |
| `orchestrator.py` | Assign journeys | smart |
| `orchestrator.py` | Comparative evaluation | smart |
| `orchestrator.py` | LLM deduplication | smart |
| `web_runner.py` | Plan actions | fast |
| `web_runner.py` | Evaluate page | fast |
| `web_runner.py` | Navigate/explore | fast |
| `design_lens.py` | Design review | fast |
| `trust_lens.py` | Trust signal checks | fast |
| `institutional_lens.py` | Institutional review | smart |
| `report_generator.py` | Report narrative | smart |
| `handoff.py` | Generate handoff tasks | smart |

### CLI Changes

```bash
# Use presets
preflight run https://example.com --tier balanced    # Default: Gemini Flash + 3.1 Pro
preflight run https://example.com --tier budget      # Cheapest: all Gemini Flash
preflight run https://example.com --tier premium     # Best: Claude Sonnet + Opus
preflight run https://example.com --tier openai      # OpenAI: GPT-4.1 + GPT-5.4

# Or specify models directly
preflight run https://example.com --fast-model gemini-3-flash --smart-model gemini-3.1-pro-preview

# In interactive mode, ask the user
# "Which quality tier? [balanced/budget/premium/openai]"
```

### Environment Variables

```bash
# One of these required depending on tier:
GOOGLE_API_KEY=...      # For Gemini models (default tier)
ANTHROPIC_API_KEY=...   # For Claude models (premium tier)
OPENAI_API_KEY=...      # For OpenAI models (openai tier)
```

### pyproject.toml

Add `google-genai>=1.0.0` to dependencies.

### Backward Compatibility

The old `--provider` and `--model` flags should still work. If set, they override both fast and smart tiers to use that single provider/model (the old behavior). The new `--tier` flag takes precedence if both are specified.

### Interactive Mode Update

In interactive mode, after asking for URL and repo, add:

```
Which quality tier? (balanced is recommended for most users)
  1. balanced — Best value, ~$0.50-0.80/run (Gemini Flash + Pro)
  2. budget — Cheapest, ~$0.20-0.40/run (Gemini Flash only)  
  3. premium — Best quality, ~$3-5/run (Claude Sonnet + Opus)
  4. openai — OpenAI stack, ~$2-4/run (GPT-4.1 + GPT-5.4)
Choose [1-4, default 1]:
```
