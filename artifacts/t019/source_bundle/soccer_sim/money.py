"""Exact nonnegative money in integer millionths; never round or use float."""
from __future__ import annotations

import re
from typing import Any

MONEY_SCALE = 1_000_000
MONEY_VERSION = "integer-micro-v1"


def validate_money_units(value: Any, name: str = "money units") -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def money_units(value: str) -> int:
    """Parse an unsigned decimal string, rejecting nonzero sub-unit digits."""
    if type(value) is not str or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None:
        raise ValueError("money must be an unsigned decimal string")
    whole, _, fraction = value.partition(".")
    if any(digit != "0" for digit in fraction[6:]):
        raise ValueError("money has nonzero digits below the minimum unit")
    return int(whole) * MONEY_SCALE + int((fraction[:6] + "000000")[:6])


def format_money(value_units: int) -> str:
    """Format validated integer units as a canonical decimal string."""
    validate_money_units(value_units)
    whole, fraction = divmod(value_units, MONEY_SCALE)
    return str(whole) + ("." + f"{fraction:06d}".rstrip("0") if fraction else "")
