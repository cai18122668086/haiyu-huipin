import json, sys, tempfile
from pathlib import Path
from PIL import Image
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from workflow import *
from route_contracts import ROUTES, audit_dimensions
from analysis_support import physical_analysis

def files(root):
    product=root/'product.png'; reference=root/'reference.png'; crop=root/'style-crop.png'
    Image.new('RGB',(100,100),(20,30,40)).save(product); Image.new('RGB',(120,120),(100,60,40)).save(reference); Image.new('RGB',(80,80),(90,60,30)).save(crop)
    return product,reference,crop
def legacy_analysis(crop, source=True):
    return {'units':[{'id':'C01','reference_input_id':'R01','source_crop_path':str(crop),'source_role':'style_mechanism_only','source_semantics':['source_environment_texture','source_annotation'],'review_status':'fixture'}], 'overview_detection':{'is_overview':False,'panel_count':1,'panels':[{'panel_order':1,'crop_unit_ids':['C01']}]}, 'mechanisms':{k:{'observation':k+' observed in crop','source_unit_ids':['C01']} for k in MECHANISMS}, 'source_semantics':[{'id':'source_environment_texture','kind':'environment','label':'source environment'},{'id':'source_annotation','kind':'carrier','label':'source note'}], 'narrative_logic':{'opening':'lead with buyer question','proof_progression':'show evidence then explain use','takeaway':'state product takeaway','handoff':'bridge to next unit','sequence':[{'id':'N01','role':'hook','description':'question-led opening','source_reference_ids':['R01']}]}, 'source_content_analysis':{'set_summary':'question to evidence to takeaway','sequence':[{'id':'CSTEP01','source_order':1,'source_reference_ids':['R01'],'content_role':'hook','verbatim_text':['source headline'],'normalized_message':'opens with buyer question','handoff':'moves to proof'}]}}
def analysis(crop, source=True):
    ref=crop.parent/'reference.png'
    d={'inputs':[{'id':'R01','path':str(ref),'sha256':sha(ref),'role':'benchmark_reference'}]}
    return physical_analysis(legacy_analysis(crop,source),d,crop.parent/'fixture-panels')

def fact_file(root):
    p=root/'facts.json'; p.write_text(json.dumps({'facts':[{'id':'f_color','text':'graphite mesh chair','status':'user_confirmed','claim_type':'appearance','evidence_input_ids':['P01'],'visual_attribute':'color'},{'id':'f_form','text':'圆角矩形靠背与坐垫 宽高比约1:1.9 一体式底座','status':'user_confirmed','claim_type':'appearance','evidence_input_ids':['P01'],'visual_attribute':'shape'},{'id':'f_lumbar','text':'adjustable lumbar support','status':'user_confirmed','claim_type':'feature','evidence_input_ids':['P01']},{'id':'f_recline','text':'90-145 degree lockable recline','status':'user_confirmed','claim_type':'performance','evidence_input_ids':['P01']}],'palette_contract':{'source_fact_ids':['f_color']}},ensure_ascii=False),encoding='utf-8'); return p
def plan_file(root, route):
    c=layout(route); p=root/'plan.json'; rows=[]
    for uid in c['unit_ids']:
        rows.append({'id':uid,'primary_duty':route+' duty','narrative_role':'hook' if uid==c['unit_ids'][0] else 'proof','narrative_step_ids':['N01'],'source_content_step_ids':['CSTEP01'],'mechanism_ids':['typography','composition','carrier'],'primary_claim_ids':['f_color'] if uid=='S01' else [],'semantic_translation':{'source_environment_texture':{'target_world':'product-native office support texture','retain_literal_source':False},'source_annotation':{'target_world':'chair adjustment callout','retain_literal_source':False}}})
    p.write_text(json.dumps({'units':rows}),encoding='utf-8'); return p

