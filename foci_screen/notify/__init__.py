"""Notice rendering and delivery.

Delivery is inert by default: see `gmail.py` for the three guards that must be
cleared before a message reaches a contracting officer.
"""
from . import gmail, render

__all__ = ["gmail", "render"]
