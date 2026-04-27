"""Shared slowapi rate limiter instance (Fix 37).

Defined in a dedicated module to avoid circular imports between
``app.main`` and the API route modules that need the decorator.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
