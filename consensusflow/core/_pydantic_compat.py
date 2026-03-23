"""
_pydantic_compat.py — Re-export Pydantic v2 symbols (always available here).

Pydantic is a required transitive dependency via litellm, so this module
simply re-exports the four symbols used by models.py.  The try/except is
kept as a safety net only.
"""

from pydantic import BaseModel, Field, field_validator, model_validator

PYDANTIC_AVAILABLE = True

__all__ = [
    "PYDANTIC_AVAILABLE",
    "BaseModel",
    "Field",
    "field_validator",
    "model_validator",
]
