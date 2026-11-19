"""Source-grounded selection and fail-closed acceptance for real image edits.

This layer never retries generation. Unknown outcomes retain the original
reconciliation path. A rejected returned candidate is retained as a private,
non-deliverable asset, with image and review costs both accounted for.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import unicodedata
import weakref
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from PIL import Image
from sqlalchemy import select

from .models import (
    AssetRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    ReplicaDocumentRecord,
)
from .provider import ProviderArtifact, ProviderOutcome
from .services import canonical_hash

VERSION = "image-fidelity-v1"
PREPARE = "image_fidelity_prepare"
REVIEW = "image_fidelity_review"
READBACK = "image_fidelity_readback"
MATTE = "image_fidelity_matte"
BACKGROUND = "image_fidelity_background"
CHECKS = (
    "subject_selection",
    "physical_artwork_text",
    "protected_attributes",
    "requested_change",
    "reference_scope",
    "canvas",
)
PHYSICAL_REVIEW_VERSION = "physical-contact-review-v1"
PHYSICAL_REVIEW_POLICY = (
    "PHYSICAL CONTACT REVIEW V1. Verify the actual final full canvas, not "
    "only product crops. Preserving original product pixels does not establish "
    "a correct physical scene. When the approved scene requires a rack, rod, "
    "hook, hand, bag attachment or supporting surface, inspect each required "
    "contact point. A visible gap, floating clasp, detached accessory or "
    "misaligned support MUST fail requested_change with concrete visible "
    "evidence. Do not excuse it because product identity/printing is correct. "
    "An explicitly flat graphic/cutout layout requires no invented support. "
    "Never add an unrequested prop to make an isolated graphic look physical."
)
GARMENT_REVIEW_VERSION = "garment-count-review-v1"
GARMENT_REVIEW_POLICY = (
    "GARMENT COUNT REVIEW V1. When editing or wearing a referenced garment, "
    "explicitly count every visible button/fastener on the original garment "
    "and on the candidate. A similar color, neckline or general style is not "
    "proof of identical construction. Any added or missing button/fastener "
    "MUST fail protected_attributes with the observed source and candidate "
    "counts. Respect an exact count in the original user instruction. Also "
    "inspect sleeve length, neckline, hem, closure and fabric. Never import "
    "clothes from a pose/model reference. If a required comparison is obscured "
    "or cannot be established, report uncertainty rather than manufacture PASS."
)
logger = logging.getLogger(__name__)
AUTHORITY_ROLES = frozenset(
    {
        "product_reference",
        "product_original",
        "model_source",
        "white_background_product",
        "product_detail",
    }
)

POLICY = (
    "PRODUCT PRESERVATION — MANDATORY. The user's explicit selected subset, count "
    "and requested changes override generic 'keep all products' recipes. Every "
    "selected object must come from product-authority sources, never from a "
    "background/style/pose reference. Physical printing is part of the product: "
    "preserve the actual characters, language, spelling, logos, illustrations, "
    "linework and placement, including small copyright marks. Never translate "
    "physical print or replace copyright with English character/product names. "
    "Never replace tiny Japanese or other lettering with "
    "a familiar brand/character name or pseudo-glyphs. A ban on copying reference "
    "marketing text DOES NOT authorize erasing or rewriting product printing. "
    "For change_model, change the person only: retain Image 1's sleeve length, "
    "neckline, garment cut, buttons, fabric and colors; never import Image 2's "
    "clothes. For pose transfer, keep the original person, outfit, background "
    "and full canvas; the pose image supplies pose only. Return a full-bleed "
    "single image without contact-sheet panels, borders or transparent padding."
)


def operation_policy(context: dict[str, Any]) -> str:
    if context["settings"].get("task_type") == "change_model":
        return POLICY + (
            "\nTHIS REQUEST IS CHANGE_MODEL: actually replace the human face, "
            "hair and adult identity with a visibly different adult model. "
            "The original person's identity is NOT a protected product attribute. "
            "Here 'product identity' means the original GARMENTS ONLY. "
            "A near-identical face or unchanged person is a failed edit. "
            "Keep the original short sleeves, neckline, trousers, shoes, "
            "requested standing pose and room. The reference's sleeveless top "
            "and shorts are forbidden clothing substitutes."
        )
    return POLICY


def technical_contract(
    context: dict[str, Any], request: Any, artifact: Any
) -> dict[str, Any]:
    with Image.open(BytesIO(artifact.payload)) as image:
        image.load()
        width, height = image.size
        alpha_min = (
            image.getchannel("A").getextrema()[0] if "A" in image.getbands() else 255
        )
    valid = (width, height) == (artifact.width, artifact.height)
    valid = valid and hashlib.sha256(artifact.payload).hexdigest() == artifact.sha256
    target_width, target_height = (int(value) for value in request.size.split("x"))
    ratio_valid = abs((width / height) / (target_width / target_height) - 1) <= 0.01
    policy = (
        "Native original must match requested aspect within 1%; no artificial padding."
    )
    valid = valid and ratio_valid
    if context["job_type"] == "amazon_visual_suite":
        from .amazon_suite_fast import native_canvas_valid
        from .amazon_visual_suite_provider import AMAZON_ITEM_SPECS

        spec = next(
            spec for spec in AMAZON_ITEM_SPECS if spec.item_key == context["item_key"]
        )
        valid = valid and native_canvas_valid(width, height, spec)
        policy = (
            "EXISTING Amazon native-original validator: aspect within 1%; "
            "desktop banners width/height at least requested. Other images: "
            "short edge >=1024, or short edge >=768 and area >=1500000. "
            "Measure the unmodified native background before any source-pixel "
            "composition, and retain that original privately. The final canvas "
            "must keep those dimensions; it is not an exact-pixel resampled "
            "export. No cropping or size relaxation. A source-pixel composite "
            "must carry its separate rendering provenance."
        )
    elif context["job_type"] == "domestic_main_image":
        valid = valid and width == height and min(width, height) >= 1024
        policy = (
            "Existing native main-image validator: exact square, short edge >=1024."
        )
    elif context["job_type"] == "model_pose_transfer":
        valid = valid and alpha_min == 255
        policy += " Opaque original background must cover the full pose canvas."
    return {
        "policy": policy,
        "measured_native_size": [width, height],
        "requested_target": [target_width, target_height],
        "server_technical_checks_passed": valid,
    }


def selection_constraint(instruction: str) -> dict[str, Any] | None:
    match = re.search(
        r"(下面|下方|下排|底部|上面|上方|上排|顶部|左边|左侧|右边|右侧)的?\s*([1-6一二两三四五六])\s*[个件款只]",
        instruction,
    )
    if not match:
        english = re.search(
            r"\b(bottom|lower|top|upper|leftmost|left|rightmost|right)[\s-]+"
            r"(one|two|three|four|five|six|[1-6])\b",
            instruction,
            re.I,
        )
        if not english:
            return None
        direction, count = english.groups()
        return {
            "direction": {
                "lower": "bottom",
                "upper": "top",
                "leftmost": "left",
                "rightmost": "right",
            }.get(direction.lower(), direction.lower()),
            "count": int(count)
            if count.isdigit()
            else {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}[
                count.lower()
            ],
        }
    direction, count = match.groups()
    return {
        "direction": next(
            key
            for key, names in {
                "bottom": ("下面", "下方", "下排", "底部"),
                "top": ("上面", "上方", "上排", "顶部"),
                "left": ("左边", "左侧"),
                "right": ("右边", "右侧"),
            }.items()
            if direction in names
        ),
        "count": int(count)
        if count.isdigit()
        else {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}[count],
    }


def response_schema(
    profile: str,
    source_ids: tuple[str, ...] = (),
    selection: dict[str, Any] | None = None,
    *,
    print_geometry: bool = False,
) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    properties: dict[str, Any]
    if profile == BACKGROUND:
        from .source_typography import layout_schema

        properties = {
            "background_description": {**text, "maxLength": 2400},
            "marketing_copy": {"type": "array", "maxItems": 32, "items": text},
            "text_layout": layout_schema(1),
        }
    elif profile == MATTE:
        properties = {
            **{
                name: {"type": "boolean"}
                for name in (
                    "complete_objects",
                    "no_context_leak",
                    "no_clipped_print",
                    "edges_clean",
                )
            },
            "unresolved": {"type": "array", "items": text},
            "evidence": text,
            "repairs": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["layer_index", "action", "box"],
                    "properties": {
                        "layer_index": {"type": "integer", "minimum": 0, "maximum": 5},
                        "action": {"enum": ["restore_foreground", "remove_background"]},
                        "box": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                    },
                },
            },
            "edge_feather_px": {"type": "integer", "minimum": 0, "maximum": 2},
        }
    elif profile == READBACK:
        properties = {
            "text_runs": {
                "type": "array",
                "items": text,
                "description": (
                    "Literal physical text from candidate only. Join vertical "
                    "glyphs in reading order. Never correct spelling or guess names."
                ),
            },
        }
    elif profile == PREPARE:
        from .source_print_evidence import REGIONS_SCHEMA

        properties = {
            "selected_regions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["asset_id", "box", "identity", "visible_print"],
                    "properties": {
                        "asset_id": {
                            **text,
                            **({"enum": list(source_ids)} if source_ids else {}),
                        },
                        "box": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "identity": text,
                        "print_regions": deepcopy(REGIONS_SCHEMA),
                        "visible_print": {
                            "type": "array",
                            "items": {
                                "anyOf": [
                                    {"const": "UNREADABLE"},
                                    {
                                        **text,
                                        "not": {
                                            "pattern": (
                                                "[Uu][Nn][Rr][Ee][Aa][Dd]"
                                                "[Aa][Bb][Ll][Ee]"
                                            )
                                        },
                                    },
                                ]
                            },
                        },
                    },
                },
            },
            "protected_attributes": {"type": "array", "minItems": 1, "items": text},
            "allowed_changes": {"type": "array", "minItems": 1, "items": text},
            "selection_summary": text,
        }
        if not print_geometry:
            del properties["selected_regions"]["items"]["properties"]["print_regions"]
        properties["source_inventory"] = {
            **properties["selected_regions"],
            "maxItems": 24,
            "description": (
                "ALL complete products in the original product-authority image, "
                "including excluded rows. Never inventory background products."
            ),
        }
        if selection:
            # Select inventory IDs instead of asking the model to duplicate
            # whole records (and accidentally change their boxes or printing).
            inventory = deepcopy(properties["source_inventory"])
            inventory["items"]["properties"]["object_id"] = {
                "type": "string",
                "pattern": "^[A-Za-z0-9_-]{1,32}$",
            }
            inventory["items"]["required"].append("object_id")
            properties["source_inventory"] = inventory
            del properties["selected_regions"]
            properties["selected_object_ids"] = {
                "type": "array",
                "uniqueItems": True,
                "minItems": selection["count"],
                "maxItems": selection["count"],
                "items": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,32}$"},
                "description": (
                    "Exact object_id values from source_inventory for the "
                    "user's spatial subset. Do not repeat object records."
                ),
            }
    else:
        assert profile == REVIEW
        properties = {
            "checks": {
                "type": "object",
                "additionalProperties": False,
                "required": list(CHECKS),
                "properties": {
                    key: {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["passed", "evidence"],
                        "properties": {
                            "passed": {"type": "boolean"},
                            "evidence": {"type": "string", "minLength": 4},
                        },
                    }
                    for key in CHECKS
                },
            },
            "unresolved": {"type": "array", "items": text},
            "observable_defects": {"type": "array", "items": text},
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            key
            for key in properties
            if key != "literal_print_ledger"
            and (key != "source_inventory" or selection)
        ],
        "properties": properties,
    }


def validate_preparation(
    payload: object,
    ids: tuple[str, ...],
    selection: dict[str, Any] | None = None,
    *,
    allow_stored: bool = False,
    print_geometry: bool = False,
) -> dict[str, Any]:
    schema = response_schema(
        PREPARE, ids, None if allow_stored else selection,
        print_geometry=print_geometry or allow_stored,
    )
    if allow_stored:
        if selection:
            schema["required"].append("source_inventory")
        schema["properties"]["literal_print_ledger"] = {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 96,
        }
    Draft202012Validator(schema).validate(payload)
    assert isinstance(payload, dict)
    if selection and not allow_stored:
        inventory = payload["source_inventory"]
        by_id = {r["object_id"]: r for r in inventory}
        if len(by_id) != len(inventory):
            raise ValueError("Source inventory object IDs are not unique")
        if any(key not in by_id for key in payload["selected_object_ids"]):
            raise ValueError("Selected product is outside the source inventory")

        def canonical_region(region: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in region.items() if k != "object_id"}

        payload = {
            **{k: v for k, v in payload.items() if k != "selected_object_ids"},
            "selected_regions": [
                canonical_region(by_id[key]) for key in payload["selected_object_ids"]
            ],
            "source_inventory": [canonical_region(r) for r in inventory],
        }
    from .source_print_evidence import PrintGeometryError
    from .source_print_evidence import validate as validate_print_geometry

    selected_count = len(payload["selected_regions"])
    for index, region in enumerate(
        payload["selected_regions"] + payload.get("source_inventory", [])
    ):
        x0, y0, x1, y1 = region["box"]
        if not (x1 > x0 and y1 > y0 and (x1 - x0) * (y1 - y0) >= 0.0001):
            raise ValueError("Source selection has invalid geometry")
        try:
            validate_print_geometry(region)
        except PrintGeometryError as exc:
            raise ValidationError(
                exc.rule, validator="source_geometry", validator_value=exc.rule,
                path=[
                    "selected_regions"
                    if index < selected_count else "source_inventory",
                    index if index < selected_count else index - selected_count,
                    "print_regions", exc.index,
                ],
            ) from exc
    if selection:
        inventory = payload["source_inventory"]
        if len(
            {
                canonical_hash({"asset_id": r["asset_id"], "box": r["box"]})
                for r in inventory
            }
        ) != len(inventory):
            raise ValueError("Source inventory contains duplicates")
        axis = 1 if selection["direction"] in {"bottom", "top"} else 0
        ordered = sorted(
            inventory,
            key=lambda r: (r["box"][axis] + r["box"][axis + 2]) / 2,
            reverse=selection["direction"] in {"bottom", "right"},
        )
        chosen = ordered[: selection["count"]]
        if len(chosen) != selection["count"] or {canonical_hash(r) for r in chosen} != {
            canonical_hash(r) for r in payload["selected_regions"]
        }:
            raise ValueError(
                "Selected products violate the user's explicit spatial subset"
            )
    return payload


def validate_review(payload: object) -> tuple[bool, list[str]]:
    Draft202012Validator(response_schema(REVIEW)).validate(payload)
    assert isinstance(payload, dict)
    failed = [name for name in CHECKS if payload["checks"][name]["passed"] is not True]
    if payload["unresolved"] or payload["observable_defects"]:
        failed.append("unresolved_evidence")
    return not failed, failed


def readback_matches(payload: object, preparation: dict[str, Any]) -> bool:
    Draft202012Validator(response_schema(READBACK)).validate(payload)
    assert isinstance(payload, dict)

    def canonical(value: str) -> str:
        return "".join(unicodedata.normalize("NFC", value).split())

    required_runs = preparation.get("literal_print_ledger") or [
        text
        for region in preparation["selected_regions"]
        for text in region["visible_print"]
    ]
    expected = Counter(
        canonical(text) for text in required_runs if text.upper() != "UNREADABLE"
    )
    actual = Counter(canonical(text) for text in payload["text_runs"])
    return all(actual[text] >= count for text, count in expected.items())


def contract_diagnostic(error: ValidationError) -> dict[str, Any]:
    missing = []
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = sorted(set(error.validator_value) - set(error.instance))
    diagnostic = {
        "validator": error.validator,
        "path": "/" + "/".join(str(part) for part in error.absolute_path),
        "missing_fields": missing,
    }
    if error.validator == "source_geometry":
        diagnostic["constraint"] = error.validator_value
    return diagnostic


class FidelityGateway:
    def __init__(self, wrapped: Any, sessions: Any, storage: Any) -> None:
        self.wrapped, self.sessions, self.storage = wrapped, sessions, storage
        self._prepared: dict[str, dict[str, Any] | None] = {}
        self._mattes: dict[
            str, tuple[tuple[Any, ...], list[dict[str, Any]]] | None
        ] = {}
        self._parent_key: str | None = None
        self._native_pair = False
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)

    async def edit_native(self, request: Any, parent: Any, *, token: str) -> Any:
        """Bind an already-authorized signed child to its exact durable parent.

        Called only after the native Harness has fenced this invocation. The
        logical request, approval hash and supplier key remain unchanged.
        """
        if not getattr(self.wrapped, "image_fidelity_source_pixels_enabled", False):
            return await self.wrapped.edit(request)
        return await self._native_bound(request, parent, token).edit(request)

    def _native_bound(self, request: Any, parent: Any, token: str) -> FidelityGateway:
        from .domestic_detail.generation import wire_idempotency_key

        if self._context(request)["job_type"] != "detail_pack":
            raise ValueError("Signed native image scope is not a detail pair")
        if (
            not token
            or any(
                getattr(request, field) != getattr(parent, field)
                for field in ("workspace_id", "job_id", "job_item_id", "attempt_id")
            )
            or request.idempotency_key
            != wire_idempotency_key(parent.idempotency_key, token)
        ):
            raise ValueError("Signed native image parent binding changed")
        bound = FidelityGateway(self.wrapped, self.sessions, self.storage)
        bound._parent_key = parent.idempotency_key
        bound._native_pair = True
        bound._prepared, bound._locks = self._prepared, self._locks
        bound._mattes = self._mattes
        return bound

    async def native_source_evidence(
        self, request: Any, parent: Any, *, token: str, candidate: Any
    ) -> tuple[Any, ...]:
        """Read the same signed receipt, never replan or dispatch an image."""
        if not getattr(self.wrapped, "image_fidelity_source_pixels_enabled", False):
            return ()
        bound = self._native_bound(request, parent, token)
        return await bound.paired_source_evidence(request, candidate)

    def _context(self, request: Any) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(JobRecord, request.job_id)
            if job is None or job.workspace_id != request.workspace_id:
                raise ValueError("Image fidelity scope invalid")
            item = session.get(JobItemRecord, request.job_item_id)
            if (
                item is None
                or item.job_id != job.id
                or item.workspace_id != job.workspace_id
            ):
                raise ValueError("Image fidelity item scope invalid")
            settings = dict(job.configuration.get("settings", {}))
            if not settings.get("instruction") and job.type in {
                "detail_pack",
                "amazon_visual_suite",
            }:
                # These forms retain user-entered constraints as fact buckets.
                # They are source authority; generated module text is not.
                facts = settings.get("verified_facts") or []
                settings["instruction"] = "\n".join(
                    [str(settings.get("product_name") or ""), *[str(v) for v in facts]]
                )
            replica_id = job.configuration.get("replica_session_id")
            if replica_id:
                document = session.get(ReplicaDocumentRecord, replica_id)
                if (
                    document is None
                    or document.workspace_id != request.workspace_id
                    or (
                        job.id not in document.payload.get("job_ids", [])
                        or settings.get("replica_plan_hash")
                        != document.payload.get("plan_hash")
                    )
                ):
                    raise ValueError("Confirmed replica source context changed")
                if not settings.get("instruction") and isinstance(
                    document.payload.get("facts"), str
                ):
                    # The native session retains the actual user input. Its
                    # generated job historically left settings.instruction blank.
                    settings["instruction"] = document.payload["facts"]
            return {
                "job_type": job.type,
                "item_key": item.item_key,
                "settings": settings,
            }

    def _frozen(
        self,
        request: Any,
        value: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Freeze policy/crops before dispatch for identical reconciliation."""
        binding = canonical_hash(
            {
                "workspace": request.workspace_id,
                "job": request.job_id,
                "item": request.job_item_id,
                "prompt": request.prompt,
                "size": request.size,
                "quality": request.quality,
                "format": request.output_format,
                "fidelity": request.input_fidelity,
                "sources": [
                    (s.asset_id, s.sha256, s.source_role) for s in request.sources
                ],
            }
        )
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        with self.sessions() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            if (
                job is None
                or job.workspace_id != request.workspace_id
                or job.status != "processing"
            ):
                raise ValueError("Image fidelity job is no longer active")
            item = session.scalar(
                select(JobItemRecord)
                .where(
                    JobItemRecord.id == request.job_item_id,
                    JobItemRecord.job_id == request.job_id,
                    JobItemRecord.workspace_id == request.workspace_id,
                )
                .with_for_update()
            )
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if (
                item is None
                or attempt is None
                or attempt.status != "running"
                or (
                    attempt.workspace_id,
                    attempt.job_id,
                    attempt.job_item_id,
                    attempt.provider_idempotency_key,
                )
                != (
                    request.workspace_id,
                    request.job_id,
                    request.job_item_id,
                    self._parent_key or request.idempotency_key,
                )
            ):
                raise ValueError("Image fidelity attempt scope invalid")
            rows = dict(item.configuration.get("_image_fidelity_frozen", {}))
            prior = rows.get(key)
            if prior is not None:
                checksum = canonical_hash(
                    {k: v for k, v in prior.items() if k != "receipt_sha256"}
                )
                if (
                    prior.get("binding") != binding
                    or prior.get("receipt_sha256") != checksum
                ):
                    raise ValueError("Frozen image fidelity inputs changed")
                return prior
            if value is None:
                return None
            if len(rows) >= 64:
                raise ValueError("Image fidelity receipt limit reached")
            rows[key] = {**value, "binding": binding, "version": VERSION}
            rows[key]["receipt_sha256"] = canonical_hash(rows[key])
            item.configuration = {
                **item.configuration,
                "_image_fidelity_frozen": rows,
            }
            session.commit()
            return rows[key]

    async def _plan(
        self,
        request: Any,
        profile: str,
        sources: tuple[Any, ...],
        context: dict[str, Any],
        suffix: str = "",
    ) -> Any:
        from .xinghe_agent import PlannerRequest

        return await self.wrapped.plan(
            PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key=request.job_item_id,
                sources=sources,
                page_count=1,
                language="zh-CN",
                product_name="Source-grounded image acceptance",
                product_information=(json.dumps(context, ensure_ascii=False),),
                platform_code="IMAGE_FIDELITY",
                size=request.size,
                idempotency_key=(
                    f"{request.idempotency_key}:{profile}{suffix}"
                    + (f":{PHYSICAL_REVIEW_VERSION}" if profile == REVIEW else "")
                    + (f":{GARMENT_REVIEW_VERSION}" if profile == REVIEW else "")
                ),
                planning_profile=profile,
                system_prompt=(
                    "You are an independent literal OCR reader. Read only the "
                    "attached candidate's actual printed glyphs. You have no "
                    "reference transcription. Never infer familiar product or "
                    "character names, correct spelling, or repair malformed "
                    "letters. Return separate text_runs for each physical "
                    "printed column/line, joining vertical glyphs in order. "
                    "Read ALL printed columns, secondary series marks and "
                    "copyright lines on EVERY attached product. Identical "
                    "printing on distinct products must repeat in text_runs; "
                    "never deduplicate repeated physical text or summarize "
                    "only the character names. "
                    "Use UNREADABLE when strokes cannot be read. JSON only."
                    if profile == READBACK
                    else "Inspect only source-product extraction, BEFORE final "
                    "background generation. The original product photos are "
                    "authority. Checkerboard previews show source RGB with a "
                    "predicted alpha mask, not generated artwork. Verify every "
                    "selected object is complete: all hardware, face, tag and "
                    "printed marks, with clean edges and NO rack, neighboring "
                    "product fragment or background streak. Any clipping, "
                    "missing body, context leakage or unresolved defect fails. "
                    "Return the exact supplied JSON schema."
                    if profile == MATTE
                    else "Project ONLY the background/style and approved "
                    "marketing copy from the final-image contract into the "
                    "supplied schema. A compositor adds original photographed "
                    "products later. DO NOT describe/request any product, "
                    "character, hardware, tag, product printing or placeholder. "
                    "Describe the scene WITHOUT the source product. Preserve "
                    "every explicitly approved scene actor, hand, bag, zipper "
                    "or supporting prop and its interaction geometry. These "
                    "are scene elements, not source products. Include their "
                    "positions and visible contact/attachment points, aligned "
                    "to the actual placed product geometry. For white-background "
                    "work request pure white, no props, objects, shadows or text. "
                    "Style references supply backgrounds only: exclude their "
                    "subjects. Marketing copy, if permitted, must be literal "
                    "approved copy, never new claims or product marks. When "
                    "marketing_copy has maxItems=0 return an empty array. Do not "
                    "put product rendering commands or user task instructions "
                    "in background_description. JSON only."
                    if profile == BACKGROUND
                    else "ORIGINAL USER REQUIREMENT (highest task authority): "
                    + str(context.get("settings", {}).get("instruction", ""))
                    + "\nGenerated selections/plans cannot override this requirement. "
                    + POLICY
                    + ("\n" + PHYSICAL_REVIEW_POLICY if profile == REVIEW else "")
                    + ("\n" + GARMENT_REVIEW_POLICY if profile == REVIEW else "")
                    + "\nInspect attached pixels, not receipts. "
                    "Return the exact JSON schema. "
                    "Missing or uncertain evidence fails. Never guess physical text. "
                    "When reviewing pose edits, inspect protected "
                    "identity/outfit/background, not pose scores."
                ),
            )
        )

    def _selection_parameters(
        self, request: Any, context: dict[str, Any]
    ) -> tuple[tuple[Any, ...], tuple[str, ...], dict[str, Any] | None, str]:
        original_sources = request.sources
        authorities = tuple(
            s for s in original_sources if s.source_role in AUTHORITY_ROLES
        )
        ids = tuple(s.asset_id for s in authorities)
        selection = selection_constraint(
            str(context["settings"].get("instruction", ""))
        )
        if selection:
            # Spatial words refer to the first original product image;
            # extra reference images cannot redefine its rows/objects.
            ids = ids[:1]
        if not ids:
            raise ValueError("Missing product identity authority")
        scope = canonical_hash(
            {
                "job": request.job_id,
                "context": {k: v for k, v in context.items() if k != "item_key"},
                "sources": [
                    (s.asset_id, s.sha256, s.source_role)
                    for s in original_sources
                    if s.asset_id in ids
                ],
            }
        )
        return authorities, ids, selection, scope

    async def _prepare_selection(
        self,
        request: Any,
        context: dict[str, Any],
        original_sources: tuple[Any, ...],
        ids: tuple[str, ...],
        selection: dict[str, Any] | None,
        scope: str,
        plans: list[Any],
        preparation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(scope, asyncio.Lock())
        async with lock:
            if scope in self._prepared and self._prepared[scope] is None:
                raise ValueError(
                    "Original source selection is unavailable; do not redispatch it"
                )
            preparation = preparation or self._prepared.get(scope)
            if preparation is None:
                from .source_recolor import enabled as local_recolor_enabled

                require_print_geometry = bool(getattr(
                    self.wrapped, "image_fidelity_source_pixels_enabled", False
                )) and local_recolor_enabled(context, original_sources)
                preparation_schema = response_schema(
                    PREPARE, ids, selection, print_geometry=require_print_geometry
                )
                if require_print_geometry:
                    for name in ("selected_regions", "source_inventory"):
                        if name not in preparation_schema["properties"]:
                            continue
                        record = preparation_schema["properties"][name]["items"]
                        if "print_regions" not in record["required"]:
                            record["required"].append("print_regions")
                        record["allOf"] = [{
                            "if": {"properties": {"visible_print": {"minItems": 1}}},
                            "then": {"properties": {"print_regions": {"minItems": 1}}},
                        }]
                preparation_feedback = None
                for preparation_attempt in range(2):
                    plan = await self._plan(
                        request,
                        PREPARE,
                        tuple(s for s in original_sources if s.asset_id in ids),
                        {
                            **context,
                            "source_roles": [
                                (s.asset_id, s.source_role) for s in original_sources
                            ],
                            "schema": preparation_schema,
                            "literal_print_geometry_required": require_print_geometry,
                            "contract_feedback": preparation_feedback,
                            "original_user_instruction": context["settings"].get(
                                "instruction", ""
                            ),
                            "spatial_selection_constraint": selection,
                            "instruction": "Select exactly the objects requested "
                            "by the user from product sources. Give tight "
                            "complete-object boxes in normalized source-image "
                            "coordinates. Read the original physical printing, "
                            "not the style image's text. If unreadable, state "
                            "UNREADABLE alone, never a prefixed/suffixed annotation, "
                            "rather than inventing a transcription. "
                            "identity describes observed color, shape and "
                            "position, never a guessed character or animal "
                            "name. visible_print contains actual photographed "
                            "glyphs only, never a name inferred from the face. "
                            "visible_print uses separate entries for each physical "
                            "printed column or copyright line. Join vertical glyphs "
                            "in their reading order; never combine a name, series "
                            "column and copyright into one invented text run. "
                            "When literal_print_geometry_required is true, map "
                            "each COMPLETE printed surface, e.g. the entire "
                            "colored tag with ALL columns and copyright, into "
                            "one print_regions quad inside the product's source "
                            "box. Do not divide adjacent columns into overlapping "
                            "crops. Every edge glyph must remain visible. "
                            "Include every product's repeated printed surface. "
                            "These regions supply "
                            "pixels only to an independent OCR reader; no answers. "
                            "When spatial_selection_constraint is present, "
                            "inventory ALL objects in the first product photo "
                            "as source_inventory, then select the requested "
                            "bottom/top/left/right count by "
                            "their actual positions. Bottom/下面 means LOWER rows, "
                            "never the upper row or background subjects.",
                        },
                        suffix=f":source-{preparation_attempt}",
                    )
                    plans.append(plan)
                    if plan.error_code:
                        if (
                            not preparation_attempt
                            and plan.provider_request_id
                            and (plan.error_code, plan.http_status)
                            in {
                                ("PLANNER_RATE_LIMITED", 429),
                                ("PLANNER_REQUEST_FAILED", 502),
                                ("PLANNER_REQUEST_FAILED", 503),
                            }
                        ):
                            logger.warning(
                                "source_selection_planner_retry job_id=%s "
                                "provider_status=%s",
                                request.job_id,
                                plan.http_status,
                            )
                            await asyncio.sleep(1)
                            continue
                        if plan.error_code == "PLANNER_OUTPUT_TRUNCATED":
                            if preparation_attempt:
                                self._prepared[scope] = None
                                raise ValueError(
                                    "Preparation response remained incomplete"
                                )
                            preparation_feedback = {
                                "diagnostic": {
                                    "validator": "complete_response", "path": "/",
                                    "missing_fields": [],
                                    "reason": "known_output_truncation",
                                },
                                "instruction": (
                                    "The acknowledged response reached its output "
                                    "limit without a complete JSON object. Return "
                                    "concise but COMPLETE JSON against the entire "
                                    "unchanged schema and ORIGINAL source pixels. "
                                    "Do not omit inventory, printed surfaces, "
                                    "selection constraints or protected attributes."
                                ),
                            }
                            logger.warning(
                                "source_selection_contract_retry job_id=%s "
                                "diagnostic=%s",
                                request.job_id,
                                preparation_feedback["diagnostic"],
                            )
                            continue
                        self._prepared[scope] = None
                        raise ValueError("Preparation unavailable")
                    try:
                        Draft202012Validator(preparation_schema).validate(plan.payload)
                        preparation = validate_preparation(
                            plan.payload, ids, selection,
                            print_geometry=require_print_geometry,
                        )
                        break
                    except ValidationError as exc:
                        if preparation_attempt:
                            self._prepared[scope] = None
                            raise
                        preparation_feedback = {
                            "diagnostic": contract_diagnostic(exc),
                            "previous_payload": plan.payload,
                            "instruction": (
                                "Correct exact invalid fields against original "
                                "pixels and the full schema. visible_print is "
                                "literal columns or exactly UNREADABLE, never "
                                "descriptive annotations. Preserve source IDs "
                                "and selection constraints. Every original "
                                "printed surface must remain complete: correct "
                                "an overly tight parent product box against the "
                                "source rather than clipping any printed glyph "
                                "to satisfy its bounds. "
                                "validator remains mandatory."
                            ),
                        }
                        logger.warning(
                            "source_selection_contract_retry job_id=%s diagnostic=%s",
                            request.job_id,
                            preparation_feedback["diagnostic"],
                        )
                assert preparation is not None
                if len(self._prepared) >= 128:
                    self._prepared.clear()
                self._prepared[scope] = preparation
        assert isinstance(preparation, dict)
        return preparation

    async def _approved_source_layers(
        self,
        request: Any,
        original_sources: tuple[Any, ...],
        authorities: tuple[Any, ...],
        preparation: dict[str, Any],
        plans: list[Any],
        inherited_layers: tuple[Any, ...] | None = None,
    ) -> tuple[tuple[Any, ...], list[dict[str, Any]]]:
        from . import source_pixels
        from .xinghe_agent import SourceImage

        key = canonical_hash(
            {
                "workspace": request.workspace_id,
                "job": request.job_id,
                "version": source_pixels.VERSION,
                "model": source_pixels.MODEL_SHA256,
                "sources": [(s.asset_id, s.sha256) for s in authorities],
                "preparation": preparation,
                "inherited_layers": (
                    [layer.artifact.sha256 for layer in inherited_layers]
                    if inherited_layers is not None else None
                ),
            }
        )
        lock = self._locks.setdefault("source-matte:" + key, asyncio.Lock())
        async with lock:
            if key in self._mattes:
                prior = self._mattes[key]
                if prior is None:
                    raise ValueError(
                        "Identical source extraction already failed acceptance"
                    )
                return prior[0], deepcopy(prior[1])
            if len(self._mattes) >= 8:
                self._mattes.pop(next(iter(self._mattes)))
            # Do not re-review identical failed pixels on another suite item.
            # Only a completed strict audit replaces this fail-closed marker.
            self._mattes[key] = None
            layers = (
                inherited_layers if inherited_layers is not None
                else await asyncio.to_thread(
                    source_pixels.extract_layers, original_sources, preparation,
                    self.wrapped.image_fidelity_source_model_path,
                )
            )
            mask_audits = []
            for mask_review in range(2):
                previews = []
                for index, layer in enumerate(layers):
                    original = next(
                        s for s in original_sources
                        if s.asset_id == layer.source_asset_id
                    )
                    with Image.open(BytesIO(original.payload)) as opened:
                        from PIL import ImageOps

                        left, top, right, bottom = layer.source_box
                        crop = (
                            ImageOps.exif_transpose(opened)
                            .convert("RGB")
                            .crop((left - 16, top - 16, right + 16, bottom + 16))
                        )
                        factor = min(4, max(1, 1024 // max(crop.size)))
                        crop = crop.resize(
                            (crop.width * factor, crop.height * factor),
                            Image.Resampling.NEAREST,
                        )
                        reference = source_pixels.artifact(
                            crop, f"matte-original-{index}.png"
                        )
                    evidence = source_pixels.preview(layer.artifact)
                    pair = source_pixels.matte_pair(reference, evidence)
                    previews.append(
                        SourceImage(
                            f"matte-pair-{index}",
                            request.group_key,
                            pair.filename,
                            pair.content_type,
                            pair.sha256,
                            pair.payload,
                            source_role="matte_original_candidate_pair",
                        )
                    )
                matte = await self._plan(
                    request,
                    MATTE,
                    tuple(
                        source
                        for source in authorities
                        if source.asset_id
                        in {
                            region["asset_id"]
                            for region in preparation["selected_regions"]
                        }
                    )
                    + tuple(previews),
                    {
                        "schema": response_schema(MATTE),
                        "correction_contract": {
                            "layer_indices": list(range(len(layers))),
                            "coordinates": (
                                "normalized within the RIGHT CUTOUT panel only, "
                                "INCLUDING its 16px checkerboard margin, "
                                "EXCLUDING the 20px label header and LEFT panel"
                            ),
                            "allowed": (
                                "At most 12 tight source alpha boxes. "
                                "restore_foreground copies source RGB; "
                                "it never generates metal. "
                                "remove_background only inside "
                                "unprinted top fittings, never over "
                                "rims, connectors, faces or printing. "
                                "edge_feather_px 1 or 2 for body halos."
                            ),
                        },
                        "instruction": (
                            "Each matte-pair image shows LEFT ORIGINAL photo "
                            "crop and RIGHT CUTOUT preview of the SAME source "
                            "region at the SAME scale, with evidence-only labels. "
                            "Compare every fitting and printed edge "
                            "directly. Previews include a neutral "
                            "margin so outer contours are visible. "
                            "Compare ONLY visible source hardware; "
                            "never infer hidden levers or mechanisms. "
                            "Distinguish external rack pixels outside the "
                            "product contour from original photographed "
                            "reflections already inside its acrylic surface. "
                            "The latter source RGB must remain unchanged; "
                            "do not propose erasing it across physical print. "
                            "Missing metal, clipped text and residual "
                            "display hardware all fail. Never infer "
                            "completeness from the main body alone. "
                            "For a small source-only mask correction, "
                            "return repairs or edge_feather_px AND "
                            "retain failing checks and unresolved "
                            "defects. A later audit must inspect the "
                            "corrected pixels. Never erase hardware "
                            "as background; distinguish the rack peg "
                            "behind a clasp from the clasp rim. "
                            "If clean, repairs=[] and edge_feather_px=0."
                        ),
                    },
                    suffix=f":mask-{mask_review}",
                )
                plans.append(matte)
                if matte.error_code:
                    raise ValueError("Source matte review unavailable")
                try:
                    Draft202012Validator(response_schema(MATTE)).validate(matte.payload)
                except ValidationError as exc:
                    exc.fidelity_profile = MATTE
                    raise
                accepted = (
                    not matte.payload["unresolved"]
                    and all(
                        matte.payload[name] is True
                        for name in (
                            "complete_objects",
                            "no_context_leak",
                            "no_clipped_print",
                            "edges_clean",
                        )
                    )
                    and not matte.payload["repairs"]
                    and not matte.payload["edge_feather_px"]
                )
                mask_audits.append(
                    {
                        "source_layer_sha256": [
                            layer.artifact.sha256 for layer in layers
                        ],
                        "review": matte.payload,
                    }
                )
                if accepted:
                    break
                logger.warning(
                    "source_matte_rejected job_id=%s item_id=%s "
                    "review=%s failed_checks=%s unresolved_count=%s",
                    request.job_id,
                    request.job_item_id,
                    mask_review,
                    [
                        name
                        for name in (
                            "complete_objects",
                            "no_context_leak",
                            "no_clipped_print",
                            "edges_clean",
                        )
                        if matte.payload[name] is not True
                    ],
                    len(matte.payload["unresolved"]),
                )
                if mask_review or not (
                    matte.payload["repairs"] or matte.payload["edge_feather_px"]
                ):
                    raise ValueError(
                        "Source product extraction failed visual acceptance"
                    )
                layers = await asyncio.to_thread(
                    source_pixels.repair_layers,
                    layers,
                    matte.payload["repairs"],
                    matte.payload["edge_feather_px"],
                )
            self._mattes[key] = (layers, deepcopy(mask_audits))
            return layers, mask_audits

    async def edit(self, request: Any) -> Any:
        from .xinghe_agent import ImageCallResult, SourceImage

        original_request = request
        original_sources = request.sources
        plans: list[Any] = []
        preparation = None
        pixel_recipe = None
        recolor_recipe = None
        recolor_artifact = None
        recolor_proof = None
        inherited_white = None
        try:
            context = self._context(request)
            if any(
                hashlib.sha256(source.payload).hexdigest() != source.sha256
                for source in original_sources
            ):
                raise ValueError("Image fidelity source hash changed")
            if not self._native_pair and (
                context["job_type"] == "detail_pack"
                or (
                    context["job_type"] == "domestic_main_image"
                    and context["settings"].get("main_image_speed") != "native_fast"
                )
            ):
                # Native Harness image requests are signed and have their own
                # source/pixel audits. Never modify a signed request downstream.
                return await self.wrapped.edit(request)
            if (
                context["job_type"] == "amazon_visual_suite"
                and getattr(self.wrapped, "image_fidelity_source_pixels_enabled", False)
            ):
                from . import white_product_lineage

                inherited_white = await white_product_lineage.load(
                    self.sessions, self.storage, request
                )
                if inherited_white is not None:
                    request = replace(
                        request,
                        sources=inherited_white.augment_sources(request.sources),
                    )
                    original_request = request
                    original_sources = request.sources
            frozen = self._frozen(request)
            from . import source_pixels, source_recolor, source_typography

            pixel_mode = (
                bool(frozen.get("pixel_recipe"))
                if frozen is not None
                else not request.reconcile_only
                and getattr(self.wrapped, "image_fidelity_source_pixels_enabled", False)
                and (self._native_pair or source_pixels.enabled_for(context))
            )
            if frozen is not None:
                pixel_recipe = frozen.get("pixel_recipe")
                recolor_recipe = frozen.get("source_recolor_recipe")
            crop_scale = (
                frozen.get("crop_scale", "native-v1")
                if frozen
                else "nearest-4-1024-padded-v2"
            )
            if frozen is not None:
                preparation = frozen["preparation"]
            if context["job_type"] in {
                "product_image",
                "creative_image",
                "reference_remix",
                "amazon_visual_suite",
                "domestic_main_image",
                "detail_pack",
            } and (not request.reconcile_only or frozen is not None):
                authorities, ids, selection, scope = self._selection_parameters(
                    request, context
                )
                preparation = await self._prepare_selection(
                    request,
                    context,
                    original_sources,
                    ids,
                    selection,
                    scope,
                    plans,
                    preparation,
                )
                crops = []
                preparation = validate_preparation(
                    preparation,
                    ids,
                    selection if frozen is None else None,
                    allow_stored=True,
                )
                for index, region in enumerate(preparation["selected_regions"]):
                    if context["settings"].get("task_type") == "change_model":
                        # A duplicated full-person crop reinforces the very face
                        # being replaced. The full garment source stays attached.
                        continue
                    source = next(
                        s for s in authorities if s.asset_id == region["asset_id"]
                    )
                    with Image.open(BytesIO(source.payload)) as im:
                        x0, y0, x1, y1 = region["box"]
                        if crop_scale == "nearest-4-1024-padded-v2":
                            padding = max(
                                8,
                                round(
                                    min((x1 - x0) * im.width, (y1 - y0) * im.height)
                                    * 0.08
                                ),
                            )
                            x0, y0 = (
                                max(0, x0 - padding / im.width),
                                max(0, y0 - padding / im.height),
                            )
                            x1, y1 = (
                                min(1, x1 + padding / im.width),
                                min(1, y1 + padding / im.height),
                            )
                        box = (
                            math.floor(x0 * im.width),
                            math.floor(y0 * im.height),
                            math.ceil(x1 * im.width),
                            math.ceil(y1 * im.height),
                        )
                        buf = BytesIO()
                        detail = im.crop(box)
                        if crop_scale in {
                            "nearest-4-1024-v1",
                            "nearest-4-1024-padded-v2",
                        }:
                            factor = min(4, max(1, 1024 // max(detail.size)))
                            if factor > 1:
                                # Pixel replication enlarges reference evidence;
                                # it invents no glyph strokes. Delivered pixels
                                # are never enlarged by this reference-only step.
                                detail = detail.resize(
                                    (detail.width * factor, detail.height * factor),
                                    Image.Resampling.NEAREST,
                                )
                        detail.save(buf, "PNG")
                    data = buf.getvalue()
                    crops.append(
                        SourceImage(
                            f"fidelity-detail-{index}",
                            request.group_key,
                            f"selected-product-{index + 1}.png",
                            "image/png",
                            hashlib.sha256(data).hexdigest(),
                            data,
                            source_role="product_detail",
                        )
                    )
                edited_sources = original_sources + tuple(crops)
                if frozen is None and pixel_mode:
                    inherited_layers = (
                        inherited_white.selected_layers(preparation)
                        if inherited_white is not None else None
                    )
                    layers, mask_audits = await self._approved_source_layers(
                        request,
                        original_sources,
                        authorities,
                        preparation,
                        plans,
                        inherited_layers,
                    )
                    saved_layers = []
                    for index, layer in enumerate(layers):
                        asset_id = await self._retain(
                            request,
                            layer.artifact,
                            role="fidelity_source_cutout",
                            ordinal=index,
                        )
                        saved_layers.append(
                            {
                                "asset_id": asset_id,
                                "sha256": layer.artifact.sha256,
                                "source_asset_id": layer.source_asset_id,
                                "source_sha256": layer.source_sha256,
                                "source_box": layer.source_box,
                            }
                        )
                    pixel_recipe = {
                        "version": source_pixels.VERSION,
                        "model_sha256": source_pixels.MODEL_SHA256,
                        "layers": saved_layers,
                        "mask_audits": mask_audits,
                        "cells": source_pixels.positions(context, len(layers)),
                    }
                    if inherited_layers is not None and inherited_white is not None:
                        pixel_recipe["white_product_parent"] = inherited_white.binding
                if frozen is None and crops and not pixel_mode:
                    readback_crops = tuple(crops)
                    if source_recolor.enabled(context, original_sources) and getattr(
                        self.wrapped, "image_fidelity_source_pixels_enabled", False
                    ):
                        readback_crops = self._literal_print_evidence(
                            original_request, preparation, "product_detail"
                        )
                    source_readback = await self._plan(
                        request,
                        READBACK,
                        readback_crops,
                        {"schema": response_schema(READBACK)},
                        suffix=":source",
                    )
                    plans.append(source_readback)
                    if (
                        source_readback.error_code
                        or not readback_matches(source_readback.payload, preparation)
                        or any(
                            "UNREADABLE" in text.upper()
                            for text in source_readback.payload["text_runs"]
                        )
                    ):
                        raise ValueError(
                            "Original physical printing could not be verified"
                        )
                    preparation = {
                        **preparation,
                        "literal_print_ledger": source_readback.payload["text_runs"],
                    }
            else:
                edited_sources = original_sources
            if (
                frozen is None
                and not request.reconcile_only
                and getattr(self.wrapped, "image_fidelity_source_pixels_enabled", False)
                and source_recolor.enabled(context, original_sources)
            ):
                source = next(
                    s for s in original_sources if s.source_role == "product_reference"
                )
                if not isinstance(preparation, dict):
                    raise ValueError(
                        "Source metal recolor requires its print authority"
                    )
                print_boxes = [
                    [min(quad[::2]), min(quad[1::2]), max(quad[::2]), max(quad[1::2])]
                    for region in preparation["selected_regions"]
                    for quad in region.get("print_regions", [])
                ]
                selection_feedback = None
                for mask_attempt in range(2):
                    selection = await self._plan(
                        request,
                        source_recolor.PROFILE,
                        (source,),
                        {
                            "settings": context["settings"],
                            "schema": source_recolor.schema(),
                            "protected_print_boxes": print_boxes,
                            "selection_feedback": selection_feedback,
                            "instruction": (
                                "Locate EVERY visible gold metal clasp, ring and "
                                "link requested for silver recoloring. Return tight "
                                "original-image normalized boxes around actual "
                                "metal. Preserve printed tags, faces, acrylic, "
                                "background and display stand. Keep boxes clear of "
                                "the protected print regions. A box may cover <=4% "
                                "of the image. Use separate boxes for separate "
                                "fittings. If selection_feedback is present, inspect "
                                "the same parts again at the reported coordinates, "
                                "narrow erroneous boxes onto actual metal and "
                                "preserve all unaffected boxes. Return the complete "
                                "selection. Report uncertainty instead of guessing "
                                "or silently dropping any visible fitting."
                            ),
                        },
                        suffix=":geometry-repair" if mask_attempt else "",
                    )
                    plans.append(selection)
                    if selection.error_code:
                        raise ValueError("Original metal selection unavailable")
                    recolor_recipe = {
                        "version": source_recolor.VERSION,
                        "source_asset_id": source.asset_id,
                        "source_sha256": source.sha256,
                        "selection": selection.payload,
                        "protected_print_boxes": print_boxes,
                    }
                    try:
                        recolor_artifact, recolor_proof = await asyncio.to_thread(
                            source_recolor.render, source, recolor_recipe
                        )
                    except source_recolor.MetalSelectionError as exc:
                        counts = Counter(
                            problem["reason"] for problem in exc.violations
                        )
                        logger.warning(
                            "source_metal_selection_rejected job_id=%s item_id=%s "
                            "attempt=%s reasons=%s",
                            request.job_id, request.job_item_id, mask_attempt + 1,
                            dict(counts),
                        )
                        if mask_attempt:
                            raise
                        selection_feedback = {
                            "previous_selection": selection.payload,
                            "violations": exc.violations,
                        }
                        continue
                    break
            if frozen is None and not request.reconcile_only:
                if pixel_recipe is not None:
                    # Original product pixels stay local. Send only background
                    # authority (or an empty format reference) to the generator.
                    background_ids = [
                        s.asset_id
                        for s in original_sources
                        if s.source_role
                        in {
                            "style_reference",
                            "background_reference",
                            "scene_reference",
                            "continuity_edge",
                        }
                    ]
                    if (
                        not background_ids
                        and context["job_type"] == "creative_image"
                        and len(original_sources) > 1
                    ):
                        background_ids = [original_sources[1].asset_id]
                    pixel_recipe["background_source_ids"] = background_ids
                    background_schema = response_schema(BACKGROUND)
                    plain_white_eligible = (
                        context["job_type"] == "product_image"
                        and context["settings"].get("task_type") == "white_background"
                        and not background_ids
                        and all(
                            source.source_role == "product_reference"
                            for source in original_sources
                        )
                    )
                    if plain_white_eligible:
                        background_schema["properties"]["plain_white_plate"] = {
                            "type": "boolean",
                            "description": (
                                "True only if a uniform #FFFFFF plate and the "
                                "preserved product cutouts completely satisfy the "
                                "request. Use false for any requested shadow, "
                                "reflection, surface, lighting change, support or "
                                "other visual treatment."
                            ),
                        }
                    layout_panels = 2 if self._native_pair else 1
                    background_schema["properties"]["text_layout"] = (
                        source_typography.layout_schema(layout_panels)
                    )
                    planned_layout = self._native_pair or context["job_type"] in {
                        "amazon_visual_suite",
                        "domestic_main_image",
                        "reference_remix",
                    }
                    crop_allowed = (
                        self._native_pair
                        or context["job_type"]
                        in {"amazon_visual_suite", "domestic_main_image"}
                    ) and context.get("item_key") != "MAIN-01"
                    if planned_layout:
                        background_schema["properties"]["placements"] = {
                            "type": "array",
                            "minItems": layout_panels,
                            "maxItems": 12,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["layer_index", "panel_box", "panel"],
                                "properties": {
                                    "layer_index": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": len(pixel_recipe["layers"]) - 1,
                                    },
                                    "panel_box": {
                                        "type": "array",
                                        "description": (
                                            "Destination rectangle normalized "
                                            "within the selected panel, never "
                                            "source-photo coordinates."
                                        ),
                                        "minItems": 4,
                                        "maxItems": 4,
                                        "items": {
                                            "type": "number",
                                            "minimum": 0.04,
                                            "maximum": 0.96,
                                        },
                                    },
                                    "panel": {
                                        "type": "integer",
                                        "enum": list(range(layout_panels)),
                                    },
                                },
                            },
                        }
                        background_schema["required"].append("placements")
                        if crop_allowed:
                            background_schema["properties"]["placements"]["items"][
                                "properties"
                            ]["source_window"] = {
                                "type": "array",
                                "minItems": 4,
                                "maxItems": 4,
                                "items": {"type": "number", "minimum": 0, "maximum": 1},
                                "description": (
                                    "Only for a close-up explicitly required by "
                                    "the approved module. Normalized coordinates "
                                    "on source_window_reference for this layer; "
                                    "NOT source-photo or destination coordinates. "
                                    "Full product = [0,0,1,1]."
                                ),
                            }
                    if context["job_type"] not in {
                        "amazon_visual_suite",
                        "domestic_main_image",
                        "detail_pack",
                    }:
                        background_schema["properties"]["marketing_copy"][
                            "maxItems"
                        ] = 0
                    projection_sources = tuple(
                        source
                        for source in original_sources
                        if source.asset_id in background_ids
                    )
                    source_cutouts: tuple[Any, ...] = ()
                    projection_inventory = [
                        {"layer_index": i, "identity": region["identity"]}
                        for i, region in enumerate(
                            (preparation or {}).get("selected_regions", [])
                        )
                    ]
                    if planned_layout:
                        source_cutouts = await self._source_layers(
                            request, pixel_recipe, original_sources
                        )
                        references = []
                        for i, cutout in enumerate(source_cutouts):
                            ref = source_pixels.layer_reference(cutout)
                            with Image.open(BytesIO(cutout.payload)) as original_layer:
                                bounds = original_layer.getchannel("A").getbbox()
                            if bounds is None:
                                raise ValueError("Source layout has no original pixels")
                            native_width = bounds[2] - bounds[0]
                            native_height = bounds[3] - bounds[1]
                            references.append(
                                SourceImage(
                                    f"source-window-reference-{i}",
                                    request.group_key,
                                    ref.filename,
                                    ref.content_type,
                                    ref.sha256,
                                    ref.payload,
                                    source_role=f"source_window_reference:{i}",
                                )
                            )
                            projection_inventory[i].update(
                                {
                                    "reference_asset_id": (
                                        f"source-window-reference-{i}"
                                    ),
                                    "width": native_width,
                                    "height": native_height,
                                    "width_over_height": native_width / native_height,
                                    "inspection_preview_size": [ref.width, ref.height],
                                    "max_detail_upscale": (
                                        source_pixels.MAX_DETAIL_UPSCALE
                                    ),
                                }
                            )
                        projection_sources += tuple(references)
                    projection_feedback = None
                    for projection_attempt in range(2):
                        background_plan = await self._plan(
                            request,
                            BACKGROUND,
                            projection_sources,
                            {
                                "schema": background_schema,
                                "projection_feedback": projection_feedback,
                                "settings": context["settings"],
                                "final_image_contract": request.prompt,
                                "reserved_product_cells": None
                                if planned_layout
                                else pixel_recipe["cells"],
                                "native_pair": self._native_pair,
                                "canvas_panel_count": layout_panels,
                                "plain_white_plate_approval": (
                                    "Inspect the original request and fill "
                                    "plain_white_plate according to the schema."
                                    if plain_white_eligible else None
                                ),
                                "copy_layout_contract": (
                                    "Keep ALL approved marketing copy with its "
                                    "EXACT original spelling, panel and hierarchy. "
                                    "The server renders it with the pinned font. "
                                    "Return exactly one text_layout block per copy "
                                    "index; [] when copy is empty. Text boxes must "
                                    "not overlap product cells, other text boxes or "
                                    "the pair seam. Reserve generous readable "
                                    "heading/body space. Background_description "
                                    "describes the product-free scene, all approved "
                                    "supporting props/actors and clear text areas; "
                                    "never requests the generator to draw letters. "
                                    "Required hands, bags and attachment points "
                                    "must not disappear under an 'empty' rule."
                                    + (
                                        " Describe one continuous backdrop "
                                        "across the midpoint, including how "
                                        "light and texture carry across it. "
                                        "Do not specify separate panel backgrounds."
                                        if self._native_pair
                                        else ""
                                    )
                                ),
                                "source_layer_inventory": projection_inventory,
                                "placement_contract": (
                                    "Select source layer indices for EACH approved "
                                    "module. For two panels: top=0, bottom=1. "
                                    "For one panel: panel=0. panel_box uses "
                                    "each PANEL's "
                                    "own coordinates .04-.96. Do NOT use source "
                                    "photo coordinates or full-pair coordinates. "
                                    "Follow the approved subject, copy and layout; "
                                    "a layer may repeat on the other panel. "
                                    "Each layer is ONE product, not the whole "
                                    "group. Use its measured width_over_height. "
                                    "When a group contains several products, "
                                    "split its destination area into separate "
                                    "non-overlapping cells; never repeat the "
                                    "whole group rectangle for every member. "
                                    "Keep room for all approved text. Never cross the "
                                    "seam or overlap cells. Inventory is the "
                                    "permitted pool, not an instruction to "
                                    "include every product in every panel."
                                    "For a module explicitly requiring a connection "
                                    "point or other macro detail, use source_window "
                                    "on its source_window_reference and fill its "
                                    "destination cell with that real detail. Do not "
                                    "replace an approved macro with a tiny full item."
                                    "Inventory width/height are ORIGINAL pixels; "
                                    "the larger inspection preview adds no detail. "
                                    "Inspect the specific requested component in "
                                    "the reference before choosing a crop. Frame "
                                    "the connector with just enough adjacent "
                                    "product to explain how it attaches. Reserve "
                                    "space by using the actual crop aspect ratio, "
                                    "without margins that make the detail smaller. "
                                    "A cropped detail may be enlarged at most "
                                    "max_detail_upscale times. Keep such details "
                                    "as appropriately sized insets; reserve other "
                                    "space for approved text/supporting source views."
                                )
                                if planned_layout
                                else "Use supplied reserved cells.",
                            },
                            suffix=f":layout-{projection_attempt}",
                        )
                        plans.append(background_plan)
                        if background_plan.error_code:
                            raise ValueError("Background projection unavailable")
                        trial_recipe = deepcopy(pixel_recipe)
                        try:
                            Draft202012Validator(background_schema).validate(
                                background_plan.payload
                            )
                            projection = deepcopy(background_plan.payload)
                            trial_recipe["background_projection"] = projection
                            detail_box_limits = {}
                            if crop_allowed:
                                candidate_indices, candidate_cells = (
                                    source_pixels.native_placements(
                                        projection["placements"],
                                        len(source_cutouts),
                                        layout_panels,
                                        check_overlap=False,
                                    )
                                )
                                windows = [
                                    p.get("source_window", [0, 0, 1, 1])
                                    for p in projection["placements"]
                                ]
                                detail_cutouts = source_pixels.crop_windows(
                                    tuple(source_cutouts[i] for i in candidate_indices),
                                    windows,
                                )
                                detail_width, detail_height = (
                                    int(v) for v in request.size.split("x")
                                )
                                detail_box_limits = (
                                    source_pixels.detail_inset_box_limits(
                                        detail_cutouts,
                                        candidate_cells,
                                        windows,
                                        (detail_width, detail_height),
                                        panels=layout_panels,
                                    )
                                )
                            if planned_layout:
                                from . import source_layout

                                placements, changes = (
                                    source_layout.place_products_without_collisions(
                                        projection["placements"],
                                        projection["text_layout"], layout_panels,
                                        detail_box_limits=detail_box_limits,
                                    )
                                )
                                projection["placements"] = placements
                                if changes:
                                    trial_recipe["product_layout_adjustments"] = {
                                        "version": source_layout.VERSION,
                                        "original_projection_sha256": canonical_hash(
                                            background_plan.payload
                                        ),
                                        "changes": changes,
                                    }
                                indices, cells = source_pixels.native_placements(
                                    placements,
                                    len(trial_recipe["layers"]),
                                    panels=layout_panels,
                                )
                                trial_recipe["placement_indices"] = indices
                                trial_recipe["cells"] = cells
                            if crop_allowed:
                                source_pixels.validate_detail_resolution(
                                    detail_cutouts, cells, windows,
                                    (detail_width, detail_height),
                                    panels=layout_panels,
                                )
                                trial_recipe["source_windows"] = windows
                            text_width, text_height = (
                                int(v) for v in request.size.split("x")
                            )
                            layout, adjustments = (
                                source_typography.place_text_without_collisions(
                                    projection["text_layout"], trial_recipe["cells"],
                                    layout_panels,
                                )
                            )
                            projection["text_layout"] = layout
                            if adjustments:
                                trial_recipe["text_layout_adjustments"] = {
                                    "original_projection_sha256": canonical_hash(
                                        background_plan.payload
                                    ),
                                    "changes": adjustments,
                                }
                            trial_recipe["text_recipe"] = source_typography.recipe(
                                projection["marketing_copy"],
                                layout,
                                trial_recipe["cells"],
                                layout_panels,
                                getattr(
                                    self.wrapped,
                                    "image_fidelity_font_path",
                                    source_typography.FONT_PATH,
                                ),
                                (text_width, text_height),
                            )
                        except (ValidationError, ValueError) as projection_error:
                            if isinstance(projection_error, ValidationError):
                                projection_error.fidelity_profile = BACKGROUND
                            if projection_attempt:
                                logger.warning(
                                    "background_projection_rejected job_id=%s "
                                    "item_id=%s attempt=2 diagnostic=%s",
                                    request.job_id, request.job_item_id,
                                    contract_diagnostic(projection_error)
                                    if isinstance(projection_error, ValidationError)
                                    else projection_error.diagnostic
                                    if isinstance(
                                        projection_error,
                                        source_pixels.DetailResolutionError,
                                    )
                                    else {"reason": str(projection_error)[:240]},
                                )
                                raise
                            projection_feedback = {
                                "diagnostic": contract_diagnostic(projection_error)
                                if isinstance(projection_error, ValidationError)
                                else projection_error.diagnostic
                                if isinstance(
                                    projection_error,
                                    source_pixels.DetailResolutionError,
                                )
                                else {"reason": str(projection_error)[:240]},
                                "previous_projection": background_plan.payload,
                                "instruction": (
                                    "Correct the invalid layout against the full "
                                    "unchanged contract, exact approved copy "
                                    "and source references. No missing copy, "
                                    "overlap, guessed geometry or unreadable "
                                    "type; all original validators run again."
                                ),
                            }
                            logger.warning(
                                "background_projection_retry job_id=%s "
                                "item_id=%s diagnostic=%s",
                                request.job_id,
                                request.job_item_id,
                                projection_feedback["diagnostic"],
                            )
                            continue
                        pixel_recipe = trial_recipe
                        break
                    if not background_ids:
                        import base64

                        width, height = (
                            int(value) for value in request.size.split("x")
                        )
                        empty = source_pixels.artifact(
                            Image.new("RGB", (width, height), "white"),
                            "native-format.png",
                        )
                        pixel_recipe["format_png_base64"] = base64.b64encode(
                            empty.payload
                        ).decode("ascii")
                        if (
                            plain_white_eligible
                            and pixel_recipe["background_projection"].get(
                                "plain_white_plate"
                            ) is True
                        ):
                            # This projection authorizes a plain white plate. The
                            # existing format image already supplies those pixels.
                            pixel_recipe["local_white_background"] = True
                prepared_prompt = (
                    request.prompt
                    + "\nORIGINAL USER REQUIREMENT (overrides generated plans): "
                    + str(context["settings"].get("instruction", ""))
                    + "\n"
                    + operation_policy(context)
                    + "\nSOURCE_SELECTION="
                    + json.dumps(
                        {
                            k: v
                            for k, v in preparation.items()
                            if k != "source_inventory"
                        }
                        if isinstance(preparation, dict)
                        else preparation,
                        ensure_ascii=False,
                    )
                )
                if pixel_recipe is not None:
                    cutouts = await self._source_layers(
                        request, pixel_recipe, original_sources
                    )
                    if "placement_indices" in pixel_recipe:
                        cutouts = tuple(
                            cutouts[i] for i in pixel_recipe["placement_indices"]
                        )
                    cutouts = source_pixels.crop_windows(
                        cutouts, pixel_recipe.get("source_windows")
                    )
                    native_size = tuple(int(v) for v in request.size.split("x"))
                    placement_geometry = []
                    for i, (cutout, cell) in enumerate(
                        zip(cutouts, pixel_recipe["cells"], strict=True)
                    ):
                        _, box, _ = source_pixels.placed_layer(
                            cutout, (native_size[0], native_size[1]), cell
                        )
                        x0, y0, x1, y1 = (
                            value / native_size[axis % 2]
                            for axis, value in enumerate(box)
                        )
                        placement_geometry.append(
                            {
                                "placement": i,
                                "visible_product_bounds": [x0, y0, x1, y1],
                                "upper_edge_midpoint": [(x0 + x1) / 2, y0],
                                "center": [(x0 + x1) / 2, (y0 + y1) / 2],
                            }
                        )
                    pixel_recipe["wire_prompt"] = source_pixels.background_prompt(
                        pixel_recipe["background_projection"]["background_description"],
                        pixel_recipe["cells"],
                        (),
                        placement_geometry,
                        continuous_pair=self._native_pair,
                    )
                frozen = self._frozen(
                    request,
                    {
                        "preparation": preparation,
                        "crop_scale": crop_scale,
                        "pixel_recipe": pixel_recipe,
                        "source_recolor_recipe": recolor_recipe,
                        "prompt": prepared_prompt,
                    },
                )
            if frozen is not None:
                request = replace(
                    request,
                    sources=edited_sources,
                    prompt=frozen["prompt"],
                )
                if pixel_recipe is not None:
                    wire_sources = tuple(
                        s
                        for s in original_sources
                        if s.asset_id in pixel_recipe["background_source_ids"]
                    )
                    if not wire_sources:
                        import base64

                        data = base64.b64decode(
                            pixel_recipe["format_png_base64"], validate=True
                        )
                        wire_sources = (
                            SourceImage(
                                "source-pixel-format",
                                request.group_key,
                                "native-format.png",
                                "image/png",
                                hashlib.sha256(data).hexdigest(),
                                data,
                                source_role="format_canvas",
                            ),
                        )
                    request = replace(
                        request,
                        sources=wire_sources,
                        prompt=pixel_recipe["wire_prompt"],
                    )
            if pixel_recipe is not None and frozen is None:
                raise ValueError("Source pixel dispatch requires its frozen receipt")
            if recolor_recipe is not None and recolor_artifact is None:
                source = next(
                    s
                    for s in original_sources
                    if s.asset_id == recolor_recipe["source_asset_id"]
                )
                recolor_artifact, recolor_proof = await asyncio.to_thread(
                    source_recolor.render, source, recolor_recipe
                )
        except Exception as exc:
            if isinstance(exc, ValidationError):
                logger.warning(
                    "image_fidelity_contract_invalid job_id=%s "
                    "profile=%s diagnostic=%s",
                    request.job_id,
                    getattr(exc, "fidelity_profile", PREPARE),
                    contract_diagnostic(exc),
                )
            logger.warning(
                "image_fidelity_preparation_failed job_id=%s item_id=%s reason=%s",
                request.job_id,
                request.job_item_id,
                type(exc).__name__,
            )
            return self._account(
                ImageCallResult(
                    ProviderOutcome.FAILED,
                    None,
                    {"network_requests": 0, "fidelity_preparation_failed": True},
                    {
                        "reported": True,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "total_tokens": 0,
                    },
                    0.0,
                    0,
                    estimated_cost_micros=0,
                    error_code="IMAGE_FIDELITY_PREPARATION_FAILED",
                    error_message="未能完整确认需保留的商品与图案，尚未发送生图请求。",
                ),
                plans,
            )
        if recolor_artifact is not None:
            result = ImageCallResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id=None,
                raw_usage={"network_requests": 0, "source_recolor": recolor_proof},
                normalized_usage={
                    "reported": True,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                api_latency_seconds=0,
                actual_cost_micros=0,
                estimated_cost_micros=0,
                artifact=recolor_artifact,
            )
        elif pixel_recipe and pixel_recipe.get("local_white_background"):
            # Older recipes have no local marker and keep their original supplier
            # request, including read-only reconciliation of an accepted task.
            (plate,) = request.sources
            width, height = (int(value) for value in request.size.split("x"))
            result = ImageCallResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id=None,
                raw_usage={"network_requests": 0, "background_origin": "local_white"},
                normalized_usage={
                    "reported": True,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                api_latency_seconds=0,
                actual_cost_micros=0,
                estimated_cost_micros=0,
                artifact=ProviderArtifact(
                    payload=plate.payload,
                    filename="white-background.png",
                    content_type=plate.content_type,
                    width=width,
                    height=height,
                    sha256=plate.sha256,
                ),
            )
        else:
            result = await self.wrapped.edit(request)
        if result.outcome != ProviderOutcome.SUCCEEDED or result.artifact is None:
            return self._account(result, plans)
        pixel_proof = None
        if (
            pixel_recipe is not None
            and technical_contract(context, request, result.artifact)[
                "server_technical_checks_passed"
            ]
        ):
            try:
                await self._retain(request, result.artifact, role="fidelity_background")
                cutouts = await self._source_layers(
                    request, pixel_recipe, original_sources
                )
                if "placement_indices" in pixel_recipe:
                    cutouts = tuple(
                        cutouts[i] for i in pixel_recipe["placement_indices"]
                    )
                cutouts = source_pixels.crop_windows(
                    cutouts, pixel_recipe.get("source_windows")
                )
                composed, pixel_proof = source_pixels.compose(
                    result.artifact, cutouts, pixel_recipe["cells"]
                )
                if pixel_recipe.get("text_recipe") is not None:
                    from . import source_typography

                    texted, text_proof = source_typography.render(
                        composed,
                        pixel_recipe["text_recipe"],
                        getattr(
                            self.wrapped,
                            "image_fidelity_font_path",
                            source_typography.FONT_PATH,
                        ),
                    )
                    pixel_proof = {
                        **pixel_proof,
                        "product_composite_sha256": composed.sha256,
                        "composite_sha256": texted.sha256,
                        "typography": text_proof,
                    }
                    composed = texted
                derived_metrics = {}
                if (
                    context["job_type"] == "product_image"
                    and context["settings"].get("task_type") == "white_background"
                ):
                    from .white_product_lineage import pointer

                    derived_metrics["white_product_lineage"] = pointer(
                        original_request, pixel_recipe, pixel_proof, composed.sha256
                    )
                result = replace(
                    result,
                    artifact=composed,
                    raw_usage={
                        **dict(result.raw_usage or {}),
                        "source_pixel_preservation": pixel_proof,
                        "metrics": {
                            **dict((result.raw_usage or {}).get("metrics", {})),
                            **derived_metrics,
                        },
                    },
                )
            except Exception as exc:
                logger.warning(
                    "source_pixel_composition_failed job_id=%s reason=%s",
                    request.job_id,
                    type(exc).__name__,
                )
                return replace(
                    self._account(result, plans),
                    outcome=ProviderOutcome.FAILED,
                    artifact=None,
                    retryable=False,
                    error_code="IMAGE_FIDELITY_COMPOSITION_FAILED",
                    error_message="原图保真合成未完成，未交付；原生图回执已保留。",
                )
        review_sources = edited_sources if pixel_recipe is not None else request.sources
        review_request = (
            replace(request, prompt=frozen["prompt"])
            if pixel_recipe is not None and frozen is not None
            else request
        )
        if pixel_proof is not None:
            try:
                review_sources += await self.paired_source_evidence(
                    original_request, result.artifact
                )
            except Exception as exc:
                logger.warning(
                    "image_fidelity_evidence_failed job_id=%s item_id=%s reason=%s",
                    request.job_id,
                    request.job_item_id,
                    type(exc).__name__,
                )
                try:
                    await self._retain(request, result.artifact)
                except Exception as retention_error:
                    logger.warning(
                        "image_fidelity_retention_failed job_id=%s reason=%s",
                        request.job_id,
                        type(retention_error).__name__,
                    )
                return replace(
                    self._account(result, plans),
                    outcome=ProviderOutcome.FAILED,
                    artifact=None,
                    retryable=False,
                    error_code="IMAGE_FIDELITY_EVIDENCE_FAILED",
                    error_message="原图对照证据未能完整确认，未交付。",
                )
        return await self._review(
            review_request,
            context,
            review_sources,
            preparation,
            plans,
            result,
            pixel_proof=pixel_proof,
            recolor_proof=recolor_proof,
        )

    async def _source_layers(
        self, request: Any, recipe: dict[str, Any], sources: tuple[Any, ...]
    ) -> tuple[Any, ...]:
        from . import source_pixels
        from .provider import ProviderArtifact

        if (
            recipe["version"] != source_pixels.VERSION
            or recipe["model_sha256"] != source_pixels.MODEL_SHA256
        ):
            raise ValueError("Saved source pixel recipe version changed")
        records = []
        with self.sessions() as session:
            for row in recipe["layers"]:
                asset = session.scalar(
                    select(AssetRecord)
                    .join(
                        JobAssetRecord,
                        JobAssetRecord.asset_id == AssetRecord.id,
                    )
                    .where(
                        AssetRecord.id == row["asset_id"],
                        AssetRecord.workspace_id == request.workspace_id,
                        AssetRecord.lifecycle_status == "ready",
                        AssetRecord.deleted_at.is_(None),
                        JobAssetRecord.job_id == request.job_id,
                        JobAssetRecord.job_item_id == request.job_item_id,
                        JobAssetRecord.workspace_id == request.workspace_id,
                        JobAssetRecord.role == "fidelity_source_cutout",
                        JobAssetRecord.is_deliverable.is_(False),
                    )
                )
                if asset is None or asset.sha256 != row["sha256"]:
                    raise ValueError(
                        "Saved source product layer is outside its immutable scope"
                    )
                records.append(
                    (
                        asset.object_key,
                        asset.sha256,
                        asset.byte_size,
                        asset.width,
                        asset.height,
                        row,
                    )
                )
        cutouts = []
        by_id = {source.asset_id: source for source in sources}
        for key, expected, size, width, height, row in records:
            payload = await self.storage.read_bytes(key)
            if len(payload) != size or hashlib.sha256(payload).hexdigest() != expected:
                raise ValueError("Saved source product layer bytes changed")
            with Image.open(BytesIO(payload)) as image:
                if image.size != (width, height) or "A" not in image.getbands():
                    raise ValueError("Saved product matte geometry changed")
                rgb = image.convert("RGB").tobytes()
            original = by_id.get(row["source_asset_id"])
            if original is None or original.sha256 != row["source_sha256"]:
                raise ValueError("Source product authority changed")
            with Image.open(BytesIO(original.payload)) as image:
                from PIL import ImageOps

                observed = (
                    ImageOps.exif_transpose(image)
                    .convert("RGB")
                    .crop(tuple(row["source_box"]))
                )
                if observed.size != (width, height) or observed.tobytes() != rgb:
                    raise ValueError(
                        "Saved product RGB differs from the original photo"
                    )
            cutouts.append(
                ProviderArtifact(
                    payload=payload,
                    filename="source-product.png",
                    content_type="image/png",
                    width=width,
                    height=height,
                    sha256=expected,
                )
            )
        return tuple(cutouts)

    async def paired_source_evidence(
        self, request: Any, candidate: Any
    ) -> tuple[Any, ...]:
        from .source_pixels import comparison_panels, crop_windows
        from .xinghe_agent import SourceImage

        frozen = self._frozen(request)
        if frozen is None or not frozen.get("pixel_recipe"):
            return self.selected_source_evidence(request)
        recipe = frozen["pixel_recipe"]
        cutouts = await self._source_layers(request, recipe, request.sources)
        if "placement_indices" in recipe:
            cutouts = tuple(cutouts[i] for i in recipe["placement_indices"])
        cutouts = crop_windows(cutouts, recipe.get("source_windows"))
        pairs = await asyncio.to_thread(
            comparison_panels, candidate, cutouts, recipe["cells"]
        )
        return tuple(
            SourceImage(
                f"source-candidate-pair-{i}",
                request.group_key,
                pair.filename,
                pair.content_type,
                pair.sha256,
                pair.payload,
                source_role="source_candidate_comparison",
            )
            for i, pair in enumerate(pairs)
        )

    def selected_source_evidence(self, request: Any) -> tuple[Any, ...]:
        """Return actual original crops bound to this frozen authorized call."""
        frozen = self._frozen(request)
        if frozen is None or not frozen.get("preparation"):
            return ()
        return self._selection_evidence(request, frozen["preparation"])

    def _selection_evidence(
        self, request: Any, preparation: dict[str, Any]
    ) -> tuple[Any, ...]:
        from PIL import ImageOps

        from .source_pixels import artifact
        from .xinghe_agent import SourceImage

        sources = {source.asset_id: source for source in request.sources}
        evidence = []
        for index, region in enumerate(preparation["selected_regions"]):
            original = sources[region["asset_id"]]
            if hashlib.sha256(original.payload).hexdigest() != original.sha256:
                raise ValueError("Original source evidence bytes changed")
            with Image.open(BytesIO(original.payload)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                x0, y0, x1, y1 = region["box"]
                crop = image.crop(
                    (
                        max(0, math.floor(x0 * image.width) - 12),
                        max(0, math.floor(y0 * image.height) - 12),
                        min(image.width, math.ceil(x1 * image.width) + 12),
                        min(image.height, math.ceil(y1 * image.height) + 12),
                    )
                )
                factor = min(4, max(1, 1024 // max(crop.size)))
                crop = crop.resize(
                    (crop.width * factor, crop.height * factor),
                    Image.Resampling.NEAREST,
                )
                ref = artifact(crop, f"original-selected-{index}.png")
            evidence.append(
                SourceImage(
                    f"original-selected-{index}",
                    request.group_key,
                    ref.filename,
                    ref.content_type,
                    ref.sha256,
                    ref.payload,
                    source_role="product_authority_detail",
                )
            )
        return tuple(evidence)

    def _literal_print_evidence(
        self, request: Any, preparation: dict[str, Any], role: str
    ) -> tuple[Any, ...]:
        from .source_print_evidence import crops
        from .xinghe_agent import SourceImage

        return tuple(
            SourceImage(
                f"print-detail-{i}", request.group_key, f"print-detail-{i}.png",
                view.content_type, view.sha256, view.payload, source_role=role,
            )
            for i, view in enumerate(crops(request.sources, preparation))
        )

    async def prepare_source_identity(
        self, request: Any, *, stored_preparation: Any = None
    ) -> dict[str, Any]:
        """Measure original source geometry before a remix authors its brief.

        This never freezes a generation prompt or submits an image. Returned
        planner calls must be recorded by the caller, including on failure.
        """
        from . import source_pixels

        plans: list[Any] = []
        try:
            context = self._context(request)
            if not getattr(
                self.wrapped, "image_fidelity_source_pixels_enabled", False
            ) or not source_pixels.enabled_for(context):
                return {"enabled": False, "calls": []}
            if any(
                hashlib.sha256(s.payload).hexdigest() != s.sha256
                for s in request.sources
            ):
                raise ValueError("Original source identity bytes changed")
            authorities, ids, selection, scope = self._selection_parameters(
                request, context
            )
            if stored_preparation is not None:
                stored_preparation = validate_preparation(
                    stored_preparation, ids, selection, allow_stored=True
                )
            preparation = await self._prepare_selection(
                request,
                context,
                request.sources,
                ids,
                selection,
                scope,
                plans,
                stored_preparation,
            )
            self._prepared[scope] = preparation
            layers, _ = await self._approved_source_layers(
                request, request.sources, authorities, preparation, plans
            )
            geometry = []
            for i, layer in enumerate(layers):
                with Image.open(BytesIO(layer.artifact.payload)) as opened:
                    visible = opened.getchannel("A").getbbox()
                if visible is None:
                    raise ValueError("Source geometry has no visible pixels")
                width, height = visible[2] - visible[0], visible[3] - visible[1]
                geometry.append(
                    {
                        "layer_index": i,
                        "width": width,
                        "height": height,
                        "width_over_height": width / height,
                        "source_sha256": layer.source_sha256,
                    }
                )
            return {
                "enabled": True,
                "calls": plans,
                "preparation": preparation,
                "sources": self._selection_evidence(request, preparation),
                "geometry": geometry,
            }
        except Exception as exc:
            logger.warning(
                "source_identity_preflight_failed job_id=%s reason=%s contract=%s",
                request.job_id,
                type(exc).__name__,
                contract_diagnostic(exc) if isinstance(exc, ValidationError) else None,
            )
            return {
                "enabled": True,
                "calls": plans,
                "error": "SOURCE_IDENTITY_PREPARATION_FAILED",
            }

    async def review_existing(
        self,
        request: Any,
        *,
        sources: tuple[Any, ...],
        prompt: str,
        size: str,
        artifact: Any,
    ) -> Any:
        """Review a hash-verified original without dispatching a replacement image."""
        from .provider import ProviderResult
        from .xinghe_agent import ImageCallResult, ImageEditRequest

        image_request = ImageEditRequest(
            request.workspace_id,
            request.job_id,
            request.job_item_id,
            request.attempt_id,
            1,
            request.job_item_id,
            sources,
            prompt,
            "high",
            request.idempotency_key,
            size,
        )
        context = self._context(image_request)
        plans: list[Any] = []
        preparation = None
        # A legacy supplier cache is not an exemption from source selection or
        # literal printing acceptance. This path cannot call edit, freeze a new
        # generation request, reconstruct a matte, or purchase a replacement.
        try:
            if any(hashlib.sha256(s.payload).hexdigest() != s.sha256 for s in sources):
                raise ValueError("Cached review source hash changed")
            ids = tuple(s.asset_id for s in sources if s.source_role in AUTHORITY_ROLES)
            selection = selection_constraint(
                str(context["settings"].get("instruction", ""))
            )
            if selection:
                ids = ids[:1]
            if not ids:
                raise ValueError("Cached review lacks product authority")
            plan = await self._plan(
                image_request,
                PREPARE,
                sources,
                {
                    **context,
                    "schema": response_schema(PREPARE, ids, selection),
                    "original_user_instruction": context["settings"].get(
                        "instruction", ""
                    ),
                    "spatial_selection_constraint": selection,
                },
                suffix=":cached",
            )
            plans.append(plan)
            if plan.error_code:
                raise ValueError("Cached source review unavailable")
            preparation = validate_preparation(plan.payload, ids, selection)
            from .xinghe_agent import SourceImage

            crops = []
            for index, region in enumerate(preparation["selected_regions"]):
                original = next(s for s in sources if s.asset_id == region["asset_id"])
                with Image.open(BytesIO(original.payload)) as opened:
                    x0, y0, x1, y1 = region["box"]
                    crop = opened.crop(
                        (
                            math.floor(x0 * opened.width),
                            math.floor(y0 * opened.height),
                            math.ceil(x1 * opened.width),
                            math.ceil(y1 * opened.height),
                        )
                    )
                    factor = min(4, max(1, 1024 // max(crop.size)))
                    crop = crop.resize(
                        (crop.width * factor, crop.height * factor),
                        Image.Resampling.NEAREST,
                    )
                    output = BytesIO()
                    crop.save(output, "PNG")
                payload = output.getvalue()
                crops.append(
                    SourceImage(
                        f"cached-source-{index}",
                        image_request.group_key,
                        "source-print.png",
                        "image/png",
                        hashlib.sha256(payload).hexdigest(),
                        payload,
                        source_role="product_detail",
                    )
                )
            readback = await self._plan(
                image_request,
                READBACK,
                tuple(crops),
                {"schema": response_schema(READBACK)},
                suffix=":cached-source",
            )
            plans.append(readback)
            if (
                readback.error_code
                or not readback_matches(readback.payload, preparation)
                or any("UNREADABLE" in t.upper() for t in readback.payload["text_runs"])
            ):
                raise ValueError("Cached source printing could not be verified")
            preparation = {
                **preparation,
                "literal_print_ledger": readback.payload["text_runs"],
            }
        except Exception as exc:
            logger.warning(
                "cached_image_source_review_failed job_id=%s reason=%s",
                request.job_id,
                type(exc).__name__,
            )
            failed = self._account(
                ImageCallResult(
                    ProviderOutcome.FAILED,
                    None,
                    {"network_requests": 0},
                    {"reported": True},
                    0.0,
                    0,
                    estimated_cost_micros=0,
                ),
                plans,
            )
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                error_code="IMAGE_FIDELITY_PREPARATION_FAILED",
                error_message="保留原图的来源与印刷尚未通过验收，未发起替代生图。",
                retryable=False,
                usage_reported=failed.normalized_usage["reported"],
                raw_usage=failed.raw_usage,
                actual_cost_micros=failed.actual_cost_micros,
                estimated_cost_micros=failed.estimated_cost_micros,
                provider_name=self.provider_name,
                model_name=self.image_model,
                pricing_version=self.pricing_version,
            )
        result = await self._review(
            image_request,
            context,
            sources,
            preparation,
            plans,
            ImageCallResult(
                ProviderOutcome.SUCCEEDED,
                None,
                {"network_requests": 0},
                {"reported": True},
                0.0,
                0,
                estimated_cost_micros=0,
                artifact=artifact,
            ),
        )
        return ProviderResult(
            outcome=result.outcome,
            artifact=result.artifact,
            error_code=result.error_code,
            error_message=result.error_message,
            retryable=False,
            usage_reported=result.normalized_usage["reported"],
            raw_usage={**result.raw_usage, "reused": True},
            actual_cost_micros=result.actual_cost_micros,
            estimated_cost_micros=result.estimated_cost_micros,
            provider_name=self.provider_name,
            model_name=self.image_model,
            pricing_version=self.pricing_version,
        )

    async def _review(
        self,
        request: Any,
        context: dict[str, Any],
        original_sources: tuple[Any, ...],
        preparation: Any,
        plans: list[Any],
        result: Any,
        *,
        pixel_proof: dict[str, Any] | None = None,
        recolor_proof: dict[str, Any] | None = None,
    ) -> Any:
        from .xinghe_agent import SourceImage

        candidate = result.artifact
        review_source = SourceImage(
            "candidate",
            request.group_key,
            candidate.filename,
            candidate.content_type,
            candidate.sha256,
            candidate.payload,
            source_role="candidate",
        )
        accepted = False
        failed = ["review_unavailable"]
        technical = technical_contract(context, request, candidate)
        if recolor_proof is not None:
            technical["policy"] = (
                "Local source-only gold-to-silver recolor: original photo "
                "dimensions, geometry and outside-mask RGB are unchanged. "
                "No supplier image was purchased or resized. Existing aspect "
                "and full-canvas checks still apply."
            )
        feedback = None
        for attempt in range(2):
            review_context = {
                **context,
                "generation_contract": request.prompt,
                "source_selection": preparation,
                "native_pair": self._native_pair,
                "marketing_canvas": context["job_type"]
                in {"detail_pack", "amazon_visual_suite", "domestic_main_image"},
                "candidate_dimensions": [candidate.width, candidate.height],
                "technical_contract": technical,
                "server_source_pixel_proof": pixel_proof,
                "source_recolor_proof": recolor_proof,
                "contract_feedback": feedback,
                "source_roles": [(s.asset_id, s.source_role) for s in original_sources],
                "schema": response_schema(REVIEW),
                "instruction": "Compare candidate against original authority, "
                "including each selected design and exact visible physical text. "
                "For native_pair or marketing_canvas, the generation contract "
                "determines "
                "which permitted source products appear in each panel. The "
                "source selection is the authority pool, not a requirement to "
                "show all products in every module. Inspect both approved "
                "modules, their exact copy and the clean pair seam. "
                "An explicitly approved macro module must show its requested "
                "original connection/detail crop, rather than a tiny whole "
                "product. Only that approved macro framing may omit the "
                "rest of the object; hero/full-product views still require "
                "complete objects. All visible source details must match. "
                "For change_model, short sleeves must remain short sleeves, "
                "never the reference's tank top. Do not declare pass if source "
                "printing changed or uncertainty/defects remain. "
                "observable_defects lists only violations of the request, "
                "not authorized changes. unresolved lists only uncertainty "
                "that prevents deciding compliance with the actual requested "
                "edit, protected pixels or approved module. Unknown material, "
                "weight, licensing or package facts that are neither requested "
                "nor claimed are not unresolved delivery defects: do not invent "
                "a requirement to establish them. A claimed unsupported fact "
                "or genuinely uncertain protected detail must still fail. "
                "Decoded candidate dimensions are "
                "measured facts supplied by the server. For canvas, apply the "
                "technical_contract's EXISTING native-original acceptance policy, "
                "not an invented exact-pixel export requirement. A failed measured "
                "technical check must fail canvas. Subject and printing checks "
                "still require direct inspection of actual attached pixels. "
                "If server_source_pixel_proof is present, source RGB was checked "
                "byte-for-byte before compositing: glyphs were copied from the "
                "source, not invented by the background generator. This proves "
                "provenance, NOT completeness: still reject clipped/missing "
                "printing, poor matte boundaries, wrong subjects, overlaps or "
                "extra products. Do not invent a changed transcription from "
                "uncertain OCR when the actual source pixels were preserved.",
            }
            try:
                review = await self._plan(
                    request,
                    REVIEW,
                    original_sources + (review_source,),
                    review_context,
                    suffix=f":{attempt + 1}",
                )
            except Exception as exc:
                logger.warning(
                    "image_fidelity_review_unavailable job_id=%s item_id=%s reason=%s",
                    request.job_id,
                    request.job_item_id,
                    type(exc).__name__,
                )
                break
            plans.append(review)
            try:
                if review.error_code:
                    raise ValueError("Review unavailable")
                accepted, failed = validate_review(review.payload)
                if technical["server_technical_checks_passed"] is not True:
                    accepted = False
                    if "canvas" not in failed:
                        failed.append("canvas")
                break
            except ValidationError as exc:
                feedback = contract_diagnostic(exc)
                logger.warning(
                    "image_fidelity_contract_invalid job_id=%s profile=%s "
                    "attempt=%s diagnostic=%s",
                    request.job_id,
                    REVIEW,
                    attempt + 1,
                    feedback,
                )
                continue
            except (ValueError, TypeError, KeyError):
                continue
        if (
            accepted
            and preparation
            and pixel_proof is None
            and (
                preparation.get("literal_print_ledger")
                or any(
                    text.upper() != "UNREADABLE"
                    for region in preparation["selected_regions"]
                    for text in region["visible_print"]
                )
            )
        ):
            try:
                # No original, expected transcription, generated plan or user
                # prompt is supplied: the reader cannot copy the answer key.
                readback_sources = (review_source,)
                if recolor_proof is not None:
                    # Local recoloring keeps original dimensions and geometry.
                    # Give the independent reader actual candidate crops at the
                    # same evidence scale as source OCR, without any answer key.
                    candidate_asset_id = preparation["selected_regions"][0]["asset_id"]
                    candidate_request = replace(
                        request,
                        sources=(replace(review_source, asset_id=candidate_asset_id),),
                    )
                    readback_sources = tuple(
                        replace(
                            view,
                            asset_id=f"candidate-detail-{i}",
                            filename=f"candidate-detail-{i}.png",
                            source_role="candidate_detail",
                        )
                        for i, view in enumerate(
                            self._literal_print_evidence(
                                candidate_request, preparation, "candidate_detail"
                            )
                        )
                    )
                readback = await self._plan(
                    request,
                    READBACK,
                    readback_sources,
                    {"schema": response_schema(READBACK)},
                )
                plans.append(readback)
                if readback.error_code or not readback_matches(
                    readback.payload, preparation
                ):
                    accepted = False
                    failed.append("physical_text_readback")
            except Exception as exc:
                accepted = False
                failed.append("physical_text_readback_unavailable")
                logger.warning(
                    "image_fidelity_readback_unavailable job_id=%s reason=%s",
                    request.job_id,
                    type(exc).__name__,
                )
        result = self._account(result, plans)
        raw = dict(result.raw_usage or {})
        raw["metrics"] = {
            **raw.get("metrics", {}),
            "image_fidelity": {
                **raw.get("metrics", {}).get("image_fidelity", {}),
                "version": VERSION,
                "accepted": accepted,
                "failed_checks": failed,
                "candidate_sha256": candidate.sha256,
            },
        }
        if pixel_proof is not None:
            raw["metrics"]["rendering"] = {
                "kind": "native_background_source_products",
                "method": pixel_proof["method"],
                "background_sha256": pixel_proof["background_sha256"],
                "result_sha256": candidate.sha256,
                "version": pixel_proof["version"],
                "font_sha256": (pixel_proof.get("typography") or {}).get("font_sha256"),
            }
        elif recolor_proof is not None:
            raw["metrics"]["rendering"] = {
                "kind": "source_original_recolor",
                "method": recolor_proof["method"],
                "source_sha256": recolor_proof["source_sha256"],
                "result_sha256": candidate.sha256,
                "version": recolor_proof["version"],
                "changed_pixels": recolor_proof["changed_pixels"],
                "outside_mask_changed_pixels": 0,
            }
        if not accepted:
            try:
                asset_id = await self._retain(request, candidate)
            except Exception as exc:
                logger.warning(
                    "image_fidelity_retention_failed job_id=%s item_id=%s reason=%s",
                    request.job_id,
                    request.job_item_id,
                    type(exc).__name__,
                )
                return replace(
                    result,
                    outcome=ProviderOutcome.FAILED,
                    artifact=None,
                    error_code="IMAGE_FIDELITY_AUDIT_FAILED",
                    error_message="保真审核或候选留存未完成，未交付。",
                    retryable=False,
                    raw_usage=raw,
                )
            raw["metrics"]["image_fidelity"]["retained_asset_id"] = asset_id
            logger.warning(
                "image_fidelity_rejected job_id=%s item_id=%s failed_checks=%s",
                request.job_id,
                request.job_item_id,
                failed,
            )
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                artifact=None,
                error_code="IMAGE_FIDELITY_REJECTED",
                error_message="生成图未通过商品图案、文字或服装保真验收，候选原图已保留。",
                retryable=False,
                raw_usage=raw,
            )
        return replace(result, raw_usage=raw)

    def _account(self, result: Any, plans: list[Any]) -> Any:
        from .xinghe_agent import _planner_call_results

        plans = list(_planner_call_results(plans))

        def total(name: str) -> int | None:
            values = [
                getattr(result, name, None),
                *[getattr(p, name, None) for p in plans],
            ]
            if any(type(value) is not int for value in values):
                return None
            return sum(value for value in values if isinstance(value, int))

        raw = dict(result.raw_usage or {})
        image_requests = raw.get("network_requests", 1)
        raw["network_requests"] = image_requests + len(plans)
        raw["fidelity_planning_requests"] = len(plans)
        raw["metrics"] = {
            **raw.get("metrics", {}),
            "image_fidelity": {
                "version": VERSION,
                "costs_aggregated": True,
                "image_requests": image_requests,
                "planning_requests": len(plans),
            },
        }
        reported = result.normalized_usage.get("reported") is True and all(
            p.normalized_usage.get("reported") is True for p in plans
        )
        raw["_provider_usage_reported"] = reported
        normalized: dict[str, Any] = {"reported": reported}
        for field in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
            values = [
                result.normalized_usage.get(field),
                *[plan.normalized_usage.get(field) for plan in plans],
            ]
            normalized[field] = (
                sum(value for value in values if isinstance(value, int))
                if all(type(value) is int for value in values)
                else None
            )
            raw[field] = normalized[field]
        reserve = getattr(self.wrapped, "review_price_micros", None)
        estimate = result.estimated_cost_micros
        if plans:
            estimate = (
                estimate + len(plans) * reserve
                if type(estimate) is int and type(reserve) is int
                else None
            )
        return replace(
            result,
            raw_usage=raw,
            actual_cost_micros=total("actual_cost_micros"),
            estimated_cost_micros=estimate,
            normalized_usage=normalized,
            api_latency_seconds=result.api_latency_seconds
            + sum(p.api_latency_seconds for p in plans),
        )

    async def _retain(
        self,
        request: Any,
        artifact: Any,
        *,
        role: str = "fidelity_candidate",
        ordinal: int | None = None,
    ) -> str:
        from .fidelity_assets import retain

        return await retain(
            self.sessions,
            self.storage,
            request,
            artifact,
            role=role,
            ordinal=ordinal,
            authority_key=self._parent_key,
        )
