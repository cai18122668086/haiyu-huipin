"""Check actual selected account configuration before a new scope model call."""

from __future__ import annotations

from typing import Any, NoReturn
from urllib.parse import urljoin, urlsplit

from .planner_dispatch import route_descriptor, select_route
from .plans import canonical_hash
from .revision_authorization import image_dispatch
from .revision_protocol import RevisionScopeError


def _reject() -> NoReturn:
    # Never interpolate provider exceptions, URLs or credentials into diagnostics.
    raise RevisionScopeError(
        "NATIVE_DETAIL_REVISION_ACCOUNTS_UNCONFIGURED",
        "The selected image and review accounts need valid current configuration",
    ) from None


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _reject()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _reject()
    return value


def _endpoint(value: object) -> str:
    endpoint = _text(value)
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in endpoint)
    ):
        _reject()
    # Access also rejects an invalid/out-of-range port.
    _ = parsed.port
    return endpoint


def configured_revision_accounts(gateway: Any) -> dict[str, Any]:
    """Configuration proof only: no remote authorization/balance/availability claim."""
    try:
        review = select_route(gateway)
        image = image_dispatch(gateway)
        if image["kind"] != review["kind"]:
            _reject()
        if review["kind"] == "http":
            review_selector = getattr(gateway, "_ordered_routes", None)
            image_selector = getattr(gateway, "_image_routes", None)
            if not callable(review_selector) or not callable(image_selector):
                _reject()
            review_routes, image_routes = review_selector(), image_selector()
            if not review_routes or len(image_routes) != 1:
                _reject()
            actual_review, actual_image = review_routes[0], image_routes[0]
            projected_review = route_descriptor(
                provider=_text(gateway.provider_name),
                route=_text(actual_review.name),
                model=_text(actual_review.planner_model),
                endpoint=urljoin(
                    _endpoint(actual_review.planner_base_url), "v1/chat/completions"
                ),
                credential=_text(actual_review.planner_api_key),
            )
            projected_image = route_descriptor(
                provider=_text(gateway.provider_name),
                route=_text(actual_image.name),
                model=_text(actual_image.image_model),
                endpoint=_endpoint(actual_image.image_base_url),
                credential=_text(actual_image.image_api_key),
            )
            if canonical_hash(projected_review) != canonical_hash(
                review
            ) or canonical_hash(projected_image) != canonical_hash(image):
                _reject()
        value = {
            "version": "domestic-detail-revision-account-configuration-v1",
            "kind": review["kind"],
            "configuration_checked": True,
            "remote_authorization_verified": False,
            "remote_balance_verified": False,
            "image_dispatch_sha256": image["sha256"],
            "review_dispatch_sha256": review["sha256"],
        }
        return {**value, "sha256": canonical_hash(value)}
    except RevisionScopeError:
        raise
    except (ValueError, TypeError, AttributeError, IndexError, KeyError):
        _reject()
