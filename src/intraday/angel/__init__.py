"""
Angel One SmartAPI Integration Module

Provides Angel One broker integration for intraday trading.
Completely separate from Zerodha Kite integration.
"""
from .auth import AngelAuth
from .client import AngelClient

__all__ = ['AngelAuth', 'AngelClient']
