"""Deterministic descriptive and inferential synthesis."""
from .engine import descriptive, inferential
from .effects import effect_size, validate_covariance

__all__ = ["descriptive", "inferential", "effect_size", "validate_covariance"]