def nonfixture_analysis(crop, opening_function):
    """Build a manually reviewed, method-complete analysis for semantic gates."""
    a = analysis(crop)
    unit = a['units'][0]
    unit['review_status'] = 'manual'
    unit['source_product_presence'] = False
    unit['source_content_step_ids'] = ['CSTEP01']
    step = a['source_content_analysis']['sequence'][0]
    step.update({
        'source_panel_ids': ['B01'],
        'text_status': 'transcribed',
        'opening_function': opening_function,
        'conversion_stage': 'opening orientation',
        'buyer_question': 'What should I understand first?',
        'belief_shift': 'The first decision is clear.',
        'persuasion_job': 'Establish the opening decision before later proof.',
        'evidence_obligations': ['show the source signal that supports the opening'],
        'expression_actions': ['lead with the observed opening signal'],
        'visual_density': {
            'text_blocks': 1, 'card_count': 0, 'text_chars': 14,
            'main_visual_share': 0.7, 'whitespace_share': 0.15,
            'notes': 'one opening text block with a dominant visual'
        },
    })
    a['source_content_analysis'].update({
        'rhythm_summary': 'single opening panel',
        'transitions': [],
    })
    return a

def nonfixture_plan_file(root, route, source_function):
    c = layout(route)
    p = root / 'plan.json'
    rows = []
    for index, uid in enumerate(c['unit_ids']):
        row = {
            'id': uid,
            'primary_duty': 'translate the observed opening duty',
            'narrative_role': 'opening' if index == 0 else 'continuation',
            'narrative_step_ids': ['N01'],
            'source_content_step_ids': ['CSTEP01'],
            'source_function': source_function,
            'buyer_question': 'What should I understand first?',
            'answer': 'The opening decision is explicit before later proof.',
            'belief_shift': 'The first decision is clear.',
            'handoff_question': 'What proof should come next?',
            'required_visual_roles': ['dominant opening signal'],
            'evidence_bindings': [{
                'source_step_ids': ['CSTEP01'],
                'visual_form': 'single dominant signal',
                'claim_scope': 'opening orientation only',
            }],
            'mechanism_ids': ['typography', 'composition', 'carrier'],
            'primary_claim_ids': ['f_color'] if uid == 'S01' else [],
            'density_reference_step_id': 'CSTEP01',
            'visible_copy': [f'opening decision {index + 1}'],
            'source_expansion_note': f'reference-derived expansion {index + 1}',
            'semantic_translation': {
                'source_environment_texture': {
                    'target_world': 'product-native support texture',
                    'retain_literal_source': False,
                },
                'source_annotation': {
                    'target_world': 'product-native explanatory callout',
                    'retain_literal_source': False,
                },
            },
        }
        if index < len(c['unit_ids']) - 1:
            row['transition_to_next'] = {
                'kind': 'continuous_background',
                'target_treatment': 'continue the opening field',
                'carryover': 'retain the same visual anchor',
                'source_transition_id': None,
                'adaptation_reason': 'single-panel source has no adjacent transition',
            }
        else:
            row['transition_to_next'] = None
        rows.append(row)
    p.write_text(json.dumps({'units': rows}), encoding='utf-8')
    return p

def nonfixture_setup(root, opening_function):
    product, reference, crop = files(root)
    facts_p = fact_file(root)
    ws, d = new_run(root / 'runs', [product], [reference], facts_p)
    classify(d, 'DETAIL_PAGE', None, 'semantic regression route')
    ap = root / 'analysis.json'
    ap.write_text(json.dumps(nonfixture_analysis(crop, opening_function)), encoding='utf-8')
    compile_crops(d, ap, ws)
    passport(d, ap)
    facts(d, facts_p)
    return ws, d
def passing_audit(path, route, artwork_hash=None):
    dims={k:True for k in audit_dimensions([route])}; d={'review_kind':'fixture','pass':True,'product_identity':True,'fact_safety':True,'dimensions':dims,'artwork_sha256':artwork_hash}
    path.write_text(json.dumps(d),encoding='utf-8'); return path
