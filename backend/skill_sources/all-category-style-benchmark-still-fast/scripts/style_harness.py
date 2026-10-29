"""Compatibility facade: the workflow module is the only state authority."""
from workflow import *

def validate_generation_request(path):
    req=load_json(path)
    required=('token','route','product_image_bindings','sanitized_style_crop_bindings','reference_narrative_logic','reference_content_analysis','visual_fidelity_contract','semantic_translation_map','route_specific_composition_plan','copy_risk_policy','render_contract','generation_scope','current_target_plan','target_reference_sequence')
    missing=[k for k in required if k not in req or req.get(k) is None or not req.get(k)]
    if missing: raise ValueError('generation request missing '+','.join(missing))
    if set(req['visual_fidelity_contract'].get('required_mechanisms',[])) != {'typography','material','composition','hierarchy','carrier','light_depth','density','reading_path'}: raise ValueError('visual fidelity contract must enumerate all eight reference mechanisms')
    if not isinstance(req['visual_fidelity_contract'].get('style_signature'),dict): raise ValueError('visual fidelity contract requires style_signature')
    rc=req['render_contract']
    if rc.get('quality')!='high' or rc.get('text_and_image_single_pass') is not True or rc.get('allow_post_text_overlay') is not False or rc.get('allow_low_resolution_upscale') is not False or rc.get('native_output_required') is not True or rc.get('postprocess_allowed') is not False: raise ValueError('generation request must require high-quality single-pass native text rendering without resize')
    if rc.get('native_generation_size') != req.get('output_contract',{}).get('canvas'): raise ValueError('native generation seed must equal route geometry')
    if req.get('route')=='DETAIL_PAGE':
        aspect=rc.get('aspect_ratio',{})
        if (aspect.get('width'),aspect.get('height')) != (3,8): raise ValueError('detail Pair render contract must require 3:8 aspect ratio')
        if not isinstance(aspect.get('tolerance'),(int,float)) or aspect['tolerance']<=0: raise ValueError('detail Pair aspect tolerance is required')
    if rc.get('provenance_required_for_nonfixture') is not True: raise ValueError('real review requires original-file provenance')
    prompt_contract=req.get('prompt_contract') or {}
    if prompt_contract.get('forbidden_decorative_label_forms') != list(FORBIDDEN_DECORATIVE_LABEL_FORMS):
        raise ValueError('generation request must carry the global decorative-label ban')
    if prompt_contract.get('reference_derived_density_and_style') is not True:
        raise ValueError('generation request must derive density and style from the reference')
    if prompt_contract.get('ban_unconfirmed_product_marks') is not True:
        raise ValueError('generation request must ban unconfirmed product names, logos and wordmarks')
    if prompt_contract.get('forbidden_typography_forms') != list(FORBIDDEN_TYPOGRAPHY_FORMS):
        raise ValueError('generation request must carry the forbidden handwritten/brush typography form ban')
    fact_ids={row.get('id') for row in req.get('product_visual_facts', [])}
    if set(req.get('copy_risk_policy', {})) != {'ordinary','evidence_required','category_review','blocked'}:
        raise ValueError('copy_risk_policy must preserve the four-tier compliance contract')
    validate_copy_compliance(req.get('route_specific_composition_plan', {}).get('units', []), fact_ids,
                             fixture=req.get('reference_content_analysis', {}).get('fixture_compatibility') is True)
    if req.get('route') == 'DETAIL_PAGE':
        validate_internal_copy_labels(req.get('route_specific_composition_plan', {}).get('units', []))
    from route_contracts import generation_scope
    job=req['output_contract']
    if req['generation_scope'] != generation_scope(req['route'],job): raise ValueError('generation scope does not match the current route job')
    if [x.get('id') for x in req['current_target_plan']] != job['unit_ids']: raise ValueError('current target plan must contain only the current job screens')
    from reference_rhythm import compile_target_rhythm, current_transitions
    from copy import deepcopy
    planned=deepcopy(req['route_specific_composition_plan']['units'])
    compile_target_rhythm(req['reference_content_analysis'],planned)
    expected_rows={row['id']:row for row in planned}
    for row in req['current_target_plan']:
        expected_row=expected_rows[row['id']]
        if any(row.get(k)!=expected_row.get(k) for k in ('density_contract','density_reference_step_id','visible_copy','transition_to_next')):
            raise ValueError('current target rhythm differs from the reference-bound plan')
    if req.get('target_transition_plan')!=current_transitions(planned,job['unit_ids']):
        raise ValueError('current target transitions differ from the adjacent route plan')
    if req.get('route') == 'DETAIL_PAGE':
        target_steps = [sid for row in req['current_target_plan'] for sid in row.get('source_content_step_ids', [])]
        target_step_set = set(target_steps)
        bound_steps = {item.get('source_content_step_id') for item in req.get('sanitized_style_crop_bindings', [])}
        sequence_steps = {item.get('source_content_step_id') for item in req.get('target_reference_sequence', [])}
        if not target_step_set.issubset(sequence_steps) or not target_step_set.issubset(bound_steps):
            missing = sorted(target_step_set - (sequence_steps & bound_steps))
            raise ValueError('detail Pair reference bindings must cover every current source step; missing sanitized bindings for '+','.join(missing))
        if any(item.get('source_content_step_id') not in target_step_set for item in req.get('target_reference_sequence', [])):
            raise ValueError('detail Pair reference sequence contains a source step outside the current target')
        if any(item.get('source_content_step_id') not in target_step_set for item in req.get('sanitized_style_crop_bindings', [])):
            raise ValueError('detail Pair sanitized crop binding contains a source step outside the current target')
        if any(item.get('source_content_step_id') is None for item in req.get('sanitized_style_crop_bindings', [])):
            raise ValueError('detail Pair non-fixture sanitized crops must retain source_content_step_id bindings')
    if req['route']=='DETAIL_PAGE':
        jobs=req['route_specific_composition_plan']['contract']['jobs']
        ratio=req.get('detail_ratio')
        if job not in jobs or req.get('group_id')!=job['id']: raise ValueError('detail Pair job binding is invalid')
        canvas=job['canvas']; expected_ratio=(8/3) if len(job['unit_ids'])==2 else (4/3)
        if abs(canvas[1]/canvas[0]-expected_ratio)>0.01: raise ValueError('detail Pair geometry must keep 3:8 aspect ratio')
        continuity=req.get('pair_continuity_contract')
        if len(job['unit_ids'])==2 and (not isinstance(continuity,dict) or not continuity.get('seam_rule') or not continuity.get('narrative_handoff')):
            raise ValueError('detail Pair requires a shared visual world and explicit seam continuity contract')
    return True
