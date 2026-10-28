"""Bind an unchanged artwork to the original file returned by the artflow provider.

This checks local files, not provider authenticity. The invoking agent must supply
the real provider-returned path; the provider's own sidecar receipt is metadata,
not a signature, so this comparison stays local.
"""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image


def fingerprint(path):
    p = Path(path).resolve(strict=True)
    with Image.open(p) as im:
        im.load()
        size = list(im.size)
    return str(p), hashlib.sha256(p.read_bytes()).hexdigest(), size


def build_receipt(native_output, artwork, expected_dimensions):
    native_path, native_sha, native_size = fingerprint(native_output)
    path, output_sha, output_size = fingerprint(artwork)
    if expected_dimensions is not None and native_size != expected_dimensions:
        raise ValueError(f'native tool output is {native_size}; required {expected_dimensions}; do not upscale to pass')
    if native_sha != output_sha or native_size != output_size:
        raise ValueError('artwork must be an unchanged byte copy of the native tool output')
    return {'generator':'artflow','resized':False,'path':path,
            'output_sha256':output_sha,'native_output_path':native_path,
            'native_sha256':native_sha,'native_dimensions':native_size,
            'verification':'local_file_comparison_not_provider_attestation'}


def validate_receipt(rp, artwork, expected_dimensions):
    if not isinstance(rp, dict) or not rp.get('native_output_path'):
        raise ValueError('render receipt requires the original tool-returned native_output_path')
    actual = build_receipt(rp['native_output_path'], artwork, expected_dimensions)
    for key in ('generator','resized','path','output_sha256','native_output_path',
                'native_sha256','native_dimensions','verification'):
        if rp.get(key) != actual[key]:
            raise ValueError('render receipt does not match original file: '+key)
    return actual


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--request', required=True)
    ap.add_argument('--native-output', required=True)
    ap.add_argument('--artwork', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    req = json.loads(Path(a.request).read_text(encoding='utf-8-sig'))
    expected = req['render_contract']['native_generation_size']
    # Detail geometry is validated separately by workflow.record: G01 adopts
    # a nearby native ratio, later groups retain it. Other routes remain exact.
    if req.get('route') == 'DETAIL_PAGE':
        expected = None
    rp = build_receipt(a.native_output, a.artwork, expected)
    output = Path(a.output).resolve()
    if output.exists():
        raise ValueError('receipt already exists; use a new filename per generation')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rp,ensure_ascii=False,indent=2),encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
