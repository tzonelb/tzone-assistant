"""Errors the kernel understands."""

from __future__ import annotations


class ValidationError(ValueError):
    """A record must not be stored. The message is reported back to the client verbatim."""


class ModuleError(RuntimeError):
    """A module manifest is malformed, or its dependencies cannot be satisfied."""
