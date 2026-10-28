"""Source-bound facts and adaptation contracts; no visual verdicts are invented here."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def user_statements(value: str) -> list[dict[str, str]]:
    # Preserve complete lines, including punctuation and numbers. This is source
    # indexing, not a parser which decides which marketing assertions are true.
    return [
        {"id": f"U{i + 1:03}", "text": line}
        for i, line in enumerate(
            row.strip() for row in value.splitlines() if row.strip()
        )
    ]


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _text(minimum: int = 1, maximum: int = 800) -> dict[str, Any]:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def _array(item: dict[str, Any], maximum: int = 240) -> dict[str, Any]:
    return {"type": "array", "items": item, "maxItems": maximum}


def fact_resolution_schema() -> dict[str, Any]:
    return _object(
        {
            "facts": _array(
                _object(
                    {
                        "id": {**_text(), "pattern": r"^F[0-9]{3,}$"},
                        "statement": _text(),
                        "disposition": {"enum": ["approved", "excluded", "uncertain"]},
                        "kind": {
                            "enum": [
                                "physical_identity",
                                "claim",
                                "instruction",
                                "other",
                            ]
                        },
                        "user_statement_ids": _array(_text(), 100),
                        "product_marks": _array(
                            _object(
                                {
                                    "product_index": {"type": "integer", "minimum": 0},
                                    "mark_index": {"type": "integer", "minimum": 0},
                                }
                            ),
                            100,
                        ),
                        "reason": _text(8),
                    }
                )
            )
        }
    )


def adaptation_schema() -> dict[str, Any]:
    return _array(
        _object(
            {
                "region_id": _text(1, 40),
                "mode": {
                    "enum": [
                        "supported_copy",
                        "truthful_substitution",
                        "non_factual_copy",
                        "omit_unsupported",
                    ]
                },
                "fact_ids": _array(_text(1, 40), 40),
                "reason": _text(8, 400),
                "preserved_mechanism": _text(8, 400),
            }
        ),
        48,
    )


def resolve_facts(
    value: dict[str, Any],
    statements: list[dict[str, str]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate provenance/coverage, retaining the model's actual dispositions."""
    users = {row["id"] for row in statements}
    products = {row["product_index"]: row for row in evidence}
    seen: set[str] = set()
    covered: set[str] = set()
    for row in value["facts"]:
        if row["id"] in seen:
            raise ValueError("Fact IDs must be unique")
        seen.add(row["id"])
        refs = row["user_statement_ids"]
        if len(refs) != len(set(refs)) or not set(refs) <= users:
            raise ValueError("Fact provenance must reference supplied user statements")
        covered.update(refs)
        for mark in row["product_marks"]:
            product = products.get(mark["product_index"])
            if product is None or not 0 <= mark["mark_index"] < len(
                product["readable_marks"]
            ):
                raise ValueError(
                    "Fact provenance must reference an observed product mark"
                )
        if not refs and not row["product_marks"]:
            raise ValueError("Every fact needs user or original-product evidence")
    if covered != users:
        raise ValueError(
            "Resolve every user statement, including excluded/uncertain ones"
        )
    ledger = {
        "schema": "replica-resolved-facts-v1",
        "input_sha256": canonical_hash(
            {"user_statements": statements, "products": evidence}
        ),
        "facts": deepcopy(value["facts"]),
    }
    return {**ledger, "version": canonical_hash(ledger)}


def validate_adaptations(
    proposal: dict[str, Any],
    layout: dict[str, Any],
    ledger: dict[str, Any],
) -> None:
    expected = {row["id"] for row in layout["text_regions"]}
    rows = proposal["fact_adaptations"]
    if len(rows) != len(expected) or {row["region_id"] for row in rows} != expected:
        raise ValueError(
            "Bind a fact/adaptation decision to every existing text region"
        )
    allowed = {row["id"] for row in ledger["facts"] if row["disposition"] == "approved"}
    bindings = {row["region_id"]: row for row in proposal["text_bindings"]}
    for row in rows:
        if not set(row["fact_ids"]) <= allowed:
            raise ValueError("New copy can reference approved fact IDs only")
        if (
            row["mode"] in {"supported_copy", "truthful_substitution"}
            and not row["fact_ids"]
        ):
            raise ValueError(
                "Factual copy/substitutions require approved supporting facts"
            )
        binding = bindings.get(row["region_id"])
        if binding is None:
            raise ValueError("Adapted text must have a corresponding text-slot binding")
        omitted = binding["action"] == "omit_unsupported_copy"
        if omitted != (row["mode"] == "omit_unsupported"):
            raise ValueError("Fact adaptation and text-slot execution must agree")
