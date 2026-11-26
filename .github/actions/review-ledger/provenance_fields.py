"""Project exact prompt/model provenance carried by the primary audit."""

from __future__ import annotations

from typing import Any


UNKNOWN_REASON = "live ledger producer has no exact value for this field"


def project_provenance_fields(primary: dict[str, Any], unknown: dict[str, str]) -> dict[str, str | None]:
    """Return supported primary provenance values and record only missing values as unknown."""
    projected: dict[str, str | None] = {}
    for field in ("prompt_digest", "model_used"):
        value = primary.get(field)
        if value is None:
            unknown[field] = UNKNOWN_REASON
            projected[field] = None
        elif not isinstance(value, str) or not value:
            raise ValueError(f"primary audit {field} must be a non-empty string")
        else:
            projected[field] = value
    return projected
