from __future__ import annotations

import asyncio
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import default_skill_root
from app.model_pose_transfer_provider import ModelPoseTransferProvider
from app.new_detail_skill_provider import BundledSkillManifestValidator
from app.new_image_skill_provider import SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION
from app.provider import (
    ProviderArtifact,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from app.schemas_v1 import ModelPoseTransferConfiguration, ModelPoseTransferInput
from app.xinghe_single_image_provider import SINGLE_IMAGE_ADAPTER_CONFIGURATION_KEY


@dataclass
class RecordingDelegate:
    name: str = "recording-model-pose"
    model: str = "recording-model"
    pricing_version: str = "test-v1"
    requests: list[ProviderRequest] = field(default_factory=list)

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        payload = b"model-pose-result"
        return ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id="pose-request-1",
            usage_reported=True,
            raw_usage={"output_images": 1, "network_requests": 1},
            artifact=ProviderArtifact(
                payload=payload,
                filename="model-pose-result.png",
                content_type="image/png",
                width=800,
                height=1200,
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
            provider_name=self.name,
            model_name=self.model,
            pricing_version=self.pricing_version,
        )


def _request(
    *,
    operation: str = "model_pose_transfer",
    input_config: dict[str, Any] | None = None,
) -> ProviderRequest:
    return ProviderRequest(
        workspace_id="ws-test",
        job_id="job-test",
        job_item_id="item-test",
        attempt_id="attempt-test",
        operation=operation,
        idempotency_key="idem-test",
        configuration={
            "job": {
                "input": input_config
                or {
                    "model_source_asset_id": "asset-model",
                    "pose_reference_asset_id": "asset-pose",
                    "pose_slug": "female-035",
                    "pose_reference_sha256": "b" * 64,
                },
                "settings": {
                    "aspect_ratio": "follow-source",
                    "image_count": 1,
                    "quality": "high",
                    "output_format": "png",
                    "instruction": "Keep the clothing unchanged.",
                },
            },
            "item": {"item_key": "image-001", "ordinal": 0},
        },
    )


def test_model_pose_schema_uses_explicit_job_hash_field_and_forbids_old_name() -> None:
    validated = ModelPoseTransferInput.model_validate(
        {
            "model_source_asset_id": "asset-model",
            "pose_reference_asset_id": "asset-pose",
            "pose_slug": "female-035",
            "pose_reference_sha256": "a" * 64,
        }
    )
    assert validated.pose_reference_sha256 == "a" * 64

    with pytest.raises(ValidationError):
        ModelPoseTransferInput.model_validate(
            {
                "model_source_asset_id": "asset-model",
                "pose_reference_asset_id": "asset-pose",
                "pose_slug": "female-035",
                "reference_sha256": "a" * 64,
            }
        )
    with pytest.raises(ValidationError):
        ModelPoseTransferInput.model_validate(
            {
                "model_source_asset_id": "asset-model",
                "pose_reference_asset_id": "asset-pose",
                "pose_slug": "female-035",
            }
        )
    with pytest.raises(ValidationError):
        ModelPoseTransferInput.model_validate(
            {
                "model_source_asset_id": "asset-same",
                "pose_reference_asset_id": "asset-same",
            }
        )


def test_model_pose_configuration_is_one_image_and_follow_source_only() -> None:
    assert ModelPoseTransferConfiguration().model_dump() == {
        "aspect_ratio": "follow-source",
        "image_count": 1,
        "quality": "high",
        "output_format": "png",
        "instruction": "",
    }
    with pytest.raises(ValidationError):
        ModelPoseTransferConfiguration.model_validate(
            {"aspect_ratio": "follow-reference"}
        )
    with pytest.raises(ValidationError):
        ModelPoseTransferConfiguration.model_validate({"image_count": 2})


def test_provider_envelope_keeps_role_order_and_explicit_catalog_hash() -> None:
    delegate = RecordingDelegate()
    provider = ModelPoseTransferProvider(
        delegate,
        manifest_validator=lambda skill_id: (
            "a" * 64 if skill_id == ModelPoseTransferProvider.skill_id else ""
        ),
    )

    result = asyncio.run(provider.execute(_request()))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert len(delegate.requests) == 1
    envelope = delegate.requests[0].configuration[
        SINGLE_IMAGE_ADAPTER_CONFIGURATION_KEY
    ]
    assert envelope["schema_version"] == SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION
    assert envelope["operation"] == "model_pose_transfer"
    assert envelope["source_assets"] == [
        {"asset_id": "asset-model", "role": "model_source"},
        {"asset_id": "asset-pose", "role": "pose_reference"},
    ]
    assert envelope["target"] == {
        "aspect_ratio": "follow-source",
        "width": None,
        "height": None,
        "quality": "high",
        "output_format": "png",
        "fit": "contain",
    }
    assert envelope["prompt_input"] == {
        "user_instruction": "Keep the clothing unchanged.",
        "pose_slug": "female-035",
        "pose_reference_sha256": "b" * 64,
    }
    assert "reference_sha256" not in envelope["prompt_input"]


def test_custom_pose_omits_catalog_identity_from_provider_envelope() -> None:
    delegate = RecordingDelegate()
    provider = ModelPoseTransferProvider(
        delegate,
        manifest_validator=lambda _skill_id: "a" * 64,
    )
    request = _request(
        input_config={
            "model_source_asset_id": "asset-model",
            "pose_reference_asset_id": "asset-custom-pose",
            "pose_slug": None,
            "pose_reference_sha256": None,
        }
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    envelope = delegate.requests[0].configuration[
        SINGLE_IMAGE_ADAPTER_CONFIGURATION_KEY
    ]
    assert set(envelope["prompt_input"]) == {"user_instruction"}


def test_old_generic_hash_field_fails_before_delegate() -> None:
    delegate = RecordingDelegate()
    provider = ModelPoseTransferProvider(
        delegate,
        manifest_validator=lambda _skill_id: "a" * 64,
    )
    result = asyncio.run(
        provider.execute(
            _request(
                input_config={
                    "model_source_asset_id": "asset-model",
                    "pose_reference_asset_id": "asset-pose",
                    "pose_slug": "female-035",
                    "reference_sha256": "b" * 64,
                }
            )
        )
    )

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "MODEL_POSE_INPUT_FIELD_NOT_ALLOWED"
    assert delegate.requests == []


def test_manifest_drift_fails_closed_before_delegate(
    tmp_path: Path,
) -> None:
    drifted_root = tmp_path / ModelPoseTransferProvider.skill_id
    shutil.copytree(
        default_skill_root(ModelPoseTransferProvider.skill_id),
        drifted_root,
    )
    with (drifted_root / "SKILL.md").open("a", encoding="utf-8") as handle:
        handle.write("\nunauthorized drift\n")
    validator = BundledSkillManifestValidator(
        source_roots={ModelPoseTransferProvider.skill_id: drifted_root}
    )
    delegate = RecordingDelegate()
    provider = ModelPoseTransferProvider(
        delegate,
        manifest_validator=validator.validate,
    )

    result = asyncio.run(provider.execute(_request()))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "SKILL_CONTENT_HASH_MISMATCH"
    assert delegate.requests == []


def test_wrong_operation_never_calls_delegate() -> None:
    delegate = RecordingDelegate()
    provider = ModelPoseTransferProvider(
        delegate,
        manifest_validator=lambda _skill_id: "a" * 64,
    )

    result = asyncio.run(provider.execute(_request(operation="creative_image")))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "OPERATION_NOT_SUPPORTED"
    assert delegate.requests == []
