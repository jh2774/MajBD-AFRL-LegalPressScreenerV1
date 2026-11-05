"""Data source connectors.

Each connector owns exactly one upstream system and returns `Document` objects
(or enriches `Contract` objects). None of them raise on upstream failure — a
dead endpoint degrades the screen, it does not end it.
"""
from .fpds import FPDSConnector
from .registries import IAPDConnector, SAMConnector, USPTOConnector
from .screening_lists import ScreeningLists
from .sec_edgar import EdgarConnector
from .usaspending import USASpendingConnector
from .webwatch import WebWatchConnector

__all__ = ["FPDSConnector", "IAPDConnector", "ScreeningLists", "SAMConnector",
           "USPTOConnector", "EdgarConnector", "USASpendingConnector",
           "WebWatchConnector"]
