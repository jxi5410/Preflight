# HumanQA Refinement Spec — Report Quality & Handoff UX

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
HumanQA skipped the login page entirely because no credentials were provided. A real first-time user would encounter the login/signup page and evaluate it as a product surface — is it clear, trustworthy, functional? This is a high-priority evaluation that was completely missed.

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
HumanQA missed critical mobile layout issues: content cut off, misalignment, information failing to show. The mobile evaluation only changes the viewport size but doesn't specifically check for responsive design problems. The evaluation prompts don't consistently send screenshots via vision for mobile, so visual-only problems (overflow, truncation, overlap) are invisible.

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
11. Tests for all of the above
