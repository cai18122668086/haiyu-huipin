"""Synthetic fixtures test execution and provenance, never real visual quality."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import hashlib

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from reference_analysis import analysis_template, crop_panels, validate_panels
from route_contracts import ROUTES, MECHANISMS, audit_dimensions
from workflow import new_run, classify, compile_crops, passport, load, save, dump, sha, generation, record, revise, audit
from reference_rhythm import current_transitions
from test_workflow import legacy_analysis, setup, passing_audit
from analysis_support import physical_analysis
from generation_handoff import next_action, resume_handoff
from render_provenance import build_receipt, validate_receipt
from test_cli_e2e import run, ENTRY


def overview_run(root, route='DETAIL_PAGE'):
    product=root/'product.png'; ref=root/'reference.png'; style=root/'style.png'
    Image.new('RGB',(32,32),(90,60,30)).save(product)
    im=Image.new('RGB',(120,1400)); draw=ImageDraw.Draw(im)
    for i in range(7):
        draw.rectangle((0,i*200,119,(i+1)*200-1),fill=(30+i*25,120-i*10,180))
        draw.line((10,i*200+20,100,i*200+50),fill='white',width=3)
    im.save(ref); im.crop((10,21,50,40)).save(style)
    ws,d=new_run(root/'runs',[product],[ref]); classify(d,route)
    panels=[{'panel_id':f'B{i:02}','panel_order':i,'reference_input_id':'R01','bbox':[0,(i-1)*200,120,i*200]} for i in range(1,8)]
    a=legacy_analysis(style)
    a['source_content_analysis']['sequence']=[{'id':f'T{i:02}','source_order':i,'source_reference_ids':['R01'],
        'source_panel_ids':[f'B{i:02}'],'content_role':'hook' if i==1 else 'proof','verbatim_text':[],
        'text_status':'no_visible_text','normalized_message':f'synthetic content stage {i}','handoff':'next stage'} for i in range(1,8)]
    a=physical_analysis(a,d,ws,panels); ap=root/'analysis.json'; dump(ap,a)
    return ws,d,ap,a


@pytest.mark.parametrize('route', ROUTES)
def test_every_route_accepts_seven_real_panels_and_content_order(tmp_path, route):
    ws,d,ap,a=overview_run(tmp_path,route)
    compile_crops(d,ap,ws); passport(d,ap)
    assert d['state']=='VISUAL_PASSPORT_READY' and d['route']==route
    assert len(d['overview_detection']['panels'])==7
    assert len({p['source_crop_path'] for p in a['overview_detection']['panels']})==7
    if route=='DETAIL_PAGE': assert d['layout']['count']==10 and len(d['layout']['jobs'])==5
    if route=='CAROUSEL_SET': assert d['layout']['count']==7


@pytest.mark.parametrize('route', ROUTES)
def test_every_route_rejects_reused_panel_file(tmp_path, route):
    ws,d,ap,a=overview_run(tmp_path,route)
    a['overview_detection']['panels'][6]['source_crop_path']=a['overview_detection']['panels'][0]['source_crop_path']
    dump(ap,a)
    with pytest.raises(ValueError,match='distinct physical'): compile_crops(d,ap,ws)
    assert d['state']=='ROUTE_CLASSIFIED'
    assert not (ws/'artifacts/reference-crops/manifest.json').exists()


def test_old_seven_panel_ids_reusing_two_style_crops_are_blocked(tmp_path):
    ws,d,ap,a=overview_run(tmp_path)
    a['overview_detection']['panels']=[{'panel_order':i,'panel_id':f'B{i:02}','crop_unit_ids':['C01' if i<4 else 'C02']} for i in range(1,8)]
    dump(ap,a)
    with pytest.raises(ValueError,match='reference_input_id'): compile_crops(d,ap,ws)
    assert d['state']=='ROUTE_CLASSIFIED'


@pytest.mark.parametrize('defect',['bbox_overlap','wrong_pixels','stale_hash','not_raster','raw_panel_as_style','missing_sanitization_review'])
def test_physical_crop_and_analysis_only_boundary(tmp_path,defect):
    ws,d,ap,a=overview_run(tmp_path)
    panels=a['overview_detection']['panels']
    if defect=='bbox_overlap': panels[1]['bbox']=panels[0]['bbox']
    if defect in ('wrong_pixels','stale_hash','not_raster'):
        p=Path(panels[0]['source_crop_path'])
        if defect=='not_raster': p.write_text('not a raster')
        else: Image.new('RGB',(120,200),'black').save(p)
        if defect!='stale_hash': panels[0]['sha256']=sha(p)
    if defect=='raw_panel_as_style': a['units'][0]['source_crop_path']=panels[0]['source_crop_path']
    if defect=='missing_sanitization_review': del a['units'][0]['source_copy_presence']
    dump(ap,a)
    with pytest.raises((ValueError,OSError)): compile_crops(d,ap,ws)
    assert d['state']=='ROUTE_CLASSIFIED'


@pytest.mark.parametrize('dimension',MECHANISMS)
def test_passport_reports_missing_dimension_and_can_retry(tmp_path,dimension):
    ws,d,ap,a=overview_run(tmp_path); compile_crops(d,ap,ws)
    observation=a['mechanisms'].pop(dimension); dump(ap,a)
    with pytest.raises(ValueError,match=dimension): passport(d,ap)
    assert d['state']=='REFERENCE_DECOMPOSED'
    a['mechanisms'][dimension]=observation; dump(ap,a); passport(d,ap)
    assert d['state']=='VISUAL_PASSPORT_READY'


@pytest.mark.parametrize('defect',['unknown_key','empty','unknown_crop','missing_panel','reordered','merged_panels','unreadable_without_evidence'])
def test_passport_rejects_incomplete_visual_or_content_evidence(tmp_path,defect):
    ws,d,ap,a=overview_run(tmp_path); compile_crops(d,ap,ws)
    content=a['source_content_analysis']['sequence']
    if defect=='unknown_key': a['mechanisms']['typography_morphology']=a['mechanisms'].pop('typography')
    if defect=='empty': a['mechanisms']['typography']['observation']='  '
    if defect=='unknown_crop': a['mechanisms']['material']['source_unit_ids']=['nonexistent']
    if defect=='missing_panel': content.pop()
    if defect=='reordered': content[0]['source_panel_ids'],content[1]['source_panel_ids']=content[1]['source_panel_ids'],content[0]['source_panel_ids']
    if defect=='merged_panels': content[0]['source_panel_ids']=['B01','B02']
    if defect=='unreadable_without_evidence': content[0]['text_status']='partially_unreadable'
    dump(ap,a)
    with pytest.raises(ValueError): passport(d,ap)
    assert d['state']=='REFERENCE_DECOMPOSED'


def test_template_and_crop_command_leave_observations_for_actual_review(tmp_path):
    ws,d,ap,a=overview_run(tmp_path)
    template=tmp_path/'fresh.json'; analysis_template(d,template)
    data=json.loads(template.read_text(encoding='utf-8'))
    assert list(data['mechanisms'])==list(MECHANISMS)
    with pytest.raises(ValueError,match='decided by image inspection'): crop_panels(d,template,ws)
    data['overview_detection']=a['overview_detection']; dump(template,data)
    data=crop_panels(d,template,ws)
    assert len(data['source_content_analysis']['sequence'])==7
    assert all(s['text_status'] is None for s in data['source_content_analysis']['sequence'])
    assert all(not m['observation'] for m in data['mechanisms'].values())
    with pytest.raises(ValueError,match='already exists'): analysis_template(d,template)


def test_pair_scope_token_order_resume_and_native_master(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE')
    generation(d,'G01',ws); token=d['active_token']; save(ws,d)
    req=json.loads((ws/'artifacts/generation-request-G01.json').read_text(encoding='utf-8'))
    assert req['generation_scope']['artifact_count']==1
    assert req['generation_scope']['unit_ids']==['S01','S02']
    assert req['generation_scope']['unit_regions']==[{'unit_id':'S01','bbox':[0,0,768,1024]},{'unit_id':'S02','bbox':[0,1024,768,2048]}]
    assert [x['id'] for x in req['current_target_plan']]==['S01','S02']
    assert next_action(d,ws)['resume_existing_request'] is True
    for key in ('G01','G02'):
        with pytest.raises(ValueError,match='finish active target'): generation(d,key,ws)
        assert d['active_token']==token
    assert run('resume','--workspace',ws)['next_action']['target_id']=='G01'
    bad=tmp_path/'one-screen.png'; Image.new('RGB',(1440,1920)).save(bad)
    with pytest.raises(ValueError,match='detail master'): record(d,'G01',bad,ws)
    assert not d['artwork']
    master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master); record(d,'G01',master,ws)
    review=tmp_path/'review.json'; passing_audit(review,'DETAIL_PAGE'); audit(d,'G01',review,ws)
    assert next_action(d,ws)['arguments']==['--group-id','G02']
    generation(d,'G02',ws)
    assert d['active_token']!=token
    req2=json.loads((ws/'artifacts/generation-request-G02.json').read_text(encoding='utf-8'))
    assert [x['id'] for x in req2['current_target_plan']]==['S03','S04']


def test_resume_emits_same_persisted_handoff_without_new_token(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE')
    generation(d,'G01',ws)
    token=d['active_token']
    request=(ws/'artifacts/generation-request-G01.json').read_bytes()
    prompt=(ws/'artifacts/imagegen-prompt-G01.txt').read_bytes()
    manifest=ws/'artifacts/recovery-manifest.json'
    assert manifest.is_file()
    resumed=run('resume','--workspace',ws)
    assert resumed['next_action']['command']=='resume-imagegen-request'
    handoff=run('resume-imagegen-request','--workspace',ws)
    assert handoff['status']=='IMAGEGEN_RESUME_READY' and handoff['token']==token
    assert (ws/'artifacts/generation-request-G01.json').read_bytes()==request
    assert (ws/'artifacts/imagegen-prompt-G01.txt').read_bytes()==prompt
    assert load(ws)['active_token']==token


@pytest.mark.parametrize('defect',['request','input'])
def test_resume_fails_closed_instead_of_restarting_after_interruption(tmp_path,defect):
    ws,d,product=setup(tmp_path,'DETAIL_PAGE')
    generation(d,'G01',ws); token=d['active_token']
    if defect=='request':
        (ws/'artifacts/generation-request-G01.json').write_text('{}',encoding='utf-8')
    else:
        product.unlink()
    result=subprocess.run([sys.executable,'-X','utf8',str(ENTRY),'resume','--workspace',str(ws)],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==2 and 'resume blocked' in result.stderr
    assert load(ws)['active_token']==token
    assert len(list((ws/'artifacts').glob('generation-request-G*.json')))==1


def test_cli_failed_passport_keeps_state_and_recovers_without_route_change(tmp_path):
    ws,d,ap,a=overview_run(tmp_path); compile_crops(d,ap,ws); save(ws,d)
    good=deepcopy(a); a['mechanisms'].pop('material'); dump(ap,a)
    result=subprocess.run([sys.executable,str(ENTRY),'compile-visual-passport','--workspace',str(ws),'--analysis',str(ap)],capture_output=True)
    assert result.returncode==2 and b'material' in result.stderr and b'state_preserved' in result.stderr
    assert load(ws)['state']=='REFERENCE_DECOMPOSED'
    dump(ap,good); result=run('compile-visual-passport','--workspace',ws,'--analysis',ap)
    assert result['state']=='VISUAL_PASSPORT_READY' and result['route']=='DETAIL_PAGE'


def test_generation_blocks_changed_reference_or_product(tmp_path):
    ws,d,product=setup(tmp_path,'DETAIL_PAGE')
    Image.new('RGB',(100,100),'red').save(product)
    with pytest.raises(ValueError,match='registered input changed'): generation(d,'G01',ws)
    assert 'active_token' not in d


def test_revision_requires_new_token_and_stops_after_one_revision(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'
    Image.new('RGB',(1440,3840)).save(master); review=tmp_path/'bad-review.json'
    dump(review,{'review_kind':'fixture','pass':False})
    generation(d,'G01',ws); token=d['active_token']; record(d,'G01',master,ws); audit(d,'G01',review,ws)
    revise(d,'G01'); generation(d,'G01',ws)
    assert d['active_token']!=token and d['groups']['G01']['revision_index']==1
    record(d,'G01',master,ws); audit(d,'G01',review,ws)
    assert d['state']=='GROUP_FAILED_CONTINUE'
    with pytest.raises(ValueError): revise(d,'G01')


def test_new_run_explicit_count_is_not_lost_at_classification(tmp_path):
    ws,d,_,_=overview_run(tmp_path)
    d['state']='INPUT_REGISTERED'; d['screen_count']=8
    classify(d,'DETAIL_PAGE',evidence='user requested eight screens')
    assert d['layout']['count']==8 and len(d['layout']['jobs'])==4


def test_cli_overview_template_to_five_pairs_and_ten_ordered_slices(tmp_path):
    _,_,_,fixture=overview_run(tmp_path)
    created=run('new','--product-input',tmp_path/'product.png','--reference-input',tmp_path/'reference.png','--base-dir',tmp_path/'cli-runs')
    ws=Path(created['run']); run('classify-route','--workspace',ws,'--route','DETAIL_PAGE')
    ap=ws/'reference-analysis.json'; run('init-reference-analysis','--workspace',ws,'--analysis',ap)
    template=json.loads(ap.read_text(encoding='utf-8')); template['overview_detection']=fixture['overview_detection']; dump(ap,template)
    result=run('crop-reference-panels','--workspace',ws,'--analysis',ap)
    assert result['state']=='ROUTE_CLASSIFIED'
    a=json.loads(ap.read_text(encoding='utf-8'))
    for field in ('units','mechanisms','source_semantics','narrative_logic','source_content_analysis'): a[field]=fixture[field]
    dump(ap,a); run('compile-reference-crops','--workspace',ws,'--analysis',ap)
    run('compile-visual-passport','--workspace',ws,'--analysis',ap)
    fp=ws/'facts.json'; dump(fp,{'facts':[{'id':'f1','text':'synthetic product appearance','status':'user_confirmed','claim_type':'appearance','evidence_input_ids':['P01']},{'id':'f_form','text':'rounded rectangular cross-section with a 1:1.9 width-to-height ratio','status':'user_confirmed','claim_type':'appearance','evidence_input_ids':['P01'],'visual_attribute':'shape'} ]})
    run('register-product-facts','--workspace',ws,'--facts',fp)
    mapping=[1,1,2,3,3,4,5,6,6,7]; rows=[]
    for i,source in enumerate(mapping,1):
        rows.append({'id':f'S{i:02}','primary_duty':f'content stage {source}','narrative_role':'hook' if i==1 else 'proof',
                     'narrative_step_ids':['N01'],'source_content_step_ids':[f'T{source:02}'],'mechanism_ids':['typography','composition'],
                     'semantic_translation':{sid:{'target_world':'current product native world','retain_literal_source':False}
                         for sid in ('source_environment_texture','source_annotation')}})
    pp=ws/'plan.json'; dump(pp,{'units':rows}); run('plan-target-page','--workspace',ws,'--plan',pp)
    pa=ws/'plan-audit.json'; dump(pa,{'review_kind':'fixture','pass':True,'route':'DETAIL_PAGE','unit_ids':[r['id'] for r in rows]})
    run('audit-plan','--workspace',ws,'--audit',pa)
    wrong=subprocess.run([sys.executable,str(ENTRY),'generate-single-request','--workspace',str(ws)],capture_output=True)
    assert wrong.returncode==2 and not list((ws/'artifacts').glob('generation-request-*.json'))
    for i in range(1,6):
        gid=f'G{i:02}'; run('generate-pair-request','--workspace',ws,'--group-id',gid)
        request=json.loads((ws/f'artifacts/generation-request-{gid}.json').read_text(encoding='utf-8'))
        assert request['generation_scope']['unit_ids']==[f'S{i*2-1:02}',f'S{i*2:02}']
        assert len(request['current_target_plan'])==2
        panel_paths={p['source_crop_path'] for p in a['overview_detection']['panels']}
        assert not panel_paths.intersection(x['path'] for x in request['sanitized_style_crop_bindings'])
        master=ws/(gid+'.png'); im=Image.new('RGB',(1440,3840),(i,20,30))
        ImageDraw.Draw(im).rectangle((0,1920,1439,3839),fill=(i,80,90)); im.save(master)
        render_receipt=ws/(gid+'-render.json'); dump(render_receipt,build_receipt(master,master,[1440,3840]))
        run('record-pair-artwork','--workspace',ws,'--group-id',gid,'--artwork',master,'--render-receipt',render_receipt)
        review=ws/'audit.json'; passing_audit(review,'DETAIL_PAGE')
        run('audit-pair','--workspace',ws,'--group-id',gid,'--audit',review)
        run('accept-pair','--workspace',ws,'--group-id',gid)
    globalp=ws/'global.json'; dump(globalp,{'review_kind':'fixture','pass':True,'ordered_ids':[r['id'] for r in rows]})
    run('audit-global','--workspace',ws,'--audit',globalp); run('finalize','--workspace',ws); run('export-single-screens','--workspace',ws)
    assert len(list((ws/'artifacts').glob('generation-request-G*.json')))==5
    assert [p.stem for p in sorted((ws/'exports').glob('*.png'))]==[f'S{i:02}' for i in range(1,11)]
    for i in range(1,6):
        with Image.open(ws/f'exports/S{i*2-1:02}.png') as upper: assert upper.size==(1440,1920) and upper.getpixel((0,0))==(i,20,30)
        with Image.open(ws/f'exports/S{i*2:02}.png') as lower: assert lower.size==(1440,1920) and lower.getpixel((0,0))==(i,80,90)
    assert load(ws)['state']=='DELIVERED'


def test_adapter_emits_one_json_handoff_with_exact_pair_inputs(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); generation(d,'G01',ws)
    adapter=ENTRY.parent/'imagegen_adapter.py'
    result=subprocess.run([sys.executable,'-X','utf8',str(adapter),'--workspace',str(ws),'--request',str(ws/'artifacts/generation-request-G01.json'),
                           '--output',str(ws/'actual-pair.png')],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr
    handoff=json.loads(result.stdout)
    assert handoff['status']=='IMAGEGEN_HANDOFF_READY' and handoff['generation_scope']['unit_ids']==['S01','S02']
    assert handoff['invocation_count']==1 and len(handoff['referenced_image_paths'])==2
    prompt=Path(handoff['prompt_path']).read_text(encoding='utf-8')
    assert Path(handoff['prompt_path']).is_file() and not (ws/'actual-pair.png').exists()
    assert len(prompt)<12000 and 'route_specific_composition_plan' not in prompt and '连续性规则' in prompt
    assert '字体与文字风格硬约束' in prompt and '跨缝锚点' in prompt and '3:8' in prompt
    assert 'S01' not in prompt and 'S02' not in prompt and '内部屏幕编号' in prompt


def test_imagegen_adapter_blocks_request_when_workflow_gate_is_not_live(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); generation(d,'G01',ws); save(ws,d)
    d['state']='PRODUCT_FACTS_READY'; save(ws,d)
    adapter=ENTRY.parent/'imagegen_adapter.py'
    result=subprocess.run([sys.executable,'-X','utf8',str(adapter),'--workspace',str(ws),
                           '--request',str(ws/'artifacts/generation-request-G01.json'),'--output',str(ws/'out.png')],
                          capture_output=True,text=True,encoding='utf-8')
    assert result.returncode!=0 and 'imagegen gate requires persisted GENERATION_ALLOWED state' in result.stderr

def _native_receipt(path, artwork):
    dump(path, build_receipt(artwork,artwork,[1440,3840]))

def test_detail_cli_requires_native_render_receipt(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master)
    generation(d,'G01',ws); save(ws,d)
    result=subprocess.run([sys.executable,str(ENTRY),'record-pair-artwork','--workspace',str(ws),'--group-id','G01','--artwork',str(master)],capture_output=True,text=True,encoding='utf-8',errors='replace')
    assert result.returncode==2 and 'render-receipt' in result.stderr

def test_nonfixture_requires_native_provenance_and_fixture_cannot_override(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master)
    generation(d,'G01',ws); receipt_path=tmp_path/'render.json'; _native_receipt(receipt_path,master); record(d,'G01',master,ws,receipt_path)
    failed=tmp_path/'failed.json'; dump(failed, {'review_kind':'manual','pass':False,'product_identity':True,'fact_safety':True,'dimensions':{k:True for k in audit_dimensions(['DETAIL_PAGE'])},'artwork_sha256':sha(master),'style_fidelity_score':0.2,'product_adaptation_score':0.2,'mechanism_scores':{k:0.2 for k in MECHANISMS}}); audit(d,'G01',failed,ws)
    assert d['state']=='REVISION_ALLOWED'
    revise(d,'G01'); generation(d,'G01',ws); record(d,'G01',master,ws,receipt_path)
    passing=tmp_path/'passing.json'; dump(passing, {'review_kind':'fixture','pass':True,'product_identity':True,'fact_safety':True,'dimensions':{k:True for k in audit_dimensions(['DETAIL_PAGE'])}}); audit(d,'G01',passing,ws)
    assert d['state']=='GROUP_FAILED_CONTINUE' and d['groups']['G01']['status']=='failed_continue'

def test_nonfixture_with_native_provenance_can_pass(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master)
    generation(d,'G01',ws); receipt_path=tmp_path/'render.json'; _native_receipt(receipt_path,master); record(d,'G01',master,ws,receipt_path)
    ids=d['groups']['G01']['unit_ids']; units=[d['units'][sid] for sid in ids]
    density_checks=[{'unit_id':u['id'],'source_step_id':u['density_reference_step_id'],'observed':{'text_blocks':u['density_contract']['text_blocks_max'],'card_count':u['density_contract']['card_count_max'],'text_chars':u['density_contract']['text_chars_max'],'main_visual_share':u['density_contract']['main_visual_share_min'],'whitespace_share':u['density_contract']['whitespace_share_min']},'pass':True,'evidence':'manual density review'} for u in units]
    transitions=current_transitions([d['units'][sid] for sid in d['layout']['unit_ids']],ids)
    transition_checks=[{'from_unit_id':e['from_unit_id'],'to_unit_id':e['to_unit_id'],'observed_kind':e['kind'],'pass':True,'evidence':'manual boundary review'} for e in (([transitions['incoming']] if transitions.get('incoming') else [])+transitions.get('within',[]))]
    review=tmp_path/'manual.json'; dump(review, {'review_kind':'manual','pass':True,'product_identity':True,'fact_safety':True,'dimensions':{k:True for k in audit_dimensions(['DETAIL_PAGE'])},'artwork_sha256':sha(master),'style_fidelity_score':0.9,'product_adaptation_score':0.9,'mechanism_scores':{k:0.9 for k in MECHANISMS},'density_checks':density_checks,'transition_checks':transition_checks}); audit(d,'G01',review,ws)
    assert d['state']=='ACCEPTED' and d['groups']['G01']['status']=='accepted'

def test_unconfirmed_product_mark_fails_visual_audit(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master)
    generation(d,'G01',ws); receipt_path=tmp_path/'render.json'; _native_receipt(receipt_path,master); record(d,'G01',master,ws,receipt_path)
    ids=d['groups']['G01']['unit_ids']; units=[d['units'][sid] for sid in ids]
    density_checks=[{'unit_id':u['id'],'source_step_id':u['density_reference_step_id'],'observed':{'text_blocks':u['density_contract']['text_blocks_max'],'card_count':u['density_contract']['card_count_max'],'text_chars':u['density_contract']['text_chars_max'],'main_visual_share':u['density_contract']['main_visual_share_min'],'whitespace_share':u['density_contract']['whitespace_share_min']},'pass':True,'evidence':'manual density review'} for u in units]
    transitions=current_transitions([d['units'][sid] for sid in d['layout']['unit_ids']],ids)
    transition_checks=[{'from_unit_id':e['from_unit_id'],'to_unit_id':e['to_unit_id'],'observed_kind':e['kind'],'pass':True,'evidence':'manual boundary review'} for e in (([transitions['incoming']] if transitions.get('incoming') else [])+transitions.get('within',[]))]
    review=tmp_path/'marked.json'; dump(review, {'review_kind':'manual','pass':True,'product_identity':True,'fact_safety':True,'unconfirmed_product_mark':True,'dimensions':{k:True for k in audit_dimensions(['DETAIL_PAGE'])},'artwork_sha256':sha(master),'style_fidelity_score':0.9,'product_adaptation_score':0.9,'mechanism_scores':{k:0.9 for k in MECHANISMS},'density_checks':density_checks,'transition_checks':transition_checks}); audit(d,'G01',review,ws)
    assert d['state']=='REVISION_ALLOWED' and d['groups']['G01']['status']=='revision_allowed'

def test_forbidden_typography_form_fails_visual_audit(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master)
    generation(d,'G01',ws); receipt_path=tmp_path/'render.json'; _native_receipt(receipt_path,master); record(d,'G01',master,ws,receipt_path)
    ids=d['groups']['G01']['unit_ids']; units=[d['units'][sid] for sid in ids]
    density_checks=[{'unit_id':u['id'],'source_step_id':u['density_reference_step_id'],'observed':{'text_blocks':u['density_contract']['text_blocks_max'],'card_count':u['density_contract']['card_count_max'],'text_chars':u['density_contract']['text_chars_max'],'main_visual_share':u['density_contract']['main_visual_share_min'],'whitespace_share':u['density_contract']['whitespace_share_min']},'pass':True,'evidence':'manual density review'} for u in units]
    transitions=current_transitions([d['units'][sid] for sid in d['layout']['unit_ids']],ids)
    transition_checks=[{'from_unit_id':e['from_unit_id'],'to_unit_id':e['to_unit_id'],'observed_kind':e['kind'],'pass':True,'evidence':'manual boundary review'} for e in (([transitions['incoming']] if transitions.get('incoming') else [])+transitions.get('within',[]))]
    review=tmp_path/'typography.json'; dump(review, {'review_kind':'manual','pass':True,'product_identity':True,'fact_safety':True,'forbidden_typography_form':True,'dimensions':{k:True for k in audit_dimensions(['DETAIL_PAGE'])},'artwork_sha256':sha(master),'style_fidelity_score':0.9,'product_adaptation_score':0.9,'mechanism_scores':{k:0.9 for k in MECHANISMS},'density_checks':density_checks,'transition_checks':transition_checks}); audit(d,'G01',review,ws)
    assert d['state']=='REVISION_ALLOWED' and d['groups']['G01']['status']=='revision_allowed'

def test_native_original_cannot_be_replaced_by_upscaled_copy(tmp_path):
    native=tmp_path/'tool-native.png'; enlarged=tmp_path/'enlarged.png'
    Image.new('RGB',(768,2048),'gray').save(native)
    with Image.open(native) as im: im.resize((1440,3840)).save(enlarged)
    with pytest.raises(ValueError,match='do not upscale'):
        build_receipt(native,enlarged,[1440,3840])

def test_detail_pair_accepts_native_3_to_8_without_fixed_pixel_size(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(768,2048),'gray').save(master)
    generation(d,'G01',ws); receipt=tmp_path/'render.json'; dump(receipt,build_receipt(master,master,None)); record(d,'G01',master,ws,receipt)
    assert d['detail_ratio']=={'width':768,'screen_height':1024,'ratio':1024/768}

def test_detail_pair_rejects_non_3_to_8_native_ratio(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(768,1800),'gray').save(master)
    generation(d,'G01',ws)
    with pytest.raises(ValueError,match='3:8 native Pair ratio'): record(d,'G01',master,ws)

def test_next_action_surfaces_persisted_generation_block(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE')
    d['state']='GENERATION_ALLOWED'; save(ws,d)
    dump(ws/'artifacts/generation-block.json', {'status':'GENERATION_BLOCKED','command':'audit-plan','reason':'route/order mismatch'})
    action=next_action(d,ws)
    assert action['blocked'] is True and action['command']=='audit-plan'

@pytest.mark.parametrize('field,value', [('resized',True),('output_sha256','0'*64),
    ('native_dimensions',[768,2048]),('native_output_path','missing.png'),('native_sha256','0'*64)])
def test_invalid_native_receipt_cannot_record(tmp_path,field,value):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master)
    generation(d,'G01',ws); rp=build_receipt(master,master,[1440,3840]); rp[field]=value
    receipt_path=tmp_path/'render.json'; dump(receipt_path,rp)
    with pytest.raises((ValueError,OSError)): record(d,'G01',master,ws,receipt_path)
    assert d['state']=='GENERATION_ALLOWED' and not d['artwork']

def test_receipt_cli_compares_unchanged_copy(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); generation(d,'G01',ws)
    native=tmp_path/'tool-native.png'; copy=tmp_path/'run-copy.png'
    Image.new('RGB',(1440,3840)).save(native); copy.write_bytes(native.read_bytes())
    receipt_path=tmp_path/'render.json'
    result=subprocess.run([sys.executable,str(ENTRY.parent/'render_provenance.py'),
        '--request',str(ws/'artifacts/generation-request-G01.json'),'--native-output',str(native),
        '--artwork',str(copy),'--output',str(receipt_path)],capture_output=True,text=True,encoding='utf-8',errors='replace')
    assert result.returncode==0,result.stderr
    rp=json.loads(receipt_path.read_text(encoding='utf-8')); validate_receipt(rp,copy,[1440,3840])
    from jsonschema import Draft202012Validator
    schema=json.loads((ENTRY.parent.parent/'schemas/render-receipt.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator(schema).validate(rp)
    assert rp['native_output_path']==str(native.resolve()) and rp['path']==str(copy.resolve())

def test_manual_without_original_evidence_fails_even_at_target_size(tmp_path):
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1440,3840)).save(master)
    generation(d,'G01',ws); record(d,'G01',master,ws)
    review=tmp_path/'manual.json'; dump(review, {'review_kind':'manual','pass':True,'product_identity':True,'fact_safety':True,
        'dimensions':{k:True for k in audit_dimensions(['DETAIL_PAGE'])},'artwork_sha256':sha(master),
        'style_fidelity_score':0.9,'product_adaptation_score':0.9,'mechanism_scores':{k:0.9 for k in MECHANISMS}})
    audit(d,'G01',review,ws); assert d['state']=='REVISION_ALLOWED'

def test_first_native_pair_ratio_is_reused_by_later_pairs(tmp_path):
    from workflow import global_audit, finalize_stage, export
    from style_harness import validate_generation_request
    ws,d,_=setup(tmp_path,'DETAIL_PAGE'); master=tmp_path/'pair.png'; Image.new('RGB',(1536,4096),'blue').save(master)
    generation(d,'G01',ws); receipt_path=tmp_path/'render.json'; dump(receipt_path,build_receipt(master,master,None)); record(d,'G01',master,ws,receipt_path)
    assert d['detail_ratio']['width']==1536 and d['detail_ratio']['screen_height']==2048
    review=tmp_path/'review.json'; passing_audit(review,'DETAIL_PAGE'); audit(d,'G01',review,ws); d['groups']['G01']['accepted_at']=True; d['state']='ACCEPTED'
    generation(d,'G02',ws); req=json.loads((ws/'artifacts/generation-request-G02.json').read_text(encoding='utf-8'))
    assert req['render_contract']['native_generation_size']==[1536,4096]
    assert req['generation_scope']['unit_regions']==[{'unit_id':'S03','bbox':[0,0,1536,2048]},{'unit_id':'S04','bbox':[0,2048,1536,4096]}]
    assert validate_generation_request(ws/'artifacts/generation-request-G02.json')
    prompt=(ws/'artifacts/imagegen-prompt-G02.txt').read_text(encoding='utf-8')
    assert 'S03' not in prompt and 'S04' not in prompt and '3:8' in prompt and '1536px' not in prompt
    wrong=tmp_path/'wrong.png'; Image.new('RGB',(1536,3584)).save(wrong)
    with pytest.raises(ValueError,match='3:8 native Pair ratio'): record(d,'G02',wrong,ws)
    larger=tmp_path/'larger.png'; Image.new('RGB',(1200,3200),'blue').save(larger)
    receipt2=tmp_path/'render2.json'; dump(receipt2,build_receipt(larger,larger,None)); record(d,'G02',larger,ws,receipt2); audit(d,'G02',review,ws)
    for gid in ('G03','G04','G05'):
        generation(d,gid,ws); record(d,gid,master,ws,receipt_path); audit(d,gid,review,ws)
    globalp=tmp_path/'global.json'; dump(globalp,{'review_kind':'fixture','pass':True,'ordered_ids':d['layout']['unit_ids']})
    global_audit(d,globalp); finalize_stage(d,ws); export(d,ws)
    with Image.open(ws/'exports/S01.png') as im: assert im.size==(1536,2048)
    with Image.open(ws/'exports/S03.png') as im: assert im.size==(1200,1600)
    assert len(list((ws/'exports').glob('S*.png')))==10

@pytest.mark.parametrize('route',[r for r in ROUTES if r!='DETAIL_PAGE'])
def test_non_detail_prompt_keeps_its_single_unit_workflow(tmp_path,route):
    ws,d,_=setup(tmp_path,route); generation(d,'U01',ws)
    prompt=(ws/'artifacts/imagegen-prompt-U01.txt').read_text(encoding='utf-8')
    assert '本次只生成 U01' in prompt
    assert '当前详情页' not in prompt and 'Pair' not in prompt

def test_global_manual_receipt_cannot_upgrade_fixture_artwork_quality(tmp_path):
    from workflow import global_audit, finalize_stage
    ws,d,_=setup(tmp_path,'SEARCH_COVER'); generation(d,'U01',ws)
    master=tmp_path/'cover.png'; Image.new('RGB',(1200,1200)).save(master); record(d,'U01',master,ws)
    review=tmp_path/'fixture.json'; passing_audit(review,'SEARCH_COVER'); audit(d,'U01',review,ws)
    review=tmp_path/'global.json'; dump(review,{'review_kind':'manual','pass':True,'ordered_ids':['U01']})
    global_audit(d,review); finalize_stage(d,ws)
    manifest=json.loads((ws/'artifacts/final-manifest.json').read_text(encoding='utf-8'))
    assert manifest['visual_quality_status']=='fixture_structure_only'


def test_export_blocks_artwork_changed_after_acceptance(tmp_path):
    from workflow import export
    ws,d,_=setup(tmp_path,'SEARCH_COVER'); generation(d,'U01',ws)
    art=tmp_path/'art.png'; Image.new('RGB',(1200,1200),'red').save(art)
    record(d,'U01',art,ws); review=tmp_path/'review.json'; passing_audit(review,'SEARCH_COVER'); audit(d,'U01',review,ws)
    d['state']='FINALIZED'; Image.new('RGB',(1200,1200),'blue').save(art)
    with pytest.raises(ValueError,match='changed before export'): export(d,ws)
    assert not (ws/'exports').exists()
