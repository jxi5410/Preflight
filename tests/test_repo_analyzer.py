"""Tests for repo analyzer, updated schemas, and enhanced intent modeler."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from humanqa.core.schemas import (
    FeatureExpectation,
    ProductIntentModel,
    RepoInsights,
    RunConfig,
)
from humanqa.core.repo_analyzer import (
    RepoAnalyzer,
    _find_routes,
    _parse_github_owner_repo,
    _parse_tech_stack_from_manifest,
    _read_config_hints,
)
from humanqa.core.intent_modeler import IntentModeler


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestRepoInsightsSchema:
    def test_defaults(self):
        ri = RepoInsights()
        assert ri.product_name == ""
        assert ri.repo_confidence == 0.0
        assert ri.tech_stack == []

    def test_full_construction(self):
        ri = RepoInsights(
            product_name="MyApp",
            description="A cool app",
            tech_stack=["Next.js", "TypeScript"],
            claimed_features=["dark mode", "auth"],
            routes_or_pages=["/", "/dashboard", "/settings"],
            recent_changes=["Fix login bug"],
            known_issues=["Slow load times"],
            configuration_hints=["[.env.example]: DATABASE_URL=..."],
            documentation_summary="MyApp is a dashboard for metrics.",
            repo_confidence=0.75,
        )
        assert ri.product_name == "MyApp"
        assert len(ri.tech_stack) == 2
        assert ri.repo_confidence == 0.75

    def test_json_roundtrip(self):
        ri = RepoInsights(
            product_name="Test",
            claimed_features=["feature1"],
            repo_confidence=0.5,
        )
        data = json.loads(ri.model_dump_json())
        assert data["product_name"] == "Test"
        ri2 = RepoInsights(**data)
        assert ri2.claimed_features == ["feature1"]


class TestFeatureExpectationSchema:
    def test_basic(self):
        fe = FeatureExpectation(feature_name="Dark mode", source="README")
        assert fe.feature_name == "Dark mode"
        assert fe.verified is None

    def test_verified_states(self):
        fe_unverified = FeatureExpectation(feature_name="Auth")
        fe_verified = FeatureExpectation(feature_name="Auth", verified=True)
        fe_failed = FeatureExpectation(feature_name="Auth", verified=False)
        assert fe_unverified.verified is None
        assert fe_verified.verified is True
        assert fe_failed.verified is False


class TestUpdatedProductIntentModel:
    def test_with_repo_insights(self):
        ri = RepoInsights(product_name="MyApp", repo_confidence=0.8)
        model = ProductIntentModel(
            product_name="MyApp",
            repo_insights=ri,
            feature_expectations=[
                FeatureExpectation(feature_name="search", source="README"),
            ],
        )
        assert model.repo_insights is not None
        assert model.repo_insights.product_name == "MyApp"
        assert len(model.feature_expectations) == 1

    def test_backward_compatible_without_repo(self):
        model = ProductIntentModel(product_name="NoRepo", confidence=0.5)
        assert model.repo_insights is None
        assert model.feature_expectations == []


class TestUpdatedRunConfig:
    def test_repo_url_default(self):
        config = RunConfig(target_url="https://example.com")
        assert config.repo_url is None
        assert config.github_token_env == "GITHUB_TOKEN"

    def test_repo_url_set(self):
        config = RunConfig(
            target_url="https://example.com",
            repo_url="https://github.com/user/repo",
            github_token_env="MY_TOKEN",
        )
        assert config.repo_url == "https://github.com/user/repo"
        assert config.github_token_env == "MY_TOKEN"


# ---------------------------------------------------------------------------
# Repo analyzer helper tests
# ---------------------------------------------------------------------------


class TestParseGitHubOwnerRepo:
    def test_https_url(self):
        owner, repo = _parse_github_owner_repo("https://github.com/user/repo")
        assert owner == "user"
        assert repo == "repo"

    def test_https_with_git_suffix(self):
        owner, repo = _parse_github_owner_repo("https://github.com/org/project.git")
        assert owner == "org"
        assert repo == "project"

    def test_ssh_url(self):
        owner, repo = _parse_github_owner_repo("git@github.com:org/project.git")
        assert owner == "org"
        assert repo == "project"

    def test_invalid_url(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_github_owner_repo("https://gitlab.com/user/repo")


class TestParseTechStack:
    def test_package_json(self, tmp_path):
        pkg = {
            "dependencies": {"react": "^18.0.0", "next": "^14.0.0"},
            "devDependencies": {"typescript": "^5.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        stack = _parse_tech_stack_from_manifest(tmp_path)
        assert "Next.js" in stack
        assert "React" in stack
        assert "TypeScript" in stack

    def test_pyproject_toml(self, tmp_path):
        toml_content = """
