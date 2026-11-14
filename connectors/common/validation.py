"""Fail-closed boundary parsing helpers for financial numbers, text, and JSON."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Callable


def _reject_nonfinite_json_token(value: str) -> Any:
    raise ValueError(f"Non-finite JSON numeric token: {value!r}")


def finite_json_loads(
    raw: bytes | str,
    *,
    parse_float: Callable[[str], Any] | None = None,
) -> Any:
    """Decode JSON while rejecting NaN, Infinity, and -Infinity constants."""
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    kwargs: dict[str, Any] = {"parse_constant": _reject_nonfinite_json_token}
    if parse_float is not None:
        kwargs["parse_float"] = parse_float
    return json.loads(text, **kwargs)


def strict_decimal(
    value: Any,
    field: str = "value",
    *,
    error_cls: type[Exception] = ValueError,
) -> Decimal:
    """Convert an untrusted numeric/string input to a finite Decimal or fail closed."""
    if value is None or isinstance(value, bool):
        raise error_cls(f"Missing or invalid numeric field {field!r}: {value!r}")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, (int, float)):
        try:
            dec = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise error_cls(f"Invalid numeric field {field!r}: {value!r}") from exc
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise error_cls(f"Empty numeric field {field!r}")
        try:
            dec = Decimal(stripped)
        except (InvalidOperation, ValueError) as exc:
            raise error_cls(f"Invalid numeric field {field!r}: {value!r}") from exc
    else:
        raise error_cls(f"Unsupported type for numeric field {field!r}: {type(value).__name__}")

    if not dec.is_finite():
        raise error_cls(f"Non-finite numeric field {field!r}: {value!r}")
    tup = dec.as_tuple()
    if len(tup.digits) > 256 or (isinstance(tup.exponent, int) and abs(tup.exponent) > 1000):
        raise error_cls(f"Out-of-bounds numeric field {field!r}: {value!r}")
    return dec


def optional_strict_decimal(
    value: Any,
    field: str = "value",
    *,
    error_cls: type[Exception] = ValueError,
) -> Decimal | None:
    """Convert an optional boundary field to finite Decimal when present."""
    if value is None or value == "":
        return None
    return strict_decimal(value, field, error_cls=error_cls)


def exact_decimal_sum(*values: Decimal) -> Decimal:
    """Sum Decimal operands exactly without default-precision rounding or float coercion."""
    with localcontext() as ctx:
        ctx.prec = max(28, sum(len(value.as_tuple().digits) for value in values) + 4)
        return sum(values, Decimal("0"))


def parse_utc_iso(
    value: Any,
    field: str = "timestamp",
    *,
    error_cls: type[Exception] = ValueError,
) -> datetime:
    """Parse an ISO-8601 timestamp requiring timezone awareness and returning UTC."""
    if not isinstance(value, str) or not value.strip():
        raise error_cls(f"Missing or invalid {field}: {value!r}")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise error_cls(f"Invalid ISO timestamp {field}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise error_cls(f"Timezone-aware timestamp required for {field}: {value!r}")
    return parsed.astimezone(timezone.utc)


def validate_relative_api_path(
    path: Any,
    *,
    error_cls: type[Exception] = ValueError,
) -> str:
    """Require a relative API path without traversal or scheme-relative slashes."""
    if not isinstance(path, str) or not path.startswith("/") or "//" in path or ".." in path:
        raise error_cls("Relative API path required")
    return path


def required_text(
    value: Any,
    field: str,
    *,
    allowed: set[str] | frozenset[str] | None = None,
    error_cls: type[Exception] = ValueError,
) -> str:
    """Require a non-empty string field, optionally validated against allowed lowercase values."""
    if not isinstance(value, str):
        raise error_cls(f"Missing or non-string field {field!r}: {value!r}")
    text = value.strip()
    if not text:
        raise error_cls(f"Empty string field {field!r}")
    if allowed is not None:
        normalized = text.lower()
        if normalized not in allowed:
            raise error_cls(f"Unsupported {field!r} value {value!r}; expected one of {sorted(allowed)}")
        return normalized
    return text
