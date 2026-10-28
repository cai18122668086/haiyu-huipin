"""Reference analysis scaffolding and physical panels, never generation references."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops
from route_contracts import MECHANISMS


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def analysis_template(d, output):
    """Empty observations deliberately fail validation until visually completed."""
    output = Path(output)
    if output.exists():
        raise ValueError('analysis already exists; edit it in place, do not overwrite observations')
    refs = [x for x in d['inputs'] if x['role'] == 'benchmark_reference']
    panels = []
    for i, ref in enumerate(refs, 1):
        with Image.open(ref['path']) as im:
            width, height = im.size
        panels.append({'panel_id': f'B{i:02}', 'panel_order': i,
                       'reference_input_id': ref['id'], 'bbox': [0, 0, width, height]})
    data = {
        'schema_version': 'aurora-reference-analysis-4',
        'overview_detection': {'is_overview': None, 'panel_count': len(panels),
                               'reading_order': '', 'detection_notes': '', 'panels': panels},
        'units': [],
        'mechanisms': {k: {'observation': '', 'source_unit_ids': []} for k in MECHANISMS},
        'source_semantics': [],
        'narrative_logic': {'opening': '', 'proof_progression': '', 'takeaway': '',
                            'handoff': '', 'sequence': []},
        'source_content_analysis': {'set_summary': '', 'rhythm_summary': '', 'sequence': [], 'transitions': []},
    }
    write(output, data)
    return str(output.resolve())


def panel_metadata(d, a):
    overview = a.get('overview_detection')
    if not isinstance(overview, dict) or type(overview.get('is_overview')) is not bool:
        raise ValueError('overview_detection.is_overview must be decided by image inspection')
    panels = overview.get('panels')
    count = overview.get('panel_count')
    if type(count) is not int or count < 1 or not isinstance(panels, list) or len(panels) != count:
        raise ValueError('overview_detection needs panel_count and one row per physical panel')
    for field in ('reading_order', 'detection_notes'):
        if not isinstance(overview.get(field), str) or not overview[field].strip():
            raise ValueError('overview_detection requires ' + field)
    refs = {x['id']: x for x in d['inputs'] if x['role'] == 'benchmark_reference'}
    ids, boxes, used_refs = set(), [], []
    for order, panel in enumerate(panels, 1):
        pid = panel.get('panel_id')
        if not isinstance(pid, str) or not pid or pid in ids or panel.get('panel_order') != order:
            raise ValueError('panels need unique panel_id and stored order 1..panel_count')
        ids.add(pid)
        rid = panel.get('reference_input_id')
        if rid not in refs:
            raise ValueError(f'panel {pid} must cite a confirmed benchmark reference_input_id')
        ref = refs[rid]
        if not Path(ref['path']).is_file() or file_sha(ref['path']) != ref['sha256']:
            raise ValueError(f'benchmark {rid} changed since input registration')
        box = panel.get('bbox')
        with Image.open(ref['path']) as im:
            w, h = im.size
        if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                or not 0 <= box[0] < box[2] <= w or not 0 <= box[1] < box[3] <= h):
            raise ValueError(f'panel {pid}.bbox must be in-bounds integer [left, top, right, bottom]')
        for other_rid, previous in boxes:
            if rid != other_rid:
                continue
            overlap = max(0, min(box[2], previous[2]) - max(box[0], previous[0])) * max(0, min(box[3], previous[3]) - max(box[1], previous[1]))
            area = min((box[2]-box[0])*(box[3]-box[1]), (previous[2]-previous[0])*(previous[3]-previous[1]))
            if overlap > area * 0.1:
                raise ValueError('physical panels cannot reuse/overlap the same reference region; split full screens, not object crops')
        boxes.append((rid, box))
        used_refs.append(rid)
    if set(used_refs) != set(refs):
        raise ValueError('panel decomposition must cover every benchmark reference for every route')
    if overview['is_overview'] != any(n > 1 for n in Counter(used_refs).values()):
        raise ValueError('is_overview must match per-image panel counts (multiple separate images alone are not an overview)')
    return panels, refs


def crop_panels(d, analysis_path, ws):
    a = read(analysis_path)
    panels, refs = panel_metadata(d, a)
    output = Path(ws) / 'artifacts' / 'analysis-panels'
    output.mkdir(parents=True, exist_ok=True)
    for panel in panels:
        path = output / f"panel-{panel['panel_order']:03}.png"
        with Image.open(refs[panel['reference_input_id']]['path']) as im:
            im.convert('RGB').crop(tuple(panel['bbox'])).save(path)
        panel.update(source_crop_path=str(path.resolve()), sha256=file_sha(path), analysis_only=True)
    content = a.setdefault('source_content_analysis', {'set_summary': '', 'sequence': []})
    if not content.get('sequence'):
        content['sequence'] = [{'id': f'T{i:02}', 'source_order': i,
                                'source_reference_ids': [p['reference_input_id']], 'source_panel_ids': [p['panel_id']],
                                'content_role': '', 'verbatim_text': [], 'text_status': None,
                                'normalized_message': '', 'handoff': '',
                                # These fields force a real decomposition of persuasion,
                                # rather than a list of visual subjects or product facts.
                                'opening_function': '',
                                'conversion_stage': '', 'buyer_question': '',
                                'belief_shift': '', 'persuasion_job': '',
                                'evidence_obligations': [], 'expression_actions': [],
                                'visual_density': {'text_blocks': None, 'card_count': None, 'text_chars': None,
                                                   'main_visual_share': None, 'whitespace_share': None, 'notes': ''}}
                               for i, p in enumerate(panels, 1)]
        content['transitions'] = [{'id': f'E{i:02}', 'from_step_id': f'T{i:02}', 'to_step_id': f'T{i+1:02}',
                                   'kind': None, 'observation': '', 'carryover': '',
                                   'semantic_handoff': '', 'next_buyer_question': ''} for i in range(1,len(panels))]
    write(analysis_path, a)
    write(output / 'manifest.json', {'analysis_only': True, 'panels': deepcopy(panels)})
    return a


def validate_panels(d, a):
    panels, refs = panel_metadata(d, a)
    paths = set()
    for panel in panels:
        path = Path(panel.get('source_crop_path', '')).resolve()
        if not path.is_file() or str(path) in paths or panel.get('analysis_only') is not True:
            raise ValueError('each panel needs a distinct physical analysis_only crop; run crop-reference-panels')
        paths.add(str(path))
        if file_sha(path) != panel.get('sha256'):
            raise ValueError('panel crop SHA-256 mismatch; recrop and review the actual panel')
        with Image.open(refs[panel['reference_input_id']]['path']) as src, Image.open(path) as actual:
            expected = src.convert('RGB').crop(tuple(panel['bbox']))
            if actual.size != expected.size or ImageChops.difference(expected, actual.convert('RGB')).getbbox():
                raise ValueError('panel pixels must match their declared reference bbox; run crop-reference-panels')
    return panels


def validate_mechanisms(mechanisms, unit_ids):
    if not isinstance(mechanisms, dict):
        raise ValueError('mechanisms must contain exactly: ' + ', '.join(MECHANISMS))
    missing = [k for k in MECHANISMS if k not in mechanisms]
    unknown = sorted(set(mechanisms) - set(MECHANISMS))
    invalid = [k for k in MECHANISMS if k in mechanisms and
               (not isinstance(mechanisms[k], dict) or not isinstance(mechanisms[k].get('observation'), str)
                or not mechanisms[k]['observation'].strip())]
    bad_cites = [k for k in MECHANISMS if isinstance(mechanisms.get(k), dict) and
                 (not isinstance(mechanisms[k].get('source_unit_ids'), list) or not mechanisms[k]['source_unit_ids']
                  or not set(mechanisms[k]['source_unit_ids']).issubset(unit_ids))]
    if missing or unknown or invalid or bad_cites:
        raise ValueError(f'Passport mechanisms: missing={missing}; unknown={unknown}; empty_observation={invalid}; '
                         f'invalid_source_unit_ids={bad_cites}. Use init-reference-analysis template; '
                         'fill real observations and registered sanitized crop IDs, then retry compile-visual-passport.')


def validate_content_panels(content, panels):
    by_id = {p['panel_id']: p for p in panels}
    covered, last_order = set(), 0
    seen_steps = set()
    for step in content['sequence']:
        if step.get('id') in seen_steps:
            raise ValueError('content sequence step IDs must be unique')
        seen_steps.add(step.get('id'))
        ids = step.get('source_panel_ids')
        if not isinstance(ids, list) or len(ids) != 1 or not set(ids).issubset(by_id):
            raise ValueError('each source content step needs exactly one source_panel_ids entry; analyze every physical panel individually')
        orders = [by_id[pid]['panel_order'] for pid in ids]
        if orders != sorted(orders) or min(orders) < last_order:
            raise ValueError('source content must follow physical panel reading order')
        last_order = max(orders)
        if set(step['source_reference_ids']) != {by_id[pid]['reference_input_id'] for pid in ids}:
            raise ValueError('source content panel/reference bindings disagree')
        if step.get('text_status') not in ('transcribed', 'no_visible_text', 'partially_unreadable'):
            raise ValueError('content text_status must record transcribed/no_visible_text/partially_unreadable')
        if step['text_status'] == 'transcribed' and not step['verbatim_text']:
            raise ValueError('transcribed panels require actual verbatim_text')
        if step['text_status'] == 'no_visible_text' and step['verbatim_text']:
            raise ValueError('no_visible_text conflicts with verbatim_text')
        if step['text_status'] == 'partially_unreadable' and not step.get('unreadable_regions'):
            raise ValueError('partially unreadable text needs explicit unreadable_regions; never invent OCR')
        covered.update(ids)
    if covered != set(by_id):
        raise ValueError('source content analysis must cover every physical panel; missing=' + ','.join(sorted(set(by_id)-covered)))
    from reference_rhythm import validate_reference_rhythm
    validate_reference_rhythm(content)
