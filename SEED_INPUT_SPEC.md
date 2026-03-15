# Input-Required Sites — Seed Input Spec

**Problem:** Many products require the user to type something before showing any meaningful content — search engines, AI tools, analytics platforms, code playgrounds, URL analyzers, etc. Currently Preflight navigates to the URL, sees an empty input field, and evaluates the blank landing state. A real user would type something and evaluate what happens next.

---

## How Real Users Interact With Input-First Products

A first-time user arriving at a search product would try a common query. Someone landing on an AI writing tool would type a simple prompt. A user testing a domain analyzer would enter a well-known domain. The persona's expertise level and goals determine what they'd type — a novice tries something simple, a power user tries something complex or edge-case-y.

This is core product evaluation, not an edge case. Preflight must handle it.

---

## What to Build

### 1. Input Pattern Detection

During page scraping or initial evaluation, detect if the product's primary interaction requires user input. Look for signals in the page structure:

**Strong signals (high confidence the product is input-first):**
- A prominent `<input>`, `<textarea>`, or `<form>` as the main page element
- An input field with a submit/go/search button as the primary CTA
- Placeholder text like "Search...", "Enter URL...", "Type a prompt...", "Ask anything..."
- The page has very little other content besides the input and branding
- Accessibility tree shows the input as the first interactive element

**Supporting signals:**
- The repo README mentions "search", "query", "prompt", "enter", "type", "paste"
- The product type inferred from intent model is search/AI/analytics/tool
- The landing page has no navigation links to other content pages

Store this detection in the intent model:

```python
class ProductIntentModel(BaseModel):
    # ... existing fields ...
    input_first: bool = False  # True if product requires input to show content
    input_type: str = ""  # "search", "prompt", "url", "code", "data", "free_text"
    input_placeholder: str = ""  # The actual placeholder text from the input field
```

### 2. Seed Input Generation

When `input_first=True`, generate contextually appropriate seed inputs for each persona to try. The inputs should match what a real person with that persona's profile would type.

**Generation approach:** Use a single LLM call (smart tier) that receives the intent model + persona details and generates 2-3 seed inputs per persona.

**Prompt structure:**
```
This product is: {product_name} ({product_type})
The main input field says: "{input_placeholder}"
Input type: {input_type}

This persona is: {persona_name} — {persona_role}
Goals: {persona_goals}
Expertise: {expertise_level}

What would this person type into the input field? Generate 2-3 realistic inputs this persona would try, ordered from most likely to least likely.

Rules:
- First input should be something simple and common (what most people would try first)
- Second input should test a realistic use case for this persona
- Third input (if applicable) should be an edge case or stress test
- Inputs must be realistic — not test data like "asdf" or "test123"
- Match the input type: if it's a URL field, give URLs; if search, give queries; etc.

Respond with JSON: {"seed_inputs": ["first try", "second try", "third try"]}
```

**Examples of good seed inputs by product type:**

| Product Type | Input Type | Persona | Seed Inputs |
|-------------|-----------|---------|-------------|
| Search engine | search | First-time user | "best restaurants nearby", "weather today" |
| AI writing tool | prompt | Skeptical user | "Write a professional email declining a meeting", "Summarize this: [paste long text]" |
| Domain analyzer | url | Power user | "google.com", "myobscuresite.xyz", "" (empty — test error handling) |
| Code playground | code | Developer | `console.log("hello")`, `function broken( {` (syntax error) |
| Translation tool | text | Mobile user | "Where is the nearest train station?", "こんにちは" |

### 3. Seed Input Schema

```python
class SeedInput(BaseModel):
    """A contextually appropriate input for a persona to try."""
    input_text: str
    purpose: str  # Why this persona would type this
    expected_outcome: str  # What a working product should show
    is_edge_case: bool = False

class PersonaSeedInputs(BaseModel):
    persona_id: str
    inputs: list[SeedInput] = Field(default_factory=list)
```

Add `seed_inputs: list[SeedInput]` to `AgentPersona`.

### 4. Web Runner Integration

In the web runner's journey execution, when the product is `input_first`:

**Step 1:** Navigate to the URL, capture the landing state (screenshot + snapshot). Evaluate the input UX: is the purpose clear? Is the placeholder helpful? Is the CTA obvious?

**Step 2:** For each seed input in the persona's list:
  1. Find the input field (via accessibility tree — look for the primary text input or textarea)
  2. Type the seed input
  3. Submit (click the CTA button, or press Enter)
  4. Wait for results to load (with timeout)
  5. Capture screenshot + snapshot of the results
  6. Evaluate the results from the persona's perspective

**Step 3:** Also test error/empty cases:
  - Submit with empty input — does the product handle it gracefully?
  - Submit with obviously invalid input — does it show a helpful error?

### 5. Evaluation Prompts for Input-First Products

Add to the evaluation prompt when `input_first=True`:

```
This product requires user input to function. You typed: "{seed_input}"

Evaluate the results:
- Did the product respond appropriately to this input?
- Are the results relevant to what was typed?
- How long did results take to appear? Was there a loading indicator?
- Is it clear what the results mean and how to act on them?
- If no results were found, is the empty state helpful?
- Can the user easily modify their input and try again?
- Does the product suggest alternatives or corrections?
```

### 6. Quick Check Integration

In `preflight check`, if the page is detected as input-first, the quick check should:
1. Detect the input pattern
2. Auto-generate one simple seed input (no LLM call — use heuristics based on input_type)
3. Type it, submit, evaluate the results
4. Report on both the input UX and the results quality

Heuristic seed inputs for quick check (no LLM needed):
- search → "test"
- url → "example.com"
- prompt → "Hello, can you help me?"
- code → `print("hello")`
- free_text → "This is a test input"

### 7. Repo-Informed Seed Inputs

When repo insights are available, use them to generate smarter seed inputs:
- If the README has example queries/inputs, use those
- If there are test fixtures or sample data, derive inputs from them
- If the product description mentions specific use cases, generate inputs matching those use cases

---

## Build Order

1. Add `input_first`, `input_type`, `input_placeholder` to `ProductIntentModel` schema
2. Add input detection to the intent modeler (from page scrape + accessibility tree)
3. Add `SeedInput` schema and `seed_inputs` field to `AgentPersona`
4. Build seed input generator (LLM-based for full runs, heuristic for quick check)
5. Update web runner journey execution to handle input-first flows
6. Update evaluation prompts for input-first result evaluation
7. Update quick check to detect and handle input-first sites
8. Tests for all above
9. Push to origin

## Constraints

- Seed input generation uses smart tier (it needs to be contextually appropriate)
- The web runner typing/submitting uses deterministic Playwright actions, not LLM-guided clicks
- Always test the empty-input case — it reveals error handling quality
- Maximum 3 seed inputs per persona to keep run time reasonable
- Quick check uses heuristic inputs only (no LLM call) to stay under 2 minutes