def setup(root, route, count=None):
    product,reference,crop=files(root); facts_p=fact_file(root); ws,d=new_run(root/'runs',[product],[reference],facts_p,count); classify(d,route,count,'explicit test route'); a=analysis(crop); ap=root/'analysis.json'; ap.write_text(json.dumps(a),encoding='utf-8'); compile_crops(d,ap,ws); passport(d,ap); facts(d,facts_p); pp=plan_file(root,route); plan(d,pp,ws); pa=root/'plan-audit.json'; pa.write_text(json.dumps({'review_kind':'fixture','pass':True,'route':route,'unit_ids':d['layout']['unit_ids']}),encoding='utf-8'); audit_plan(d,pa); save(ws,d); return ws,d,product
def test_detail_default_and_export():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); ws,d,product=setup(root,'DETAIL_PAGE'); assert d['screen_count']==10; assert list(d['groups'])==['G01','G02','G03','G04','G05']; assert d['groups']['G05']['unit_ids']==['S09','S10']
        master=root/'master.png'; Image.new('RGB',(1440,3840),(20,30,40)).save(master)
        auditp=root/'audit.json'
        for gid in ['G01','G02','G03','G04','G05']:
            generation(d,gid,ws); record(d,gid,master,ws); passing_audit(auditp,'DETAIL_PAGE'); audit(d,gid,auditp,ws); d['groups'][gid]['accepted_at']=True; d['state']='ACCEPTED'; save(ws,d)
        d['global_audit']={'status':'passed','review_kind':'fixture','ordered_ids':d['layout']['unit_ids']}; d['state']='FINAL_AUDIT_REQUIRED'; finalize_stage(d,ws); export(d,ws); assert len(list((ws/'exports').glob('S*.png')))==10
        assert Image.open(ws/'exports'/'S10.png').size==(1440,1920)

def test_result_promise_opening_cannot_be_rewritten_as_problem_awareness():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws, d = nonfixture_setup(root, 'result_promise')
        bad = nonfixture_plan_file(root, 'DETAIL_PAGE', 'problem_awareness')
        with pytest.raises(ValueError, match='must preserve CSTEP01.*result_promise'):
            plan(d, bad, ws)
        good = nonfixture_plan_file(root, 'DETAIL_PAGE', 'result_promise')
        plan(d, good, ws)
        assert d['units']['S01']['source_function'] == 'result_promise'

def test_problem_awareness_opening_remains_valid_when_reference_starts_with_pain():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws, d = nonfixture_setup(root, 'problem_awareness')
        plan_path = nonfixture_plan_file(root, 'DETAIL_PAGE', 'problem_awareness')
        plan(d, plan_path, ws)
        assert {row['source_function'] for row in d['units'].values()} == {'problem_awareness'}

def test_detail_plan_uses_reference_steps_without_fixed_target_story_fields():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws, d = nonfixture_setup(root, 'result_promise')
        plan_path = nonfixture_plan_file(root, 'DETAIL_PAGE', 'result_promise')
        payload = json.loads(plan_path.read_text(encoding='utf-8'))
        removed = ('primary_duty', 'narrative_role', 'narrative_step_ids', 'source_function',
                   'buyer_question', 'answer', 'belief_shift', 'handoff_question',
                   'required_visual_roles', 'evidence_bindings')
        for row in payload['units']:
            for key in removed:
                row.pop(key, None)
        plan_path.write_text(json.dumps(payload), encoding='utf-8')
        plan(d, plan_path, ws)
        assert d['units']['S01']['source_function'] == 'result_promise'
        assert d['units']['S01']['narrative_role'] == 'hook'
        audit_path = root / 'plan-audit.json'
        audit_path.write_text(json.dumps({'review_kind': 'fixture', 'pass': True,
                                          'route': 'DETAIL_PAGE',
                                          'unit_ids': d['layout']['unit_ids']}), encoding='utf-8')
        audit_plan(d, audit_path)
        generation(d, 'G01', ws)
        prompt = (ws / 'artifacts' / 'imagegen-prompt-G01.txt').read_text(encoding='utf-8')
        assert '对标回答的问题=What should I understand first?' in prompt
        assert '不要从详情页默认模板' in prompt
        assert '本屏买家疑问=；' not in prompt

