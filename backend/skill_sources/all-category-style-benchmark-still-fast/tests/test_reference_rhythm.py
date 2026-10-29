"""Deterministic contracts, not real imagegen/visual quality evidence."""
from copy import deepcopy
import json
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from reference_rhythm import (compile_target_rhythm, validate_reference_rhythm, current_transitions,
                              validate_rhythm_review, char_count)
from generation_handoff import write_handoff, build_continuity_contract
from route_contracts import ROUTES, layout, generation_scope


def content_and_rows(ids):
    steps=[]; rows=[]; edges=[]
    kinds=['hard_cut','continuous_background','whitespace_break','color_transition']
    for i, uid in enumerate(ids):
        text=['hero'] if i%2==0 else ['proof','evidence','takeaway']
        steps.append({'id':f'T{i}', 'source_panel_ids':[f'B{i}'], 'text_status':'transcribed', 'verbatim_text':text,
                      'normalized_message':f'content stage {i}', 'handoff':'next buyer question',
                      'visual_density':{'text_blocks':len(text),'card_count':0 if i%2==0 else 2,'text_chars':char_count(text),
                                        'main_visual_share':.8 if i%2==0 else .5,'whitespace_share':.2,
                                        'notes':'one large visual' if i%2==0 else 'two proof cards'}})
        rows.append({'id':uid,'primary_duty':f'duty {i}','narrative_role':'hero' if i==0 else 'proof',
                     'source_content_step_ids':[f'T{i}'],'density_reference_step_id':f'T{i}',
                     'visible_copy':text, 'semantic_translation':{'world':{'target_world':'current product world'}}})
        if i<len(ids)-1:
            edge={'id':f'E{i}','from_step_id':f'T{i}','to_step_id':f'T{i+1}', 'kind':kinds[i%4],
                  'observation':f'observed boundary {i}', 'carryover':f'anchor {i}'}
            edges.append(edge)
            rows[-1]['transition_to_next']={'source_transition_id':edge['id'],'kind':edge['kind'],
                                           'target_treatment':f'product-adapted boundary {i}', 'carryover':edge['carryover']}
    return {'sequence':steps,'transitions':edges,'rhythm_summary':'alternating sparse hero and proof; varied observed boundaries'},rows


@pytest.mark.parametrize('route',ROUTES)
def test_all_routes_keep_panel_density_and_only_current_job_transitions(tmp_path,route):
    c=layout(route); content,rows=content_and_rows(c['unit_ids']); compile_target_rhythm(content,rows)
    for job in c['jobs']:
        current=[r for r in rows if r['id'] in job['unit_ids']]
        req={'route':route,'render_contract':{'native_generation_size':job['canvas']},'generation_scope':generation_scope(route,job),
             'current_target_plan':current,'reference_content_analysis':content,'product_palette_contract':{},
             'target_transition_plan':current_transitions(rows,job['unit_ids'])}
        req['pair_continuity_contract']=build_continuity_contract(req,current,req['generation_scope'])
        p=tmp_path/(job['id']+'.txt'); write_handoff(req,p); prompt=p.read_text(encoding='utf8')
        for row in current:
            assert row['density_contract']['card_count_max']==(0 if rows.index(row)%2==0 else 2)
            assert '最多 '+str(row['density_contract']['text_blocks_max'])+' 个文字块' in prompt
        if route=='DETAIL_PAGE':
            for edge in req['target_transition_plan']['within']:
                assert edge['target_treatment'] in prompt and edge['kind'] in prompt
            assert not any(uid in prompt for uid in c['unit_ids'])
        else:
            assert '上下屏连续性合同' not in prompt


def test_sparse_hero_cannot_become_a_card_grid_or_copy_dump():
    content,rows=content_and_rows(['S01','S02']); rows[0]['visible_copy']=['hero','extra card']
    with pytest.raises(ValueError,match='exceeds'): compile_target_rhythm(content,rows)


def test_different_reference_boundaries_cannot_be_flattened_to_one_kind():
    content,rows=content_and_rows(['S01','S02','S03','S04'])
    rows[1]['transition_to_next']['kind']='hard_cut'
    with pytest.raises(ValueError,match='corresponding source transition'): compile_target_rhythm(content,rows)


def test_overview_cannot_reuse_one_physical_panel_for_multiple_density_records():
    content,_=content_and_rows(['S01','S02']); content['sequence'][1]['source_panel_ids']=['B0']
    with pytest.raises(ValueError,match='split the overview'): validate_reference_rhythm(content)


def test_real_density_review_rejects_added_cards_and_missing_boundary_evidence():
    content,rows=content_and_rows(['S01','S02']); compile_target_rhythm(content,rows)
    transitions=current_transitions(rows,['S01','S02'])
    review={'density_checks':[{'unit_id':r['id'],'source_step_id':r['density_reference_step_id'],
                              'observed':content['sequence'][i]['visual_density'],'pass':True,'evidence':'synthetic observed values'}
                             for i,r in enumerate(rows)],
            'transition_checks':[{'from_unit_id':'S01','to_unit_id':'S02','observed_kind':'hard_cut','pass':True,'evidence':'synthetic boundary observation'}]}
    assert validate_rhythm_review(review,rows,transitions)
    bad=deepcopy(review); bad['density_checks'][0]['observed']['card_count']=3
    assert not validate_rhythm_review(bad,rows,transitions)
    bad=deepcopy(review); bad['transition_checks']=[]
    assert not validate_rhythm_review(bad,rows,transitions)


def test_cross_group_boundaries_are_incoming_and_outgoing_without_extra_screens():
    content,rows=content_and_rows(['S01','S02','S03','S04']); compile_target_rhythm(content,rows)
    first=current_transitions(rows,['S01','S02']); second=current_transitions(rows,['S03','S04'])
    assert first['outgoing']==second['incoming']
    assert first['within'][0]['kind']=='hard_cut'
    assert second['within'][0]['kind']=='whitespace_break'
