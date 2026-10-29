"""Per-panel density and adjacent-boundary evidence; never infer from a set average."""
from copy import deepcopy
import math

KINDS = ('continuous_background', 'whitespace_break', 'hard_cut', 'overlap',
         'color_transition', 'independent_card', 'other')
COUNTS = ('text_blocks', 'card_count', 'text_chars')
SHARES = ('main_visual_share', 'whitespace_share')


def _require(ok, message):
    if not ok:
        raise ValueError('reference rhythm: ' + message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def char_count(blocks):
    return sum(len(''.join(text.split())) for text in blocks)


def validate_density(value):
    _require(isinstance(value, dict), 'each physical panel needs visual_density')
    for key in COUNTS:
        _require(type(value.get(key)) is int and value[key] >= 0, 'visual_density.' + key + ' must be a nonnegative observed count')
    for key in SHARES:
        _require(type(value.get(key)) in (int, float) and 0 <= value[key] <= 1, 'visual_density.' + key + ' must be an observed fraction')
    _require(_text(value.get('notes')), 'visual_density needs notes describing the actual panel, not the whole overview')
    return value


def validate_reference_rhythm(content):
    steps = content['sequence']
    seen = set()
    for step in steps:
        panels = step['source_panel_ids']
        _require(content.get('fixture_compatibility') is True or not seen.intersection(panels), 'one content/density record per physical panel; split the overview instead of reusing a full overview as multiple screens')
        seen.update(panels)
        density = validate_density(step.get('visual_density'))
        if step.get('text_status') == 'transcribed':
            _require(density['text_chars'] == char_count(step['verbatim_text']), 'text_chars must count transcribed non-whitespace characters')
        elif step.get('text_status') == 'no_visible_text':
            _require(density['text_chars'] == density['text_blocks'] == 0, 'text-free panel has zero text budget')
    transitions = content.get('transitions')
    _require(isinstance(transitions, list) and len(transitions) == len(steps)-1,
             'transitions must cover every adjacent physical panel (empty only for one panel)')
    ids = set()
    for left, right, edge in zip(steps, steps[1:], transitions):
        _require(_text(edge.get('id')) and edge['id'] not in ids, 'transition IDs must be unique')
        ids.add(edge['id'])
        _require((edge.get('from_step_id'), edge.get('to_step_id')) == (left['id'], right['id']), 'transition order must match the adjacent source panels')
        _require(edge.get('kind') in KINDS and _text(edge.get('observation')) and _text(edge.get('carryover')), 'transition requires kind, boundary observation and carryover (including explicitly none)')
        semantic_method_present = any(edge.get('semantic_handoff') or edge.get('next_buyer_question') for edge in transitions)
        if content.get('fixture_compatibility') is not True and semantic_method_present:
            _require(_text(edge.get('semantic_handoff')) and _text(edge.get('next_buyer_question')),
                     'transition requires semantic_handoff and next_buyer_question; visual continuity alone is not a narrative handoff')
    _require(_text(content.get('rhythm_summary')), 'rhythm_summary must describe the actual sparse/dense and boundary sequence')
    return {'panels': deepcopy(steps), 'transitions': deepcopy(transitions), 'rhythm_summary': content['rhythm_summary']}


def fixture_defaults(content):
    """Populate legacy synthetic fixtures only; real/manual analysis never uses this path."""
    content['fixture_compatibility'] = True
    steps = content.get('sequence', [])
    for step in steps:
        blocks = step.get('verbatim_text') or []
        step.setdefault('visual_density', {'text_blocks': len(blocks), 'card_count': 0,
                                           'text_chars': char_count(blocks),
                                           'main_visual_share': .7, 'whitespace_share': .15,
                                           'notes': 'synthetic fixture counts'})
    content.setdefault('rhythm_summary', 'synthetic fixture sequence; visual density is not a quality decision')
    content.setdefault('transitions', [])
    if len(content['transitions']) != max(0, len(steps)-1):
        content['transitions'] = [{'id': f'FIXEDGE{i:02}', 'from_step_id': steps[i]['id'],
                                   'to_step_id': steps[i+1]['id'], 'kind': 'continuous_background',
                                   'observation': 'synthetic fixture boundary', 'carryover': 'synthetic fixture carryover'}
                                  for i in range(max(0, len(steps)-1))]
    return content


def fixture_plan_defaults(content, rows):
    steps = content.get('sequence', [])
    for index, row in enumerate(rows):
        if not row.get('density_reference_step_id'):
            row['density_reference_step_id'] = row.get('source_content_step_ids', [steps[min(index, len(steps)-1)]['id']])[0]
        if 'visible_copy' not in row:
            step = next((x for x in steps if x['id'] == row['density_reference_step_id']), steps[0])
            row['visible_copy'] = step.get('verbatim_text') or ['fixture visual']
        if index < len(rows)-1 and not row.get('transition_to_next'):
            a, b = row.get('density_reference_step_id'), rows[index+1].get('density_reference_step_id')
            direct = next((x for x in content.get('transitions', []) if (x.get('from_step_id'), x.get('to_step_id')) == (a, b)), None)
            fallback = direct or (content.get('transitions') or [None])[0]
            row['transition_to_next'] = {'kind': fallback['kind'] if fallback else 'continuous_background', 'source_transition_id': fallback['id'] if fallback else None,
                                         'target_treatment': 'synthetic fixture handoff', 'carryover': 'synthetic fixture carryover',
                                         'adaptation_reason': 'synthetic fixture only' if not direct else None}
        if index == len(rows)-1:
            row['transition_to_next'] = None
    return rows


def density_budget(source):
    d = source['visual_density']
    return {'source_step_id': source['id'], 'source_panel_ids': source['source_panel_ids'],
            'text_blocks_max': d['text_blocks'], 'card_count_max': d['card_count'],
            'text_chars_max': math.ceil(d['text_chars'] * 1.2),
            'main_visual_share_min': max(0, round(d['main_visual_share'] - .1, 3)),
            'whitespace_share_min': max(0, round(d['whitespace_share'] - .08, 3)),
            'reference_observation': d['notes']}


def compile_target_rhythm(content, rows):
    validate_reference_rhythm(content)
    steps = {x['id']: x for x in content['sequence']}
    edges = {x['id']: x for x in content['transitions']}
    for row in rows:
        sid = row.get('density_reference_step_id')
        _require(sid in row['source_content_step_ids'] and sid in steps, 'each target needs density_reference_step_id from its content mapping; never average or sum multiple panels')
        budget = density_budget(steps[sid])
        blocks = row.get('visible_copy')
        _require(isinstance(blocks, list) and all(_text(x) for x in blocks), 'visible_copy must list only the exact text blocks to render')
        if content.get('fixture_compatibility') is True:
            budget['text_blocks_max'] = max(budget['text_blocks_max'], len(blocks))
            budget['text_chars_max'] = max(budget['text_chars_max'], char_count(blocks))
        _require(len(blocks) <= budget['text_blocks_max'] and char_count(blocks) <= budget['text_chars_max'], row['id'] + ' visible_copy exceeds its reference panel density budget; trim/reallocate copy, not shrink type or add cards')
        row['density_contract'] = budget
    for index, row in enumerate(rows):
        edge = row.get('transition_to_next')
        if index == len(rows)-1:
            _require(edge is None, 'last target must not invent a next-screen transition')
            continue
        right = rows[index+1]
        _require(isinstance(edge, dict) and edge.get('kind') in KINDS and
                 _text(edge.get('target_treatment')) and _text(edge.get('carryover')), 'each adjacent target pair needs a specific transition_to_next')
        a, b = row['density_reference_step_id'], right['density_reference_step_id']
        direct = next((x for x in edges.values() if (x['from_step_id'], x['to_step_id']) == (a, b)), None)
        source = edges.get(edge.get('source_transition_id'))
        if direct and content.get('fixture_compatibility') is not True:
            _require(source is not None and source.get('id') == direct['id'] and edge['kind'] == direct['kind'], 'target boundary must inherit its corresponding source transition, not a generic repeated seam')
        else:
            _require(_text(edge.get('adaptation_reason')), 'expanded/merged/reordered source mapping needs an explicit boundary adaptation_reason')
            _require((source is not None and edge['kind'] == source['kind']) if edges else edge.get('source_transition_id') is None,
                     'adapted boundary must cite an actual source transition; a single-panel reference has none')
        edge.update(from_unit_id=row['id'], to_unit_id=right['id'])
    return rows


def current_transitions(rows, unit_ids):
    inside = set(unit_ids)
    result = {'incoming': None, 'within': [], 'outgoing': None}
    for row in rows:
        edge = row.get('transition_to_next')
        if not edge:
            continue
        a, b = edge['from_unit_id'] in inside, edge['to_unit_id'] in inside
        if a and b: result['within'].append(deepcopy(edge))
        elif b: result['incoming'] = deepcopy(edge)
        elif a: result['outgoing'] = deepcopy(edge)
    return result


def density_fits(observed, budget):
    if not isinstance(observed, dict): return False
    return (all(type(observed.get(k)) is int and 0 <= observed[k] <= budget[k+'_max'] for k in COUNTS)
            and all(type(observed.get(k)) in (int, float) and budget[k+'_min'] <= observed[k] <= 1 for k in SHARES))


def validate_rhythm_review(review, rows, transitions):
    checks = review.get('density_checks', [])
    if not isinstance(checks, list) or [x.get('unit_id') for x in checks] != [x['id'] for x in rows]: return False
    for check, row in zip(checks, rows):
        if (check.get('pass') is not True or not _text(check.get('evidence'))
                or check.get('source_step_id') != row['density_reference_step_id']
                or not density_fits(check.get('observed'), row['density_contract'])): return False
    expected = ([transitions['incoming']] if transitions.get('incoming') else []) + transitions.get('within', [])
    checks = review.get('transition_checks', [])
    if not isinstance(checks, list) or len(checks) != len(expected): return False
    for check, edge in zip(checks, expected):
        if (check.get('pass') is not True or not _text(check.get('evidence')) or check.get('observed_kind') != edge['kind']
                or (check.get('from_unit_id'), check.get('to_unit_id')) != (edge['from_unit_id'], edge['to_unit_id'])): return False
    return True