def test_plan_normalizes_plain_string_source_semantics_and_rejects_flat_translation():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws, d = nonfixture_setup(root, 'result_promise')
        # Real visual analyses may emit source_semantics as plain strings.
        d['source_semantic_inventory'] = ['source_environment_texture', 'source_annotation']
        plan_path = nonfixture_plan_file(root, 'DETAIL_PAGE', 'result_promise')
        plan(d, plan_path, ws)
        assert d['state'] == 'PLAN_AUDIT_REQUIRED'

        ws2, d2 = nonfixture_setup(root, 'result_promise')
        d2['source_semantic_inventory'] = ['source_environment_texture', 'source_annotation']
        plan_path = nonfixture_plan_file(root, 'DETAIL_PAGE', 'result_promise')
        payload = json.loads(plan_path.read_text(encoding='utf-8'))
        payload['units'][0]['semantic_translation'] = {
            'product_facts': ['f_color'], 'translation': 'flattened legacy shape'
        }
        plan_path.write_text(json.dumps(payload), encoding='utf-8')
        with pytest.raises(ValueError, match='semantic_translation entries must be objects'):
            plan(d2, plan_path, ws2)

def test_every_non_detail_contract():
    for route in ROUTES:
        if route in ('DETAIL_PAGE','ECOMMERCE_BANNER'): continue
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); ws,d,product=setup(root,route); assert d['layout']['count'] >= 3 if route in ('CAROUSEL_SET','MARKETING_VISUAL_SET') else d['layout']['count']==1
            auditp=root/'audit.json'; unit_ids=d['layout']['unit_ids'];
            for uid in unit_ids:
                generation(d,uid,ws); out=root/(uid+'.png'); dims=d['layout']['jobs'][int(uid[1:])-1]['canvas']; Image.new('RGB',tuple(dims),(20,30,40)).save(out); record(d,uid,out,ws); passing_audit(auditp,route); audit(d,uid,auditp,ws); d['state']='ACCEPTED'; d['units'][uid]['accepted_at']=True; save(ws,d)
            d['global_audit']={'status':'passed'}; d['state']='FINAL_AUDIT_REQUIRED'; finalize_stage(d,ws); export(d,ws); assert [p.stem for p in sorted((ws/'exports').glob('*.png'))]==unit_ids
def test_source_leakage_and_missing_crops_fail():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); product,reference,crop=files(root); ws,d=new_run(root/'runs',[product],[reference]); classify(d,'DETAIL_PAGE',None,'test'); ap=root/'bad.json'; ap.write_text(json.dumps({'units':[{'id':'C01','reference_input_id':'R01','source_crop_path':str(crop),'source_role':'style_mechanism_only','source_semantics':[],'review_status':'fixture','source_environment_presence':True}], 'overview_detection':{'is_overview':False,'panel_count':1,'panels':[{'panel_order':1,'crop_unit_ids':['C01']}]}}),encoding='utf-8')
        try: compile_crops(d,ap,ws); assert False
        except ValueError: pass

def test_mixed_inputs_require_role_review_and_bind_hashes():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); paths=[]
        for i in range(4):
            p=root/f'product-{i}.png'; Image.new('RGB',(40+i,40+i),(10+i,20,30)).save(p); paths.append(p)
        ref=root/'benchmark.png'; Image.new('RGB',(80,80),(120,60,20)).save(ref); paths.append(ref)
        ws,d=new_run(root/'runs',[],[],mixed_inputs=paths)
        assert all(x['role']=='unassigned' for x in d['inputs'])
        try: classify(d,'DETAIL_PAGE'); assert False
        except ValueError as e: assert 'inspect mixed inputs' in str(e)
        review=root/'role-review.json'; review.write_text(json.dumps({'review_kind':'manual','items':[
            {'input_id':x['id'],'sha256':x['sha256'],'role':'benchmark_reference' if x['path']==str(ref.resolve()) else 'product_original','confidence':0.95,'visual_observation':'visible product subject and silhouette' if x['path']!=str(ref.resolve()) else 'composed benchmark layout with typography','evidence':['human visual inspection']}
            for x in d['inputs']]},ensure_ascii=False),encoding='utf-8')
        inspect_inputs(d,review,ws); classify(d,'DETAIL_PAGE'); assert d['input_roles_confirmed'] is True
        assert len([x for x in d['inputs'] if x['role']=='product_original'])==4
        assert len([x for x in d['inputs'] if x['role']=='benchmark_reference'])==1