[project]
dependencies = ["fastapi>=0.100", "uvicorn"]
"""
        (tmp_path / "pyproject.toml").write_text(toml_content)
        stack = _parse_tech_stack_from_manifest(tmp_path)
        assert "FastAPI" in stack
        assert "Python" in stack

    def test_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'myapp'")
        stack = _parse_tech_stack_from_manifest(tmp_path)
        assert "Rust" in stack

    def test_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text("module github.com/user/repo")
        stack = _parse_tech_stack_from_manifest(tmp_path)
        assert "Go" in stack

    def test_empty_dir(self, tmp_path):
        stack = _parse_tech_stack_from_manifest(tmp_path)
        assert stack == []


class TestFindRoutes:
    def test_nextjs_pages(self, tmp_path):
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "index.tsx").touch()
        (pages / "about.tsx").touch()
        (pages / "dashboard").mkdir()
        (pages / "dashboard" / "index.tsx").touch()
        routes = _find_routes(tmp_path)
        assert "/" in routes
        assert "/about" in routes
        assert "/dashboard" in routes

    def test_nextjs_app_router(self, tmp_path):
        app = tmp_path / "app"
        app.mkdir()
        (app / "page.tsx").touch()
        (app / "settings").mkdir()
        (app / "settings" / "page.tsx").touch()
        routes = _find_routes(tmp_path)
        assert "/" in routes
        assert "/settings" in routes

    def test_no_route_dirs(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        routes = _find_routes(tmp_path)
        assert routes == []


class TestReadConfigHints:
    def test_env_example(self, tmp_path):
        (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://...\nREDIS_URL=redis://...")
        hints = _read_config_hints(tmp_path)
        assert len(hints) == 1
        assert "DATABASE_URL" in hints[0]

    def test_no_configs(self, tmp_path):
        hints = _read_config_hints(tmp_path)
        assert hints == []


# ---------------------------------------------------------------------------
# RepoAnalyzer integration tests (mocked git/API/LLM)
# ---------------------------------------------------------------------------


class TestRepoAnalyzerMocked:
    @pytest.mark.asyncio
    async def test_analyze_with_mocked_clone(self, tmp_path):
        """Test the full analyze flow with mocked subprocess and API calls."""
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "product_name": "TestApp",
            "description": "A test application",
            "claimed_features": ["auth", "dashboard"],
            "documentation_summary": "TestApp lets you manage things.",
        }

        analyzer = RepoAnalyzer(mock_llm)

        # We'll mock subprocess.run and _fetch_github_data
        def fake_clone(args, **kwargs):
            # Create the repo directory with some files
            repo_dir = Path(args[-1])
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "README.md").write_text("# TestApp\nA test application with auth and dashboard.")
            (repo_dir / "package.json").write_text(json.dumps({
                "dependencies": {"react": "^18", "next": "^14"},
            }))
            pages = repo_dir / "pages"
            pages.mkdir()
            (pages / "index.tsx").touch()
            (pages / "dashboard.tsx").touch()
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("humanqa.core.repo_analyzer.subprocess.run", side_effect=fake_clone), \
             patch("humanqa.core.repo_analyzer._fetch_github_data", new_callable=AsyncMock) as mock_gh:
            mock_gh.return_value = {
                "recent_prs": ["Fix login bug", "Add dark mode"],
                "open_issues": ["Performance issue on dashboard"],
            }
            insights = await analyzer.analyze("https://github.com/user/testapp")

        assert insights.product_name == "TestApp"
        assert "React" in insights.tech_stack
        assert "Next.js" in insights.tech_stack
        assert "/dashboard" in insights.routes_or_pages
        assert len(insights.recent_changes) == 2
        assert len(insights.known_issues) == 1
        assert insights.repo_confidence > 0

    @pytest.mark.asyncio
    async def test_analyze_clone_failure(self):
        """If git clone fails, return minimal RepoInsights."""
        mock_llm = MagicMock()
        analyzer = RepoAnalyzer(mock_llm)

        def fake_fail(args, **kwargs):
            result = MagicMock()
            result.returncode = 128
            result.stderr = "fatal: repo not found"
            return result

        with patch("humanqa.core.repo_analyzer.subprocess.run", side_effect=fake_fail):
            insights = await analyzer.analyze("https://github.com/user/missing")

        assert insights.product_name == "missing"
        assert insights.repo_confidence == 0.1


# ---------------------------------------------------------------------------
# Enhanced IntentModeler tests
# ---------------------------------------------------------------------------


class TestEnhancedIntentModeler:
    @pytest.mark.asyncio
    async def test_build_with_repo_insights(self):
        """IntentModeler should incorporate repo insights and produce feature expectations."""
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "product_name": "TestApp",
            "product_type": "SaaS Dashboard",
            "target_audience": ["developers"],
            "primary_jobs": ["monitor metrics"],
            "user_expectations": ["fast load"],
            "critical_journeys": ["login", "view dashboard"],
            "trust_sensitive_actions": ["delete account"],
            "institutional_relevance": "moderate",
            "institutional_reasoning": "Used by teams",
            "assumptions": [],
            "confidence": 0.9,
            "feature_expectations": [
                {"feature_name": "dark mode", "source": "README"},
                {"feature_name": "auth", "source": "README"},
            ],
        }

        repo_insights = RepoInsights(
            product_name="TestApp",
            description="A dashboard",
            tech_stack=["React"],
            claimed_features=["dark mode", "auth"],
            repo_confidence=0.7,
        )

        modeler = IntentModeler(mock_llm)
        config = RunConfig(target_url="https://example.com")
        model = await modeler.build_intent_model(config, "page content", repo_insights)

        assert model.product_name == "TestApp"
        assert model.repo_insights is not None
        assert model.repo_insights.repo_confidence == 0.7
        assert len(model.feature_expectations) == 2
        assert model.feature_expectations[0].feature_name == "dark mode"

    @pytest.mark.asyncio
    async def test_build_without_repo_insights(self):
        """IntentModeler should work without repo insights (backward compatible)."""
        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "product_name": "SimpleApp",
            "product_type": "Website",
            "target_audience": ["visitors"],
            "primary_jobs": ["browse"],
            "user_expectations": [],
            "critical_journeys": ["visit homepage"],
            "trust_sensitive_actions": [],
            "institutional_relevance": "none",
            "institutional_reasoning": "",
            "assumptions": [],
            "confidence": 0.6,
            "feature_expectations": [],
        }

        modeler = IntentModeler(mock_llm)
        config = RunConfig(target_url="https://example.com")
        model = await modeler.build_intent_model(config, "page content")

        assert model.product_name == "SimpleApp"
        assert model.repo_insights is None
        assert model.feature_expectations == []

    @pytest.mark.asyncio
    async def test_build_llm_failure_with_repo(self):
        """On LLM failure, repo insights should still be attached to fallback model."""
        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = Exception("API error")

        repo_insights = RepoInsights(product_name="FailApp", repo_confidence=0.5)
        modeler = IntentModeler(mock_llm)
        config = RunConfig(target_url="https://example.com")
        model = await modeler.build_intent_model(config, "content", repo_insights)

        assert model.product_name == "Unknown"
        assert model.confidence == 0.1
        assert model.repo_insights is not None
        assert model.repo_insights.product_name == "FailApp"

    def test_format_repo_insights_none(self):
        """Formatting None repo insights should return placeholder."""
        modeler = IntentModeler(MagicMock())
        result = modeler._format_repo_insights(None)
        assert "no repository analysis" in result

    def test_format_repo_insights_populated(self):
        """Formatting populated repo insights should include key sections."""
        modeler = IntentModeler(MagicMock())
        ri = RepoInsights(
            product_name="MyApp",
            tech_stack=["React", "Node.js"],
            claimed_features=["search", "filters"],
            routes_or_pages=["/", "/search"],
            recent_changes=["Add search feature"],
        )
        result = modeler._format_repo_insights(ri)
        assert "MyApp" in result
        assert "React" in result
        assert "search" in result
        assert "recently changed" in result.lower() or "Recent changes" in result
