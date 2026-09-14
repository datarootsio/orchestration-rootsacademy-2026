"""Rootsmarkt data-processing layer.

THIS PACKAGE IS THE DATA-PROCESSING LAYER. It reads, transforms, aggregates,
writes and validates data. It decides nothing about when work runs, what depends
on what, what should be retried, or what "success" means. Those are orchestration
questions, and they belong in your DAGs and assets.

Sealed. Do not modify, except where mission D3 says so.
"""

__all__ = ["clean", "config", "load", "revenue", "source", "validate"]
__version__ = "1.0.0"
