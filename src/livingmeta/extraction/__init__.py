"""Evidence-preserving document extraction and calibrated image measurements."""

from .layout import inspect_pdf


def extract_pdf(*args, **kwargs):
    """Load the optional hosted/API pipeline only when explicitly requested."""
    from .pipeline import extract_pdf as implementation
    return implementation(*args, **kwargs)

__all__ = ["extract_pdf", "inspect_pdf"]