def test_mixed_role_review_fail_closed_on_missing_or_bad_evidence():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); paths=[]
        for i in range(2):
            p=root/f'{i}.png'; Image.new('RGB',(20,20),(i,20,30)).save(p); paths.append(p)
        ws,d=new_run(root/'runs',[],[],mixed_inputs=paths)
        base=[{'input_id':x['id'],'sha256':x['sha256'],'role':'product_original','confidence':0.95,'visual_observation':'clear product image','evidence':['inspection']} for x in d['inputs']]
        review=root/'bad.json'; review.write_text(json.dumps({'review_kind':'fixture','items':base}),encoding='utf-8')
        for mutate in (lambda rows: rows[:-1], lambda rows: [{**rows[0],'confidence':0.5},rows[1]], lambda rows: [{**rows[0],'sha256':'0'*64},rows[1]], lambda rows: [{**rows[0],'role':'unknown'},rows[1]]):
            rows=mutate(base.copy()); review.write_text(json.dumps({'review_kind':'fixture','items':rows}),encoding='utf-8')
            try: inspect_inputs(d,review,ws); assert False
            except ValueError: pass

def test_multi_reference_carousel_requires_complete_ordered_decomposition():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); product,_,crop=files(root); refs=[]
        for i in range(3):
            p=root/f'ref{i}.png'; Image.new('RGB',(60+i,60+i),(80+i,40,20)).save(p); refs.append(p)
        ws,d=new_run(root/'runs',[product],refs); classify(d,'CAROUSEL_SET',None,'carousel test')
        crops=[]
        for i,r in enumerate(refs):
            cp=root/f'crop{i}.png'; Image.new('RGB',(30,30),(i,40,80)).save(cp); crops.append({'id':f'C{i+1:02}','reference_input_id':f'R{i+1:02}','source_crop_path':str(cp),'source_role':'layout_only','source_semantics':[f's{i}'],'review_status':'fixture','source_order':i+1})
        a=root/'analysis.json'; a.write_text(json.dumps({'units':crops,'overview_detection':{'is_overview':True,'panel_count':3,'panels':[{'panel_order':i+1,'crop_unit_ids':[f'C{i+1:02}']} for i in range(3)]},'mechanisms':{k:{'observation':'observed '+k,'source_unit_ids':['C01']} for k in MECHANISMS},'source_semantics':[],'narrative_logic':{'opening':'question','proof_progression':'evidence','takeaway':'takeaway','handoff':'next','sequence':[{'id':'N01','role':'hook','description':'ordered benchmark narrative','source_reference_ids':['R01','R02','R03']}]},'source_content_analysis':{'set_summary':'ordered story','sequence':[{'id':'CSTEP01','source_order':1,'source_reference_ids':['R01','R02','R03'],'content_role':'hook','verbatim_text':['headline'],'normalized_message':'opening','handoff':'proof'}]}}),encoding='utf-8')
        prepared=physical_analysis(load_json(a),d,ws)
        prepared['source_content_analysis']['sequence']=[{'id':f'CSTEP{i:02}','source_order':i,'source_reference_ids':[f'R{i:02}'],'source_panel_ids':[f'B{i:02}'],'content_role':'proof','verbatim_text':['synthetic headline'],'text_status':'transcribed','normalized_message':'fixture proof','handoff':'next'} for i in range(1,4)]
        dump(a,prepared)
        compile_crops(d,a,ws); assert [x['reference_input_id'] for x in d['benchmark_sequence']]==['R01','R02','R03']
        passport(d,a); fp=fact_file(root); facts(d,fp)
        p=root/'plan.json'; p.write_text(json.dumps({'units':[{'id':f'U{i:02}','primary_duty':'duty','mechanism_ids':['typography'],'primary_claim_ids':[],'narrative_role':'stage','source_reference_ids':['R01'] if i==1 else (['R02'] if i==2 else ['R03']),'semantic_translation':{}} for i in range(1,4)]}),encoding='utf-8')
        try: plan(d,p,ws); assert False
        except ValueError as e: assert 'semantic translation' in str(e) or 'source sequence' in str(e)

