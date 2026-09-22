"""foci-screen — contract and contractor risk screening.

Screens federal contract awards and the awardee's public disclosures for
changes indicating Foreign Ownership, Control or Influence (FOCI),
intellectual-property collateralisation, or other IP risk, and drafts a notice
to the responsible contracting officer.
"""
__version__ = "0.1.0"

from .config import Config, get_config
from .models import Contract, Entity, Finding, Signal
from .pipeline import Screener, ScreenOptions
from .store import Store

__all__ = ["Config", "get_config", "Contract", "Entity", "Finding", "Signal",
           "ScreenOptions", "Screener", "Store", "__version__"]
