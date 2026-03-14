"""Repository Analyzer for HumanQA.

Clones/accesses a GitHub repo and extracts product understanding.
Reads only what is listed in the "MAY read" boundary:
  - README, docs/, CHANGELOG
  - Package manifests (package.json, pyproject.toml, Cargo.toml, etc.)
  - Route/page structure (file names and directory layout only)
  - Recent PRs and open issues (titles and descriptions)
  - Configuration files (env.example, config files)

Never reads function/class bodies or judges code quality.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from humanqa.core.llm import LLMClient
from humanqa.core.schemas import RepoInsights

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SUMMARIZE_SYSTEM_PROMPT = """You are a product analyst for HumanQA.
Your job is to understand what a product IS and DOES by reading its repository documentation.
You must NEVER judge code quality or architecture. You only extract product intent.
Respond ONLY with valid JSON. No markdown fences, no preamble."""

SUMMARIZE_PROMPT_TEMPLATE = """Analyze the following repository documentation and extract product understanding.

## README
{readme}

## Documentation Files
{docs}

## CHANGELOG / Release Notes
{changelog}

## Configuration Hints
{config_hints}

Respond with a JSON object:
{{
  "product_name": "string — the product name",
  "description": "string — what the product does in 1-2 sentences",
  "claimed_features": ["list of features the product claims to offer"],
  "documentation_summary": "string — condensed understanding of the product"
}}"""

# ---------------------------------------------------------------------------
# Package manifest parsers
# ---------------------------------------------------------------------------

ROUTE_DIRS = [
    "pages", "app", "routes", "views", "screens",
    "src/pages", "src/app", "src/routes", "src/views", "src/screens",
    "app/routes", "app/views",
]

MANIFEST_FILES = [
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "build.gradle",
    "pom.xml",
    "composer.json",
    "mix.exs",
]

CONFIG_FILES = [
    ".env.example",
    ".env.sample",
    "env.example",
    "config.yaml",
    "config.yml",
    "config.json",
    "config.toml",
    ".config.js",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vite.config.ts",
    "vite.config.js",
    "nuxt.config.ts",
    "angular.json",
    "svelte.config.js",
]

DOC_FILES = [
    "CHANGELOG.md",
    "CHANGELOG",
    "CHANGES.md",
    "HISTORY.md",
    "RELEASE_NOTES.md",
]


def _parse_tech_stack_from_manifest(repo_path: Path) -> list[str]:
    """Extract tech stack from package manifest files."""
    stack: list[str] = []

    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(errors="replace"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            # Identify major frameworks/tools
            framework_markers = {
                "next": "Next.js", "react": "React", "vue": "Vue.js",
                "angular": "Angular", "svelte": "Svelte", "express": "Express",
                "fastify": "Fastify", "nuxt": "Nuxt", "gatsby": "Gatsby",
                "tailwindcss": "Tailwind CSS", "typescript": "TypeScript",
                "prisma": "Prisma", "drizzle-orm": "Drizzle",
            }
            for dep, label in framework_markers.items():
                if dep in deps or f"@{dep}" in str(deps):
                    stack.append(label)
            if not stack:
                stack.append("Node.js")
        except (json.JSONDecodeError, OSError):
            pass

    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        try:
            data = tomllib.loads(pyproject.read_text(errors="replace"))
            deps = data.get("project", {}).get("dependencies", [])
            dep_str = " ".join(deps)
            py_markers = {
                "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
                "starlette": "Starlette", "streamlit": "Streamlit",
            }
            for marker, label in py_markers.items():
                if marker in dep_str.lower():
                    stack.append(label)
            if not stack or "Python" not in stack:
                stack.append("Python")
        except Exception:
            pass

    cargo = repo_path / "Cargo.toml"
    if cargo.exists():
        stack.append("Rust")

    go_mod = repo_path / "go.mod"
    if go_mod.exists():
        stack.append("Go")

    return stack


def _find_routes(repo_path: Path) -> list[str]:
    """Extract route/page names from directory structure."""
    routes: list[str] = []
    for route_dir in ROUTE_DIRS:
        d = repo_path / route_dir
        if d.is_dir():
            for item in sorted(d.rglob("*")):
                rel = item.relative_to(d)
                # Skip hidden files, node_modules, __pycache__
                parts = rel.parts
                if any(p.startswith(".") or p in ("node_modules", "__pycache__") for p in parts):
                    continue
                if item.is_file():
                    # Convert file path to route-like string
                    route = "/" + "/".join(parts)
                    # Remove common extensions
                    route = re.sub(r"\.(tsx?|jsx?|vue|svelte|py|rb|go|rs)$", "", route)
                    # Remove index suffix
                    route = re.sub(r"/index$", "", route) or "/"
                    # Remove page/layout suffixes (Next.js app router)
                    route = re.sub(r"/(page|layout|loading|error|not-found)$", "", route) or "/"
                    if route not in routes:
                        routes.append(route)
    return routes[:50]  # Cap to avoid noise


def _read_config_hints(repo_path: Path) -> list[str]:
    """Extract feature hints from config/env files."""
    hints: list[str] = []
    for cfg_name in CONFIG_FILES:
        cfg = repo_path / cfg_name
        if cfg.exists():
            try:
                content = cfg.read_text(errors="replace")[:2000]
                hints.append(f"[{cfg_name}]: {content[:500]}")
            except OSError:
                pass
    return hints


def _read_docs(repo_path: Path) -> str:
    """Read documentation directory content."""
    docs_dir = repo_path / "docs"
    if not docs_dir.is_dir():
        return ""
    parts: list[str] = []
    for f in sorted(docs_dir.rglob("*.md"))[:10]:  # Cap at 10 doc files
        try:
            content = f.read_text(errors="replace")[:3000]
            parts.append(f"### {f.relative_to(repo_path)}\n{content}")
        except OSError:
            pass
    return "\n\n".join(parts)[:12000]


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _parse_github_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub URL."""
    # Handle https://github.com/owner/repo or git@github.com:owner/repo.git
    match = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", repo_url)
    if not match:
        raise ValueError(f"Cannot parse GitHub owner/repo from URL: {repo_url}")
    return match.group(1), match.group(2)