def test_generation_request_requires_native_high_res_single_pass_text():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); ws,d,product=setup(root,'SEARCH_COVER'); generation(d,'U01',ws)
        req=json.loads((ws/'artifacts/generation-request-U01.json').read_text(encoding='utf-8'))
        assert req['visual_fidelity_contract']['required_mechanisms']==list(MECHANISMS)
        assert req['prompt_contract']['do_not_substitute_generic_ecommerce_style'] is True
        assert req['prompt_contract']['forbidden_decorative_label_forms']==list(FORBIDDEN_DECORATIVE_LABEL_FORMS)
        assert req['prompt_contract']['reference_derived_density_and_style'] is True
        assert req['prompt_contract']['ban_unconfirmed_product_marks'] is True
        assert req['prompt_contract']['forbidden_typography_forms'] == list(FORBIDDEN_TYPOGRAPHY_FORMS)
        assert set(req['copy_risk_policy']) == {'ordinary','evidence_required','category_review','blocked'}
        assert 'premium_visual_contract' not in req['prompt_contract']
        prompt=(ws/'artifacts'/'imagegen-prompt-U01.txt').read_text(encoding='utf-8')
        assert '全路由只禁止 AI 标签化的载体形式' in prompt
        assert '高级感与信息密度必须从对标观察结果继承' in prompt
        assert '最后一屏的收束/总结/结尾只是内部规划职责' in prompt
        assert '文案合规按四档执行' in prompt
        assert '禁止复制这些 AI 标签载体' in prompt
        assert '胶囊标题、编号徽标' not in prompt
        assert '不得出现任何未经产品事实确认的品牌名、系列名、产品名' in prompt
        assert req['render_contract']=={'native_generation_size':[1200,1200],'quality':'high','text_and_image_single_pass':True,'text_rendering':'native_model_rendered','allow_post_text_overlay':False,'allow_low_resolution_upscale':False,'native_output_required':True,'postprocess_allowed':False,'provenance_required_for_nonfixture':True,'minimum_accepted_dimensions':[1200,1200]}
        from style_harness import validate_generation_request
        assert validate_generation_request(ws/'artifacts/generation-request-U01.json') is True

def test_detail_generation_scopes_ordered_source_crops_and_sequence_prompt():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); ws,d=nonfixture_setup(root,'result_promise')
        pp=nonfixture_plan_file(root,'DETAIL_PAGE','result_promise'); plan(d,pp,ws)
        audit_path=root/'plan-audit.json'; dump(audit_path,{'review_kind':'manual','pass':True,'route':'DETAIL_PAGE','unit_ids':d['layout']['unit_ids']}); audit_plan(d,audit_path)
        generation(d,'G01',ws)
        req=load_json(ws/'artifacts/generation-request-G01.json')
        prompt=(ws/'artifacts/imagegen-prompt-G01.txt').read_text(encoding='utf-8')
        assert req['target_reference_sequence'][0]['source_order']==1
        assert len(req['sanitized_style_crop_bindings'])==1
        assert '当前 Pair 对标单屏顺序' in prompt and '源序号=1' in prompt
        assert '唯一屏间分界是横向接缝' in prompt
        assert '这些是硬上限' in prompt
        assert '不得出现任何未经产品事实确认的品牌名、系列名、产品名' in prompt

