"""Explicit local typography overrides; never mutate the approved source plan."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .schema_validation import validate_schema
from .state import HarnessError

VERSION = "native-local-typography-override-v1"
DIMENSIONS = ["identity", "facts", "target_buyer", "campaign_style", "module_sequence"]


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _owned(path: Path, root: Path) -> bytes:
    if (not path.is_absolute() or path.parent.resolve() != root.resolve()
            or any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))):
        raise HarnessError("Local typography authority must belong to its original owned workspace")
    return path.read_bytes()


def _invalid(message: str) -> None:
    raise HarnessError(message)


def _parent(contract: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    parts = path.strip("/").split("/")
    value = contract
    for part in parts[:-1]:
        if not isinstance(value.get(part), dict):
            _invalid("A local contract parent is absent from the effective source")
        value = value[part]
    if parts[-1] not in value:
        _invalid("A local contract leaf is absent from the effective source")
    return value, parts[-1]


def _bindings(copy: Any) -> list[dict[str, str]]:
    return [{"text": row["text"], "bound_to_visual": row["anchor"]} for row in copy]


def _validate_replacement(path: str, old: Any, new: Any, effect: str | None = None) -> None:
    if isinstance(new, str) and not new.strip():
        _invalid("A local contract replacement cannot be blank")
    if path == "/visible_copy":
        rephrase = len(old) == len(new) and all(
            {k: v for k, v in a.items() if k != "text"}
            == {k: v for k, v in b.items() if k != "text"}
            for a, b in zip(old, new, strict=True)
        )
        remaining = iter(old)
        removal = all(any(row == prior for prior in remaining) for row in new)
        if not ((rephrase and effect in {None, "REPHRASE_CONFIRMED"})
                or (removal and effect in {None, "REMOVE"})):
            _invalid("Local copy must rephrase the existing anchored units or remove an exact subset")
    elif path == "/module_spatial_structure_contract/zones":
        if len(old) != len(new) or any(
            {k: v for k, v in a.items() if k not in {"geometry", "visual_weight_percent"}}
            != {k: v for k, v in b.items() if k not in {"geometry", "visual_weight_percent"}}
            for a, b in zip(old, new, strict=True)
        ) or abs(sum(row["visual_weight_percent"] for row in new) - 100) > 0.01:
            _invalid("Local geometry must preserve zone roles, content and count with total weight 100")


def _kind_allows(path: str, kind: str) -> bool:
    if path.startswith("/text_system/"):
        return kind == "TYPOGRAPHY_LAYOUT"
    if path == "/visible_copy":
        return kind == "COPY_CONTENT"
    return kind in {"VISUAL_DETAIL", "OTHER_LOCAL"}


def _apply(plan: dict, patches: list, changes: list, selected: str) -> dict:
    validate_schema(patches, "local-typography-overrides.schema.json", "local contract overrides")
    result = deepcopy(plan)
    paths = []
    for patch in patches:
        index = patch["change_index"]
        copy_change = patch["path"] == "/visible_copy"
        if (patch["module_id"] != selected or index >= len(changes)
                or changes[index].get("contract_effect") != "OVERRIDE_LOCAL_FIELDS"
                or changes[index].get("scope") != "LOCAL"
                or (not copy_change and changes[index].get("claim_effect") != "PRESERVE")
                or (copy_change and changes[index].get("claim_effect") not in {"REMOVE", "REPHRASE_CONFIRMED"})):
            raise HarnessError("A local replacement requires its exact fact-preserving original change")
        parent, field = _parent(result, patch["path"])
        old, new = patch["original_value"], patch["replacement_value"]
        if (_sha(parent[field]) != _sha(old) or _sha(old) == _sha(new) or patch["path"] in paths):
            raise HarnessError("A local replacement is duplicated, unchanged or not based on the original value")
        _validate_replacement(patch["path"], old, new, changes[index]["claim_effect"])
        if copy_change:
            if result.get("copy_intent", {}).get("text_to_visual_bindings") != _bindings(old):
                raise HarnessError("Local copy requires its exact original visual bindings")
            if changes[index]["claim_effect"] == "REPHRASE_CONFIRMED" and not changes[index].get("fact_refs"):
                raise HarnessError("Local copy rephrasing needs original expressible fact references")
            result["copy_intent"]["text_to_visual_bindings"] = _bindings(new)
        paths.append(patch["path"])
        parent[field] = deepcopy(new)
    for index, change in enumerate(changes):
        associated = [p for p in patches if p["change_index"] == index]
        effect = change.get("contract_effect")
        if effect not in {"RESTORE_OR_PRESERVE_APPROVED", "OVERRIDE_LOCAL_FIELDS"} or bool(associated) != (effect == "OVERRIDE_LOCAL_FIELDS"):
            raise HarnessError("Every original change needs an explicit restore or nonempty local override decision")
    return result


def effective_plan(module: dict) -> dict:
    """Replay each bound overlay from the original plan; earlier forks keep their authority."""
    original = module["plan"]
    overlay = module.get("local_contract_override")
    if overlay is None:
        return original
    layers = []
    while overlay is not None:
        if not isinstance(overlay, dict) or len(layers) >= 100:
            raise HarnessError("Invalid or excessive local typography override lineage")
        if (overlay.get("version") != VERSION
                or overlay.get("sha256") != _sha({k: v for k, v in overlay.items() if k != "sha256"})
                or overlay.get("module_id") != module["id"]
                or overlay.get("original_plan_sha256") != _sha(original)):
            raise HarnessError("Original local typography override authority changed")
        layers.append(overlay)
        overlay = overlay.get("previous")
    result = original
    for layer in reversed(layers):
        owner = Path(layer["owner_root"])
        for row in layer["authority_files"]:
            if hashlib.sha256(_owned(Path(row["path"]), owner)).hexdigest() != row["sha256"]:
                raise HarnessError("Original local typography scope evidence changed")
        owner_session = json.loads(_owned(owner / "session.json", owner))
        owner_scope = json.loads(_owned(owner / "local-revision-scope.json", owner))
        if (owner_session.get("approval", {}).get("local_contract_override_sha256") != layer["sha256"]
                or owner_session.get("revision_fork", {}).get("local_contract_override_sha256") != layer["sha256"]
                or _sha(owner_scope["original_observation"]["contract_overrides"]) != _sha(layer["patches"])
                or _sha(owner_scope["original_observation"]["changes"]) != _sha(layer["changes"])
                or owner_scope["context_sha256"] != layer["context_sha256"]):
            raise HarnessError("Local typography metadata differs from its original approval or model observation")
        if _sha(result) != layer["base_effective_plan_sha256"]:
            raise HarnessError("Local typography override no longer has its original effective base")
        result = _apply(result, layer["patches"], layer["changes"], module["id"])
        if _sha(result) != layer["effective_plan_sha256"]:
            raise HarnessError("Effective local typography plan differs from its original scope")
    return result


def effective_module(module: dict) -> dict:
    return {**module, "plan": effective_plan(module)}


def scope_contract_override(root: Path, intent: dict, source: dict, pair: dict) -> dict | None:
    """Consume one existing scope journal; this command never requests a model."""
    scope_path = root / "local-revision-scope.json"
    if not scope_path.exists():
        return None
    try:
        scope_bytes = _owned(scope_path, root)
        scope = json.loads(scope_bytes)
        observation = scope["original_observation"]
        if "contract_overrides" not in observation:
            # Old scope-v1 receipts do not authorize a changed plan field.
            return None
        journal_path = root / "local-scope-model-result.json"
        journal_bytes = _owned(journal_path, root)
        journal = json.loads(journal_bytes)
        binding = journal["binding"]
        semantic = binding["semantic_request"]
        context = semantic["semantics"]
        authority = context["approved_authority"]
        raw = journal["result"]["audit_raw_content"].strip()
        if raw.startswith("```") and raw.endswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if (scope["accepted_local_scope"] is not True or scope["requires_replan"] is not False
                or journal["result"].get("error_code", "MISSING") is not None
                or context["version"] != scope["version"] or observation["version"] != scope["version"]
                or scope["version"] != "domestic-detail-revision-scope-v1"
                or journal["version"] != "domestic-detail-local-scope-result-v1"
                or journal["sha256"] != _sha({k: v for k, v in journal.items() if k != "sha256"})
                or journal["result_sha256"] != _sha(journal["result"])
                or scope["original_journal_file_sha256"] != hashlib.sha256(journal_bytes).hexdigest()
                or scope["binding_sha256"] != _sha(binding)
                or binding["phase"] != "aurora_detail_revision_scope"
                or semantic["profile"] != binding["phase"]
                or binding["semantic_sha256"] != _sha(semantic)
                or _sha(json.loads(raw)) != _sha(observation)
                or _sha(journal["result"]["payload"]) != _sha(observation)
                or context["sha256"] != _sha({k: v for k, v in context.items() if k != "sha256"})
                or scope["context_sha256"] != context["sha256"]
                or observation["context_sha256"] != context["sha256"]
                or observation["intent_sha256"] != intent["sha256"]
                or context["intent_sha256"] != intent["sha256"]
                or _sha(context["adjustment"]) != _sha(intent["adjustment"])
                or _sha(context["approved_plan"]) != _sha(source["plan"])
                or authority["sha256"] != _sha({k: v for k, v in authority.items() if k != "sha256"})
                or authority["compiled_input_sha256"] != _sha(source["input"])
                or authority["compiled_identity_sha256"] != _sha(source["identity"])
                or authority["compiled_plan_sha256"] != _sha(source["plan"])
                or observation["decision"] != "LOCAL"
                or [r["dimension"] for r in observation["dimensions"]] != DIMENSIONS
                or any(r["state"] != "UNCHANGED" for r in observation["dimensions"])
                or observation["other_screens"]["state"] != "PRESERVED"):
            raise HarnessError("Local typography replacements lack their original source-bound semantic authority")
        selected = intent["adjustment"]["module_id"]
        if (context["selected_module_id"] != selected or observation["selected_module_id"] != selected
                or context["pair_id"] != pair["pair_id"] or observation["pair_id"] != pair["pair_id"]
                or context["module_ids"] != pair["module_ids"]
                or observation["adjustment_sha256"] != _sha(intent["adjustment"])):
            raise HarnessError("Local typography scope targets another original module or pair")
        changes = observation["changes"]
        original_changes = intent["adjustment"]["change_only"]
        if (len(changes) != len(original_changes)
                or any(type(r["index"]) is not int or r["index"] != i or r["original_change"] != original_changes[i] or r["scope"] != "LOCAL"
                       for i, r in enumerate(changes))):
            raise HarnessError("Local typography scope rewrote or omitted an original human change")
        by_id = {row["id"]: row for row in source["modules"]}
        expected = [{"module_id": key, "contract": effective_plan(by_id[key]),
                     "contract_sha256": _sha(effective_plan(by_id[key]))} for key in pair["module_ids"]]
        if _sha(context["effective_module_contracts"]) != _sha(expected):
            raise HarnessError("Local typography scope differs from the accepted effective source contracts")
        prior = by_id[selected]
        base = effective_plan(prior)
        patches = observation["contract_overrides"]
        for patch in patches:
            if not _kind_allows(patch["path"], intent["adjustment"]["change_kind"]):
                raise HarnessError("This local contract kind does not authorize the requested field")
            if patch["path"] == "/visible_copy":
                change = changes[patch["change_index"]]
                refs = change.get("fact_refs", [])
                allowed = {r["fact"]["id"]: r["fact"]["text"] for r in authority.get("original_fact_ledger", [])
                           if r["approved_disposition"] in {"LEAD", "SUPPORT"}}
                if len({r["id"] for r in refs}) != len(refs) or any(allowed.get(ref["id"]) != ref["text"] for ref in refs):
                    raise HarnessError("Local copy references changed or unapproved original facts")
        effective = _apply(base, patches, changes, selected)
        paths = [scope_path, journal_path, root / "source-session.json", root / "local-revision-intent.json"]
        value = {"version": VERSION, "module_id": selected, "owner_root": str(root),
                 "intent_sha256": intent["sha256"], "context_sha256": context["sha256"],
                 "original_plan_sha256": _sha(prior["plan"]), "base_effective_plan_sha256": _sha(base),
                 "effective_plan_sha256": _sha(effective), "patches": patches, "changes": changes,
                 "previous": prior.get("local_contract_override"),
                 "authority_files": [{"path": str(p), "sha256": hashlib.sha256(_owned(p, root)).hexdigest()} for p in paths]}
        return {**value, "sha256": _sha(value)}
    except (OSError, KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError("Original local typography contract evidence is missing, changed or unreadable") from exc


def validate_request_contracts(session: dict, lineage: dict, modules: list[dict]) -> None:
    """The audited effective layout must be the one in the original signed render."""
    if not session.get("revision_fork") and not any(m.get("local_contract_override") is not None for m in modules):
        return
    try:
        entries = [row for row in session["generation_requests"] if row["token"] == lineage["token"]]
        if len(entries) != 1:
            raise HarnessError("One original signed request is required for the effective layout")
        raw = Path(entries[0]["request"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != lineage["signed_request_sha256"]:
            raise HarnessError("Effective layout signed request bytes changed")
        expected = ([{"module_id": item["id"], "effective_plan_sha256": _sha(effective_plan(item)),
                      "override_sha256": (item.get("local_contract_override") or {}).get("sha256")} for item in modules]
                    if any(item.get("local_contract_override") is not None for item in modules) else None)
        if _sha(json.loads(raw).get("local_contract_bindings")) != _sha(expected):
            raise HarnessError("Audited local layout differs from the original signed render contract")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError("Original signed local layout contract is missing or changed") from exc
