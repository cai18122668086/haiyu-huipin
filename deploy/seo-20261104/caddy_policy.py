"""Restrict the existing local Worker bridge allowance to its real admin page."""
import re

BRIDGE_SOURCES = (
    "http://localhost:8765/v1/enrollment",
    "http://127.0.0.1:8765/v1/enrollment",
)


def scope_worker_bridge(source: str) -> str:
    if "@seo_worker_bridge" in source:
        raise ValueError("SEO CSP policy is already installed; review before modifying")
    matches = list(re.finditer(r'^(\s*)Content-Security-Policy "([^"]+)"\s*$', source, re.M))
    if len(matches) != 1:
        raise ValueError("Expected exactly one shared CSP declaration")
    match = matches[0]
    old = match.group(2)
    if not all(item in old.split() or item + ";" in old.split() for item in BRIDGE_SOURCES):
        raise ValueError("Existing Worker bridge policy differs from the reviewed baseline")
    public = old
    for item in BRIDGE_SOURCES:
        public = public.replace(" " + item, "")
    without = source[:match.start()] + source[match.end():]
    marker = re.search(r"^(\s*)header \{", without, re.M)
    if not marker:
        raise ValueError("Shared security header block not found")
    indent = marker.group(1)
    scoped = (
        indent + "@seo_public_csp not path /admin/accounts /admin/accounts/\n"
        + indent + 'header @seo_public_csp Content-Security-Policy "' + public + '"\n'
        + indent + "@seo_worker_bridge path /admin/accounts /admin/accounts/\n"
        + indent + 'header @seo_worker_bridge Content-Security-Policy "' + old + '"\n'
    )
    return without[:marker.start()] + scoped + without[marker.start():]
