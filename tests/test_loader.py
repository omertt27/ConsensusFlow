"""
tests/test_loader.py — Tests for consensusflow/prompts/loader.py

Covers:
  • load_prompt resolves real prompt files from disk
  • load_prompt raises PromptNotFoundError for missing names
  • register_prompt_override bypasses disk
  • clear_prompt_overrides removes overrides
  • LRU cache is invalidated on override registration/clearing
"""

from __future__ import annotations

import pytest

from consensusflow.prompts.loader import (
    load_prompt,
    register_prompt_override,
    clear_prompt_overrides,
)
from consensusflow.exceptions import PromptNotFoundError


@pytest.fixture(autouse=True)
def _clean_overrides():
    """Ensure overrides and cache are clean before/after every test."""
    clear_prompt_overrides()
    yield
    clear_prompt_overrides()


class TestLoadPrompt:
    def test_loads_real_prompt(self):
        """Verifies that 'adversarial', 'synthesis', 'extractor' prompts exist on disk."""
        for name in ("adversarial", "synthesis", "extractor"):
            content = load_prompt(name)
            assert isinstance(content, str)
            assert len(content) > 10, f"prompt '{name}' looks empty"

    def test_missing_prompt_raises(self):
        with pytest.raises(PromptNotFoundError):
            load_prompt("does_not_exist_xyz_abc")

    def test_error_message_contains_name(self):
        with pytest.raises(PromptNotFoundError, match="does_not_exist_xyz_abc"):
            load_prompt("does_not_exist_xyz_abc")

    def test_result_is_string(self):
        content = load_prompt("adversarial")
        assert isinstance(content, str)
        assert content  # non-empty

    def test_caching_returns_same_object(self):
        """LRU cache should return the exact same object on repeated calls."""
        a = load_prompt("adversarial")
        b = load_prompt("adversarial")
        assert a is b


class TestRegisterPromptOverride:
    def test_override_bypasses_disk(self):
        register_prompt_override("adversarial", "CUSTOM SYSTEM PROMPT")
        assert load_prompt("adversarial") == "CUSTOM SYSTEM PROMPT"

    def test_override_new_name(self):
        register_prompt_override("my_custom_prompt", "Hello from override!")
        assert load_prompt("my_custom_prompt") == "Hello from override!"

    def test_override_invalidates_cache(self):
        # Load once to populate cache
        original = load_prompt("adversarial")
        # Register override — must NOT return cached value
        register_prompt_override("adversarial", "OVERRIDDEN")
        assert load_prompt("adversarial") == "OVERRIDDEN"
        assert load_prompt("adversarial") != original

    def test_multiple_overrides(self):
        register_prompt_override("p1", "prompt one")
        register_prompt_override("p2", "prompt two")
        assert load_prompt("p1") == "prompt one"
        assert load_prompt("p2") == "prompt two"


class TestClearPromptOverrides:
    def test_clear_removes_custom_overrides(self):
        register_prompt_override("my_prompt", "custom content")
        clear_prompt_overrides()
        with pytest.raises(PromptNotFoundError):
            load_prompt("my_prompt")

    def test_clear_restores_disk_prompts(self):
        """After clearing, real prompts should load from disk again."""
        original = load_prompt("adversarial")
        register_prompt_override("adversarial", "TEMP OVERRIDE")
        assert load_prompt("adversarial") == "TEMP OVERRIDE"
        clear_prompt_overrides()
        assert load_prompt("adversarial") == original

    def test_clear_is_idempotent(self):
        """Calling clear when nothing is registered should not raise."""
        clear_prompt_overrides()
        clear_prompt_overrides()
