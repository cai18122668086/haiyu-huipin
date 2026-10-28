from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, RefResolver

from .state import HarnessError, load_json


SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    schema_path = SCHEMA_DIR / schema_name
    if not schema_path.is_file():
        raise HarnessError(f"JSON Schema not found: {schema_path}")
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    resolver = RefResolver(
        base_uri=schema_path.resolve().as_uri(),
        referrer=schema,
    )
    return Draft202012Validator(schema, resolver=resolver, format_checker=FormatChecker())


def validate_schema(value: dict, schema_name: str, label: str) -> None:
    errors = sorted(_validator(schema_name).iter_errors(value), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    rendered = []
    for error in errors[:20]:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        rendered.append(f"{location}: {error.message}")
    suffix = f"\n- ... and {len(errors) - 20} more" if len(errors) > 20 else ""
    raise HarnessError(
        f"{label} failed {schema_name} with {len(errors)} issue(s):\n- "
        + "\n- ".join(rendered)
        + suffix
    )