def test_generation_gate_rejects_persisted_plan_block_even_when_state_was_bypassed():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); ws,d,product=setup(root,'DETAIL_PAGE')
        generation(d,'G01',ws); save(ws,d)
        dump(ws/'artifacts/generation-block.json', {'status':'GENERATION_BLOCKED','command':'audit-plan','reason':'route/order mismatch','state':'GENERATION_ALLOWED','route':'DETAIL_PAGE'})
        with pytest.raises(ValueError, match='GENERATION_BLOCKED'):
            validate_generation_gate(ws, ws/'artifacts/generation-request-G01.json')

def test_nonfixture_crop_requires_explicit_product_identity_exclusion():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); product,reference,crop=files(root); ws,d=new_run(root/'runs',[product],[reference]); classify(d,'DETAIL_PAGE',None,'crop contract')
        ap=root/'analysis.json'; a=nonfixture_analysis(crop,'result_promise'); a['units'][0].pop('source_product_presence',None); ap.write_text(json.dumps(a),encoding='utf-8')
        with pytest.raises(ValueError, match='source_product_presence=false'):
            compile_crops(d,ap,ws)

def test_nonfixture_sequence_rejects_flattened_method_template():
    rows=[]
    for index in range(4):
        rows.append({'id':f'T{index+1:02}','content_role':f'role-{index}','normalized_message':f'message-{index}',
                     'conversion_stage':'consideration','buyer_question':'same question','persuasion_job':'same job',
                     'belief_shift':'same shift','evidence_obligations':['same evidence'],'expression_actions':['same action']})
    with pytest.raises(ValueError,match='flattens distinct panels'):
        validate_sequence_method_distinction(rows)

def test_nonfixture_sequence_rejects_replacement_character_corruption():
    row={'id':'T01','content_role':'proof','normalized_message':'bad\ufffdtext',
         'conversion_stage':'consideration','buyer_question':'question','persuasion_job':'job',
         'belief_shift':'shift','evidence_obligations':['evidence'],'expression_actions':['action']}
    with pytest.raises(ValueError,match='replacement characters'):
        validate_sequence_method_distinction([row])

def test_copy_risk_tiers_do_not_turn_normal_product_copy_into_evidence_debt():
    assert classify_copy_risk('轻薄 柔软 30ml 便于携带') == 'ordinary'
    assert classify_copy_risk('专利配方 抗菌99.9%') == 'evidence_required'
    assert classify_copy_risk('美白改善肤色') == 'category_review'
    assert classify_copy_risk('国家级 包治百病') == 'blocked'
    rows=[{'id':'S01','visible_copy':['轻薄 柔软 30ml']}]
    validate_copy_compliance(rows, {'f_color'})
    with pytest.raises(ValueError, match='evidence-gated copy'):
        validate_copy_compliance([{'id':'S01','visible_copy':['专利配方']}], {'f_color'})
    validate_copy_compliance([{'id':'S01','visible_copy':['专利配方'],'copy_evidence_ids':['f_patent']}], {'f_patent'})
    with pytest.raises(ValueError, match='hard-prohibited'):
        validate_copy_compliance([{'id':'S01','visible_copy':['国家级品质']}], {'f_quality'})

def test_detail_evidence_facts_follow_benchmark_without_exactly_once_gate():
    rows=[
        {'id':'S01','primary_claim_ids':['patent_fact']},
        {'id':'S02','primary_claim_ids':['test_fact']},
        {'id':'S03','primary_claim_ids':[]},
    ]
    validate_copy_compliance(rows, {'patent_fact','test_fact'})
    # A supplied patent/test fact may be unused, or repeated, when the
    # benchmark's mapped steps call for that treatment. There is no universal
    # exactly-once coverage rule.
    validate_copy_compliance([{'id':'S01','primary_claim_ids':[]}], {'patent_fact'})
    validate_copy_compliance([
        {'id':'S01','visible_copy':['专利配方'], 'copy_evidence_ids':['patent_fact']},
        {'id':'S02','visible_copy':['专利配方'], 'copy_evidence_ids':['patent_fact']},
    ], {'patent_fact'})

