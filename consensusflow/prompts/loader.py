"""
loader.py — Loads prompt templates from the prompts/ directory.

Features:
  • LRU cache with bounded size (maxsize=128)
  • Runtime override via register_prompt_override() / clear_prompt_overrides()
  • Clear error messages that include the searched paths
"""

from __future__ import annotations

import os
from functools import lru_cache

_PROMPTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "prompts")
)

# Runtime overrides: name → content.  Checked before disk lookup.
_OVERRIDES: dict[str, str] = {}


def register_prompt_override(name: str, content: str) -> None:
    """
    Register an in-memory override for a named prompt.

    Useful for testing or runtime customisation without touching files.
    The override bypasses the LRU cache entirely — subsequent calls to
    ``load_prompt(name)`` will return the override until it is removed.

    Example::

        register_prompt_override("adversarial", "You are a strict fact-checker...")
    """
    _OVERRIDES[name] = content
    # Invalidate cached version so the override takes effect immediately
    load_prompt.cache_clear()


def clear_prompt_overrides() -> None:
    """Remove all in-memory prompt overrides and clear the LRU cache."""
    _OVERRIDES.clear()
    load_prompt.cache_clear()


@lru_cache(maxsize=128)
def load_prompt(name: str) -> str:
    """
    Load a prompt template by name (without extension).

    Resolution order:
      1. In-memory override registered via ``register_prompt_override()``
      2. consensusflow/prompts/<name>.md
      3. consensusflow/prompts/<name>.txt

    Returns the file content as a string.
    Raises PromptNotFoundError if neither override nor file exists.
    """
    # Check in-memory override first (cache is invalidated on override change)
    if name in _OVERRIDES:
        return _OVERRIDES[name]

    searched: list[str] = []
    for ext in (".md", ".txt"):
        path = os.path.normpath(os.path.join(_PROMPTS_DIR, f"{name}{ext}"))
        searched.append(path)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()

    from consensusflow.exceptions import PromptNotFoundError
    raise PromptNotFoundError(
        f"Prompt template '{name}' not found. Searched:\n"
        + "\n".join(f"  {p}" for p in searched)
    )
