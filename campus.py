"""Campus definitions shared by catalog, cart, and enrollment requests."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Campus:
    """One campus value accepted by the school course-selection client."""

    code: str
    name: str


# These values mirror the XQ dictionary exposed by the school selection system.
CAMPUS_OPTIONS = (
    Campus("01", "粤海校区"),
    Campus("02", "丽湖校区"),
    Campus("03", "深大附属医院"),
    Campus("04", "技术大学"),
    Campus("05", "香港校区"),
    Campus("06", "深理光明校区"),
)
CAMPUS_BY_CODE = {campus.code: campus for campus in CAMPUS_OPTIONS}
DEFAULT_CAMPUS_CODE = CAMPUS_OPTIONS[0].code
DEFAULT_CAMPUS_NAME = CAMPUS_OPTIONS[0].name


def get_campus(code: object) -> Campus | None:
    """Return a supported campus, or ``None`` for an unknown code."""
    return CAMPUS_BY_CODE.get(str(code or "").strip())


def normalize_campus_code(code: object, *, fallback: str | None = None) -> str:
    """Validate a campus code and optionally use one known fallback."""
    campus = get_campus(code)
    if campus is not None:
        return campus.code
    if fallback is not None:
        fallback_campus = get_campus(fallback)
        if fallback_campus is not None:
            return fallback_campus.code
    raise ValueError("不支持的校区代码")


def campus_name(code: object, *, fallback: str = "") -> str:
    """Return the stable display name for a campus code."""
    campus = get_campus(code)
    return campus.name if campus is not None else fallback


def campus_options_payload() -> list[dict[str, str]]:
    """Return a JSON-ready copy of every supported campus."""
    return [asdict(campus) for campus in CAMPUS_OPTIONS]


__all__ = [
    "CAMPUS_OPTIONS",
    "DEFAULT_CAMPUS_CODE",
    "DEFAULT_CAMPUS_NAME",
    "Campus",
    "campus_name",
    "campus_options_payload",
    "get_campus",
    "normalize_campus_code",
]