def test_product_evidence_inventory_tracks_optional_attached_evidence():
    rows=[{'id':'patent_fact','text':'confirmed evidence'}]
    validate_evidence_inventory(['P01','P02'], rows, [
        {'input_id':'P01','kind':'product_identity','observed_fact_ids':[]},
        {'input_id':'P02','kind':'patent','observed_fact_ids':['patent_fact']},
    ])
    with pytest.raises(ValueError,match='evidence_inventory'):
        validate_evidence_inventory(['P01','P02'], rows, None)
    validate_evidence_inventory(['P01','P02'], rows, [
        {'input_id':'P01','kind':'product_identity','observed_fact_ids':[]},
        {'input_id':'P02','kind':'patent','observed_fact_ids':[]},
    ])

def test_product_evidence_inventory_requires_exactly_one_row_per_input():
    with pytest.raises(ValueError, match='one row per product input'):
        validate_evidence_inventory(['P01','P02'], [{'id':'evidence_fact'}], [
            {'input_id':'P01','kind':'product_identity'},
            {'input_id':'P02','kind':'patent','observed_fact_ids':['evidence_fact']},
            {'input_id':'P02','kind':'patent','observed_fact_ids':['evidence_fact']},
        ])

def test_detail_expansion_follows_benchmark_repetition_without_skill_gate():
    first={'id':'S01','source_content_step_ids':['T01'],'visible_copy':['Primary outcome'],'source_expansion_note':'Observed outcome statement'}
    second={'id':'S02','source_content_step_ids':['T01'],'visible_copy':['How the mechanism works'],'source_expansion_note':'Observed mechanism explanation'}
    validate_internal_copy_labels([first, second])
    validate_internal_copy_labels([first, {**second,'source_expansion_note':''}])
    validate_internal_copy_labels([first, {
        'id':'S03','source_content_step_ids':['T01'],
        'visible_copy':[' Primary   outcome '],
        'source_expansion_note':'Different note with no new visible information',
    }])

def test_detail_visible_copy_rejects_internal_closing_labels():
    for label in ('收束','卖点收束','总结：产品特点','结尾'):
        with pytest.raises(ValueError, match='internal closing label'):
            validate_internal_copy_labels([{'id':'S10','visible_copy':[label]}])
    validate_internal_copy_labels([{'id':'S10','visible_copy':['产品名称','已确认的使用方法']}])

def test_carousel_default_count_follows_reference_story_steps():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); product,reference,crop=files(root); ws,d=new_run(root/'runs',[product],[reference]); classify(d,'CAROUSEL_SET',None,'default carousel')
        ap=root/'analysis.json'; a=analysis(crop); a['source_content_analysis']['sequence']=[{'id':f'CSTEP{i:02}','source_order':i,'source_reference_ids':['R01'],'source_panel_ids':['B01'],'text_status':'transcribed','content_role':'step','verbatim_text':[f'text {i}'],'normalized_message':f'message {i}','handoff':'next'} for i in range(1,6)]; ap.write_text(json.dumps(a),encoding='utf-8'); compile_crops(d,ap,ws); passport(d,ap)
        assert d['layout']['count']==5 and d['layout']['unit_ids']==[f'U{i:02}' for i in range(1,6)] and d['count_source']=='reference_content_analysis'

def test_carousel_explicit_count_overrides_reference_story_count():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); product,reference,crop=files(root); ws,d=new_run(root/'runs',[product],[reference]); classify(d,'CAROUSEL_SET',4,'user explicitly requested four')
        ap=root/'analysis.json'; a=analysis(crop); a['source_content_analysis']['sequence']=[{'id':f'CSTEP{i:02}','source_order':i,'source_reference_ids':['R01'],'source_panel_ids':['B01'],'text_status':'transcribed','content_role':'step','verbatim_text':[f'text {i}'],'normalized_message':f'message {i}','handoff':'next'} for i in range(1,6)]; ap.write_text(json.dumps(a),encoding='utf-8'); compile_crops(d,ap,ws); passport(d,ap)
        assert d['layout']['count']==4 and d['count_source']=='user'
