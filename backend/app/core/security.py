"""Restricted demo mode: write/analysis endpoints need X-Demo-Key. Sample investigations are public."""
from __future__ import annotations

import hmac

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import Unauthorized


def key_is_valid(provided: str | None) -> bool:
    expected = get_settings().demo_access_key
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def require_demo_key(x_demo_key: str | None = Header(default=None)) -> None:
    if not key_is_valid(x_demo_key):
        raise Unauthorized("A valid demo access key is required for this action.")


def has_demo_key(x_demo_key: str | None = Header(default=None)) -> bool:
    """Non-raising variant for read routes that expose more data to key holders."""
    return key_is_valid(x_demo_key)
