#!/usr/bin/env python3
"""OpenAI-compatible visual extraction and generated-output comparison."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from identity_framework_v0_1_2 import build_identity_contract, load, write


def data_url(path: str | Path) -> str:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def extract_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks)


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def request_json(
    base_url: str,
    model: str,
    api_key: str,
    prompt: str,
    images: list[str | Path],
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    content.extend({"type": "input_image", "image_url": data_url(x)} for x in images)
    payload = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": 2600,
    }
    endpoint = base_url.rstrip("/") + "/responses"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return parse_json(extract_text(raw)), {
        "response_id": raw.get("id"),
        "usage": raw.get("usage"),
        "model": model,
        "endpoint": endpoint,
    }


IDENTITY_PROMPT = """
You are a product identity evidence extractor. The images are multiple views of
one current product. Observe only visible facts and return one compact JSON
object. Do not beautify, infer hidden parts, import another model, or use prior
generated images. Resolve image order as reference_index starting at 1.

Required shape:
{
  "references":[{"reference_index":1,"view_tags":["FRONT","THREE_QUARTER","SIDE","REAR","DETAIL","RECLINED"]}],
  "identity":{
    "category_and_quantity":{"category":"visible category","quantity":1},
    "silhouette_and_proportions":{"overall":"...","part_ratios":["..."]},
    "color_partitions":[{"region":"...","color":"...","evidence_indices":[1]}],
    "materials_and_surfaces":[{"region":"...","appearance":"...","evidence_indices":[1]}],
    "components":[{"name":"...","shape":"...","color":"...","connection":"...","evidence_indices":[1]}],
    "relative_geometry":["..."],
    "logos_and_labels":[{"text_or_mark":"...","position":"...","evidence_indices":[1]}],
    "accessories_and_interfaces":[{"name":"...","position":"...","evidence_indices":[1]}]
  },
  "must_preserve":["specific current-product invariant"],
  "must_not_change":["specific current-product prohibition"],
  "view_specific_evidence":{
    "FRONT":["ref-01"],
    "THREE_QUARTER":["ref-01"],
    "SIDE_OR_RECLINE":["ref-02"],
    "REAR":["ref-03"],
    "DETAIL":["ref-04"]
  },
  "evidence_conflicts":[],
  "coverage":{"semantic_visual_review":true}
}
Use empty arrays for genuinely absent logos, accessories, or interfaces.
If the images disagree about product color/model, record a precise conflict.
Return JSON only.
""".strip()


AUDIT_PROMPT = """
You are a strict generated-commerce-image product identity auditor.
Images 1..N are original same-product evidence; the LAST image is generated.
Compare the generated product against the supplied product-identity-contract,
the expected page role, and allowed text facts. Do not reward aesthetics when
identity changes. A changed main color, silhouette, component, relative
geometry, or wrong view evidence is FAIL.

Return JSON only:
{
  "checks":{
    "product_identity_match":"PASS|FAIL",
    "color_partition_match":"PASS|FAIL",
    "silhouette_match":"PASS|FAIL",
    "component_match":"PASS|FAIL",
    "relative_geometry_match":"PASS|FAIL",
    "material_match":"PASS|FAIL",
    "view_evidence_match":"PASS|FAIL",
    "text_fact_match":"PASS|FAIL",
    "compliance_match":"PASS|FAIL",
    "page_role_match":"PASS|FAIL",
    "campaign_style_match":"PASS|FAIL"
  },
  "errors":["allowed fixed error code only"],
  "evidence_notes":["short source-bound observation"]
}
Allowed errors:
PRODUCT_COLOR_DRIFT, PRODUCT_STRUCTURE_DRIFT, COMPONENT_MISSING,
COMPONENT_INVENTED, RELATIVE_GEOMETRY_DRIFT, WRONG_VIEW_EVIDENCE,
TEXT_HALLUCINATION, OFFER_HALLUCINATION, PAGE_ROLE_MISSING,
CAMPAIGN_STYLE_INCONSISTENT.
Return conservative FAIL when visible evidence does not support identity.
""".strip()


def extract_mode(args: argparse.Namespace, key: str) -> int:
    raw = load(args.raw_input)
    paths = raw["product"]["same_product_reference_images"]
    prompt = (
        IDENTITY_PROMPT
        + "\nThe user-verified current-product facts below are claims to check "
        "against the images. Preserve supported facts; put every visible "
        "contradiction in evidence_conflicts. verified_facts="
        + json.dumps(
            {
                "name": raw["product"]["name"],
                "category": raw["product"]["category"],
                "verified_attributes": raw["product"]["verified_attributes"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    observation, meta = request_json(
        args.base_url, args.model, key, prompt, paths, args.timeout
    )
    for item in observation.get("references", []):
        index = int(item.pop("reference_index"))
        item["path"] = str(Path(paths[index - 1]).resolve())
    contract = build_identity_contract(raw, observation)
    write(args.observations_out, {"model_receipt": meta, **observation})
    write(args.out, contract)
    print(
        json.dumps(
            {
                "status": contract["status"],
                "contract": str(args.out),
                "observations": str(args.observations_out),
                "response_id": meta["response_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if contract["status"] == "READY" else 3


def audit_mode(args: argparse.Namespace, key: str) -> int:
    contract = load(args.identity_contract)
    compiled = load(args.compiled_output)
    page = compiled["pages"][args.page_index - 1]
    allowed = {
        "product_name": page["factual_content"]["product_name"]["text"],
        "primary_claim": page["factual_content"]["primary_claim"]["text"],
        "offer_facts": [x["text"] for x in page["factual_content"]["offer_facts"]],
    }
    context = (
        AUDIT_PROMPT
        + "\nproduct_identity_contract="
        + json.dumps(contract, ensure_ascii=False, sort_keys=True)
        + "\nexpected_page_role="
        + page["page_role"]
        + "\nallowed_text_facts="
        + json.dumps(allowed, ensure_ascii=False, sort_keys=True)
    )
    refs = [x["path"] for x in contract["reference_evidence"]]
    judgment, meta = request_json(
        args.base_url,
        args.model,
        key,
        context,
        refs + [args.generated_image],
        args.timeout,
    )
    write(
        args.out,
        {
            "schema_version": "generated-page-visual-judgment-0.1.2",
            "page_index": args.page_index,
            "generated_image": str(Path(args.generated_image).resolve()),
            "judgment": judgment,
            "model_receipt": meta,
        },
    )
    print(
        json.dumps(
            {
                "status": "JUDGMENT_COMPLETE",
                "page_index": args.page_index,
                "response_id": meta["response_id"],
                "output": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["extract", "audit"], required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="NEWAPI_API_KEY")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--raw-input")
    parser.add_argument("--observations-out")
    parser.add_argument("--identity-contract")
    parser.add_argument("--compiled-output")
    parser.add_argument("--page-index", type=int)
    parser.add_argument("--generated-image")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    key = os.environ.get(args.api_key_env)
    if not key:
        raise SystemExit(f"missing environment variable {args.api_key_env}")
    if args.mode == "extract":
        if not args.raw_input or not args.observations_out:
            raise SystemExit("extract requires --raw-input and --observations-out")
        return extract_mode(args, key)
    required = (
        args.identity_contract,
        args.compiled_output,
        args.page_index,
        args.generated_image,
    )
    if not all(required):
        raise SystemExit("audit requires identity-contract, compiled-output, page-index and generated-image")
    return audit_mode(args, key)


if __name__ == "__main__":
    raise SystemExit(main())