async def _fetch_github_data(
    owner: str, repo: str, token: str | None
) -> dict[str, Any]:
    """Fetch PRs and issues from the GitHub API."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    result: dict[str, Any] = {"recent_prs": [], "open_issues": [], "is_public": None}

    async with httpx.AsyncClient(timeout=30) as client:
        # Check repo visibility
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers=headers,
            )
            if resp.status_code == 200:
                repo_data = resp.json()
                result["is_public"] = not repo_data.get("private", True)
            elif resp.status_code == 404:
                # 404 without auth means private; with auth means truly not found
                result["is_public"] = False if not token else None
        except httpx.HTTPError:
            pass
        # Recent PRs (last 10, sorted by updated)
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                params={"state": "all", "sort": "updated", "per_page": 10},
                headers=headers,
            )
            if resp.status_code == 200:
                for pr in resp.json():
                    title = pr.get("title", "")
                    body = (pr.get("body") or "")[:200]
                    result["recent_prs"].append(f"{title}: {body}" if body else title)
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch PRs: %s", e)

        # Open issues (last 20)
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                params={"state": "open", "per_page": 20},
                headers=headers,
            )
            if resp.status_code == 200:
                for issue in resp.json():
                    # GitHub API returns PRs as issues too; skip them
                    if "pull_request" in issue:
                        continue
                    result["open_issues"].append(issue.get("title", ""))
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch issues: %s", e)

    return result


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class RepoAnalyzer:
    """Analyzes a GitHub repository to extract product understanding."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def analyze(self, repo_url: str, github_token_env: str = "GITHUB_TOKEN") -> RepoInsights:
        """Clone the repo, read allowed files, and produce RepoInsights."""
        owner, repo_name = _parse_github_owner_repo(repo_url)
        token = os.environ.get(github_token_env)

        logger.info("Analyzing repository: %s/%s", owner, repo_name)

        # Shallow clone into a temp directory
        tmp_dir = tempfile.mkdtemp(prefix="humanqa_repo_")
        repo_path = Path(tmp_dir) / repo_name
        try:
            clone_url = repo_url
            if token and "github.com" in repo_url:
                clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
            elif not repo_url.endswith(".git"):
                clone_url = repo_url + ".git" if "github.com" in repo_url else repo_url

            logger.info("Shallow cloning %s ...", repo_url)
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--single-branch", clone_url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                logger.error("git clone failed: %s", proc.stderr)
                return RepoInsights(product_name=repo_name, repo_confidence=0.1)

            # Extract local data
            readme = self._read_readme(repo_path)
            tech_stack = _parse_tech_stack_from_manifest(repo_path)
            routes = _find_routes(repo_path)
            config_hints = _read_config_hints(repo_path)
            docs_content = _read_docs(repo_path)
            changelog = self._read_changelog(repo_path)

            # Fetch GitHub API data (PRs, issues)
            gh_data = await _fetch_github_data(owner, repo_name, token)

            # Use LLM to summarize documentation into structured understanding
            llm_summary = self._summarize_docs(readme, docs_content, changelog, config_hints)

            # Compute confidence based on how much data we found
            signals = sum([
                bool(readme),
                bool(tech_stack),
                len(routes) > 0,
                bool(docs_content),
                bool(changelog),
                len(gh_data["recent_prs"]) > 0,
                len(gh_data["open_issues"]) > 0,
                len(config_hints) > 0,
            ])
            confidence = min(signals / 8.0, 1.0)

            return RepoInsights(
                product_name=llm_summary.get("product_name", repo_name),
                description=llm_summary.get("description", ""),
                tech_stack=tech_stack,
                claimed_features=llm_summary.get("claimed_features", []),
                routes_or_pages=routes,
                recent_changes=gh_data["recent_prs"][:10],
                known_issues=gh_data["open_issues"][:20],
                configuration_hints=[h[:200] for h in config_hints],
                documentation_summary=llm_summary.get("documentation_summary", ""),
                repo_confidence=round(confidence, 2),
                is_public=gh_data.get("is_public"),
            )
        finally:
            # Clean up temp directory
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _read_readme(self, repo_path: Path) -> str:
        """Read the README file."""
        for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
            f = repo_path / name
            if f.exists():
                try:
                    return f.read_text(errors="replace")[:8000]
                except OSError:
                    pass
        return ""

    def _read_changelog(self, repo_path: Path) -> str:
        """Read changelog / release notes."""
        for name in DOC_FILES:
            f = repo_path / name
            if f.exists():
                try:
                    return f.read_text(errors="replace")[:4000]
                except OSError:
                    pass
        return ""

    def _summarize_docs(
        self, readme: str, docs: str, changelog: str, config_hints: list[str]
    ) -> dict[str, Any]:
        """Use LLM to summarize documentation into structured product understanding."""
        if not readme and not docs:
            return {}

        prompt = SUMMARIZE_PROMPT_TEMPLATE.format(
            readme=readme[:6000] or "(no README found)",
            docs=docs[:6000] or "(no docs/ directory)",
            changelog=changelog[:3000] or "(no changelog)",
            config_hints="\n".join(config_hints[:5]) or "(none)",
        )

        try:
            return self.llm.complete_json(prompt, system=SUMMARIZE_SYSTEM_PROMPT)
        except Exception as e:
            logger.warning("LLM summarization failed: %s", e)
            return {}
