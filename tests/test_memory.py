"""Tests for the self-learning product memory system."""

import json
import tempfile
from pathlib import Path

import pytest

from preflight.core.memory import (
    IssueFeedback,
    ProductMemory,
    ProductMemoryData,
    RunFeedback,
)


class TestProductMemory:
    """Tests for ProductMemory CRUD and context generation."""

    def _make_memory(self, tmpdir: str) -> ProductMemory:
        return ProductMemory(base_dir=tmpdir)

    def test_load_returns_empty_for_new_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            assert data.url == "https://example.com"
            assert data.run_count == 0
            assert data.issue_feedback == []

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            data.product_name = "TestApp"
            data.run_count = 3
            data.known_false_positives = ["Nav is confusing"]
            mem.save(data)

            loaded = mem.load("https://example.com")
            assert loaded.product_name == "TestApp"
            assert loaded.run_count == 3
            assert "Nav is confusing" in loaded.known_false_positives

    def test_url_key_deterministic(self):
        key1 = ProductMemory._url_key("https://example.com")
        key2 = ProductMemory._url_key("https://example.com")
        assert key1 == key2

    def test_url_key_trailing_slash_normalized(self):
        key1 = ProductMemory._url_key("https://example.com")
        key2 = ProductMemory._url_key("https://example.com/")
        assert key1 == key2

    def test_record_issue_feedback_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")

            mem.record_issue_feedback(
                data, issue_id="ISS-001", issue_title="Broken button",
                rating="valid", comment="Good catch",
            )

            assert len(data.issue_feedback) == 1
            assert data.issue_feedback[0].rating == "valid"
            assert data.issue_feedback[0].issue_title == "Broken button"

    def test_record_issue_feedback_false_positive_tracked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")

            mem.record_issue_feedback(
                data, issue_id="ISS-002", issue_title="Colors don't match",
                rating="false_positive",
            )

            assert "Colors don't match" in data.known_false_positives

    def test_record_issue_feedback_no_duplicate_fp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")

            mem.record_issue_feedback(
                data, issue_id="ISS-002", issue_title="Same issue",
                rating="false_positive",
            )
            mem.record_issue_feedback(
                data, issue_id="ISS-003", issue_title="Same issue",
                rating="false_positive",
            )

            assert data.known_false_positives.count("Same issue") == 1

    def test_record_run_feedback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")

            fb = RunFeedback(
                run_id="run-abc",
                overall_rating=4,
                useful_issues=["ISS-001"],
                false_positives=["ISS-002"],
                comments="Good run overall",
            )
            mem.record_run_feedback(data, fb)

            assert data.run_count == 1
            assert data.last_run is not None
            assert len(data.run_feedback) == 1
            assert data.run_feedback[0].overall_rating == 4

    def test_get_false_positive_titles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            data.known_false_positives = ["Issue A", "Issue B"]

            titles = mem.get_false_positive_titles(data)
            assert titles == ["Issue A", "Issue B"]

    def test_context_empty_for_new_product(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")

            ctx = mem.get_context_for_prompts(data)
            assert ctx == ""

    def test_context_includes_false_positives(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            data.run_count = 1
            data.known_false_positives = ["Nav issue", "Color mismatch"]

            ctx = mem.get_context_for_prompts(data)
            assert "KNOWN FALSE POSITIVES" in ctx
            assert "Nav issue" in ctx
            assert "Color mismatch" in ctx

    def test_context_includes_custom_guidance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            data.run_count = 1
            data.custom_guidance = "Focus on checkout flow"

            ctx = mem.get_context_for_prompts(data)
            assert "Focus on checkout flow" in ctx

    def test_context_includes_accuracy_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            data.run_count = 2

            # 2 valid, 1 false positive = 67% accuracy
            mem.record_issue_feedback(data, "ISS-1", "A", "valid")
            mem.record_issue_feedback(data, "ISS-2", "B", "valid")
            mem.record_issue_feedback(data, "ISS-3", "C", "false_positive")

            ctx = mem.get_context_for_prompts(data)
            assert "67%" in ctx
            assert "2 valid" in ctx

    def test_context_warns_on_low_accuracy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            data.run_count = 1

            # 1 valid, 3 false positives = 25% accuracy
            mem.record_issue_feedback(data, "ISS-1", "A", "valid")
            mem.record_issue_feedback(data, "ISS-2", "B", "false_positive")
            mem.record_issue_feedback(data, "ISS-3", "C", "false_positive")
            mem.record_issue_feedback(data, "ISS-4", "D", "false_positive")

            ctx = mem.get_context_for_prompts(data)
            assert "conservative" in ctx.lower()

    def test_reset_deletes_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")
            data.product_name = "WillBeDeleted"
            mem.save(data)

            assert mem.reset("https://example.com")
            loaded = mem.load("https://example.com")
            assert loaded.product_name == ""
            assert loaded.run_count == 0

    def test_reset_returns_false_if_no_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            assert not mem.reset("https://never-seen.com")

    def test_list_products(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)

            d1 = mem.load("https://example.com")
            d1.product_name = "Example"
            d1.run_count = 2
            mem.save(d1)

            d2 = mem.load("https://other.com")
            d2.product_name = "Other"
            d2.run_count = 1
            mem.save(d2)

            products = mem.list_products()
            assert len(products) == 2
            names = {p["product_name"] for p in products}
            assert names == {"Example", "Other"}

    def test_set_custom_guidance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")

            mem.set_custom_guidance(data, "This is a B2B SaaS dashboard")
            assert data.custom_guidance == "This is a B2B SaaS dashboard"

    def test_update_learned_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            data = mem.load("https://example.com")

            mem.update_learned_context(data, "Product uses React with dark theme")
            assert data.learned_product_context == "Product uses React with dark theme"

    def test_corrupted_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = self._make_memory(tmpdir)
            # Write garbage
            path = mem._path_for("https://example.com")
            path.write_text("not json{{{")

            data = mem.load("https://example.com")
            assert data.url == "https://example.com"
            assert data.run_count == 0
