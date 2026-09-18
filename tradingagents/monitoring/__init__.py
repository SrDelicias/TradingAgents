"""Structured, local-only observability for the dashboard."""

from .events import EventLog, instrument_node

__all__ = ["EventLog", "instrument_node"]
