"""Authoritative fail-closed workflow. Commands are thin wrappers in entrypoint.py."""
from __future__ import annotations
import hashlib, json, re, shutil, uuid
from datetime import datetime, timezone
from pathlib import Path
from route_contracts import ROUTES, SPEC, MECHANISMS, CONTAMINANTS, layout, canonical, audit_dimensions
from reference_analysis import validate_panels, validate_mechanisms, validate_content_panels
from reference_rhythm import compile_target_rhythm, current_transitions, validate_rhythm_review, fixture_defaults, fixture_plan_defaults

TRANSLATION_FIELDS = ('reference_style_mechanisms','product_visual_facts','product_palette_contract',
                      'semantic_translation_map','route_specific_composition_plan','forbidden_literal_inheritance')
FORBIDDEN_DECORATIVE_LABEL_FORMS = (
    'pill capsule chip or rounded-card containers used to package copy as labels',
    'floating sticker tag badge or label-cloud treatments used as decorative filler',
    'repeated boxed icon-and-caption label components that replace the reference information layout',
)
FORBIDDEN_TYPOGRAPHY_FORMS = (
    'handwritten brush calligraphy marker or cursive display fonts used for visible copy',
    'irregular stroke lettering that mimics a handwritten note or brush script',
)
COPY_BLOCKED_PATTERNS = (
    r'国家级', r'最高级', r'最佳', r'包治百病', r'保本', r'无风险', r'稳赚',
    r'保证收益', r'驰名商标', r'绝对安全', r'百分之百安全', r'100%安全',
)
COPY_EVIDENCE_PATTERNS = (
    r'专利', r'认证', r'检测', r'实验(?:证明|数据)?', r'临床', r'销量', r'排名',
    r'全网', r'行业第一', r'第一品牌', r'唯一', r'抗菌', r'防螨', r'零甲醛',
    r'纯天然', r'无添加', r'99(?:\.\d+)?%', r'提升[^。；，,]{0,8}倍',
)
COPY_CATEGORY_REVIEW_PATTERNS = (
    r'治疗', r'治愈', r'疾病', r'抗癌', r'降血糖', r'降血压', r'消炎', r'祛斑',
    r'祛痘', r'美白', r'防脱', r'减肥', r'增高', r'失眠', r'药妆', r'医用级',
)
COPY_RISK_POLICY = {
    'ordinary': '允许使用已确认或可直接观察的普通产品描述；不要求额外检测报告',
    'evidence_required': '仅在绑定产品事实和对应证据时使用；不把用户确认的普通描述升级成此档',
    'category_review': '按商品监管类别和目标平台复核；不做全品类自动拦截',
    'blocked': '禁止渲染；应改写为真实、具体、可核验的产品信息',
}
STATES = ('INPUT_REGISTERED','ROUTE_CLASSIFIED','REFERENCE_DECOMPOSED','VISUAL_PASSPORT_READY',
          'PRODUCT_FACTS_READY','PLAN_AUDIT_REQUIRED','GENERATION_ALLOWED',
          'ARTWORK_RECORDED','AUDIT_REQUIRED','REVISION_ALLOWED','RE_AUDIT_REQUIRED','ACCEPTED',
          'GROUP_FAILED_CONTINUE','FINAL_AUDIT_REQUIRED','FINALIZED','DELIVERY_READY','DELIVERED')

# 产品几何形态锁：事实集必须至少一条 fact 用具体几何描述产品「长什么样」。
# 只写功效/功能（除臭率、成分百分比）而不写截面/比例/结构，模型会回退到品类通用款。
GEOMETRY_FACETS = (
    '截面', '横截面', '圆角矩形', '方柱', '圆柱', '扁', '跑道形', '宽高比', '比例',
    '接缝', '分模线', '盖', '瓶盖', '瓶身', '瓶肩', '瓶颈', '颈环', '滚珠', '托环',
    '螺纹', '直径', '厚度', '高度', '宽度', '圆角', '台阶', '凸缘', '一体式', '弧度',
    '曲率', '锥度', '收肩', '斜切', '顶面', '底面', '轮廓', '外形', '形状', '造型',
    '分段', '分层', '结构', '长宽', '粗细', '腰线', '箱体',
)

def geometry_fact_present(rows) -> bool:
    """True when at least one product fact carries concrete geometry language."""
    for row in rows:
        text = str(row.get('text', '') or '')
        attr = str(row.get('visual_attribute', '') or '')
        haystack = text + ' ' + attr
        if any(facet in haystack for facet in GEOMETRY_FACETS):
            return True
        if any(ch.isdigit() for ch in haystack) and ('比' in haystack or ':' in haystack):
            return True
    return False

def utc(): return datetime.now(timezone.utc).isoformat()
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def load_json(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def dump(path, value):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    # Replace JSON atomically so an interrupted write cannot leave a truncated
    # workflow or request that a later resume might mistake for a new plan.
    tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(p)
def error(msg): raise ValueError(msg)

def classify_copy_risk(text):
    """Classify visible copy without turning ordinary product language into evidence debt."""
    value = ' '.join(str(text or '').split())
    if not value:
        return 'ordinary'
    if any(re.search(pattern, value, re.IGNORECASE) for pattern in COPY_BLOCKED_PATTERNS):
        return 'blocked'
    if any(re.search(pattern, value, re.IGNORECASE) for pattern in COPY_EVIDENCE_PATTERNS):
        return 'evidence_required'
    if any(re.search(pattern, value, re.IGNORECASE) for pattern in COPY_CATEGORY_REVIEW_PATTERNS):
        return 'category_review'
    return 'ordinary'

def validate_copy_compliance(rows, fact_ids, fixture=False):
    """Fail only hard-prohibited or unsupported evidence-gated visible copy.

    Ordinary descriptive copy remains allowed. Category-review phrases are carried
    to the prompt for category-aware review instead of being rejected generically.
    """
    for row in rows:
        visible = row.get('visible_copy', [])
        if not isinstance(visible, list):
            continue
        risk_tiers = []
        for block in visible:
            risk = classify_copy_risk(block)
            risk_tiers.append(risk)
            if risk == 'blocked':
                error(f"{row.get('id')} contains hard-prohibited advertising language; rewrite the visible copy")
            if risk == 'evidence_required':
                bound = set(row.get('primary_claim_ids', [])) | set(row.get('copy_evidence_ids', []))
                if not bound:
                    error(f"{row.get('id')} uses evidence-gated copy without a bound product fact; ordinary descriptive copy remains allowed")
                unknown = bound - set(fact_ids)
                if unknown:
                    error(f"{row.get('id')} binds unknown copy evidence facts: {','.join(sorted(unknown))}")
        row['copy_risk_tiers'] = risk_tiers
def load(ws):
    p=Path(ws)/'artifacts/workflow.json'
    if not p.is_file(): error('workflow.json is missing; run new first')
    return load_json(p)

def validate_generation_gate(ws, request_path):
    """Validate that an imagegen request belongs to a live, audited run."""
    ws=Path(ws).resolve(); req_path=Path(request_path).resolve()
    if ws not in req_path.parents or req_path.parent != ws/'artifacts':
        error('imagegen gate requires a request from the supplied workspace artifacts directory')
    d=load(ws)
    if d.get('state')!='GENERATION_ALLOWED': error('imagegen gate requires persisted GENERATION_ALLOWED state')
    block = ws/'artifacts/generation-block.json'
    if block.is_file():
        try:
            marker = load_json(block)
        except Exception:
            error('imagegen gate found an unreadable generation block; rerun plan audit before generating')
        if marker.get('status') == 'GENERATION_BLOCKED':
            error('imagegen gate found GENERATION_BLOCKED; resolve the recorded plan/audit failure before generating')
    pa=d.get('plan_audit') or {}
    if pa.get('pass') is not True or pa.get('route')!=d.get('route') or pa.get('unit_ids')!=d.get('layout',{}).get('unit_ids'):
        error('imagegen gate requires a passing audit-plan receipt for the current route and unit order')
    req=load_json(req_path)
    key=req.get('group_id') if d.get('route')=='DETAIL_PAGE' else req.get('unit_id')
    pool=d.get('groups',{}) if d.get('route')=='DETAIL_PAGE' else d.get('units',{})
    item=pool.get(key) if key else None
    if not item or item.get('status')!='awaiting_artwork' or req.get('token')!=d.get('active_token') or item.get('generation_token')!=d.get('active_token'):
        error('imagegen gate request is not the active awaiting target; do not create a replacement request')
    if req.get('route')!=d.get('route') or req.get('run_id',d.get('run_id'))!=d.get('run_id'):
        error('imagegen gate request route or run identity does not match the workspace')
    expected=item.get('unit_ids',[key]); actual=req.get('generation_scope',{}).get('unit_ids')
    if actual!=expected or [x.get('id') for x in req.get('current_target_plan',[]) ]!=expected:
        error('imagegen gate request target plan does not match the active route plan')
    return d,req
def save(ws,d):
    dump(Path(ws)/'artifacts/workflow.json',d)
    dump(Path(ws)/'artifacts/style-harness.json',{'state':d['state'],'route':d.get('route'),'screen_count':d.get('screen_count'),'groups':d.get('groups',{}),'units':d.get('units',{}),'revision_index':d.get('revision_index',{}),'artwork':d.get('artwork',[]),'audits':d.get('audits',[])})
    write_recovery_manifest(ws,d)

def write_recovery_manifest(ws,d):
    """Persist the exact in-flight handoff used by resume after a process loss."""
    pool=d.get('groups',{}) if d.get('route')=='DETAIL_PAGE' else d.get('units',{})
    active=[(k,v) for k,v in pool.items() if v.get('status')=='awaiting_artwork']
    if not active:
        p=Path(ws)/'artifacts/recovery-manifest.json'
        if p.exists(): p.unlink()
        return
    key,item=active[0]
    request=Path(ws)/f'artifacts/generation-request-{key}.json'
    prompt=Path(ws)/f'artifacts/imagegen-prompt-{key}.txt'
    manifest={'schema_version':'aurora-recovery-1','run_id':d.get('run_id'),'workspace':str(Path(ws).resolve()),
              'state':d.get('state'),'target_id':key,'token':item.get('generation_token'),
              'request':str(request.resolve()),'prompt':str(prompt.resolve()),
              'request_exists':request.is_file(),'prompt_exists':prompt.is_file(),
              'created_at':item.get('generated_at'),'resume_only':True}
    if request.is_file(): manifest['request_sha256']=sha(request)
    if prompt.is_file(): manifest['prompt_sha256']=sha(prompt)
    dump(Path(ws)/'artifacts/recovery-manifest.json',manifest)
def require(d,*states):
    if d.get('state') not in states: error('requires state '+','.join(states)+', got '+str(d.get('state')))
def require_route(d):
    if d.get('route') not in ROUTES: error('route has not been classified')
def require_trans(d):
    missing=[x for x in TRANSLATION_FIELDS if not d.get(x)]
    if missing: error('translation contract missing: '+','.join(missing))
def new_run(base, products, references, facts=None, count=None, mixed_inputs=None):
    if mixed_inputs is not None:
        mixed=[str(Path(x).resolve()) for x in mixed_inputs]
        if len(mixed)<2: error('mixed input requires at least two images')
        for p in mixed:
            if not Path(p).is_file(): error('input not found: '+p)
        ws=Path(base).resolve()/('run-'+uuid.uuid4().hex[:12]); (ws/'artifacts').mkdir(parents=True)
        inputs=[{'id':f'I{i+1:02}','path':p,'sha256':sha(p),'role':'unassigned'} for i,p in enumerate(mixed)]
        d={'schema_version':'aurora-workflow-3','run_id':ws.name,'state':'INPUT_REGISTERED','route':None,'inputs':inputs,'facts_source':str(Path(facts).resolve()) if facts else None,'screen_count':count,'revision_index':{},'groups':{},'units':{},'artwork':[],'audits':[],'receipts':[],'input_roles_confirmed':False}
        save(ws,d); dump(ws/'artifacts/input-register.json',{'run_id':ws.name,'inputs':inputs,'registered_at':utc(),'role_status':'unassigned'}); dump(ws/'artifacts/audit-ledger.json',{'fixture':[],'manual':[],'visual_model':[],'real_imagegen':[]})
        return ws,d
    products=[str(Path(x).resolve()) for x in (products or [])]; references=[str(Path(x).resolve()) for x in (references or [])]
    if not products or not references: error('one or more product images and one or more reference images are required')
    for p in products+references:
        if not Path(p).is_file(): error('input not found: '+p)
    ws=Path(base).resolve()/('run-'+uuid.uuid4().hex[:12]); (ws/'artifacts').mkdir(parents=True)
    inputs=[{'id':f'P{i+1:02}','path':p,'sha256':sha(p),'role':'product_original'} for i,p in enumerate(products)]
    inputs += [{'id':f'R{i+1:02}','path':p,'sha256':sha(p),'role':'benchmark_reference'} for i,p in enumerate(references)]
    d={'schema_version':'aurora-workflow-3','run_id':ws.name,'state':'INPUT_REGISTERED','route':None,'inputs':inputs,'facts_source':str(Path(facts).resolve()) if facts else None,'screen_count':count,'revision_index':{},'groups':{},'units':{},'artwork':[],'audits':[],'receipts':[],'input_roles_confirmed':True}
    save(ws,d); dump(ws/'artifacts/input-register.json',{'run_id':ws.name,'inputs':inputs,'registered_at':utc()}); dump(ws/'artifacts/audit-ledger.json',{'fixture':[],'manual':[],'visual_model':[],'real_imagegen':[]})
    return ws,d
def receipt(d,cmd,**extra): d.setdefault('receipts',[]).append({'command':cmd,'at':utc(),'state':d['state'],**extra})

def inspect_inputs(d, review_path, ws):
    require(d,'INPUT_REGISTERED')
    if not review_path: error('--role-review is required for mixed inputs')
    review=load_json(review_path)
    if review.get('review_kind') not in ('fixture','manual','visual_model'): error('role review_kind must be fixture, manual, or visual_model')
    rows=review.get('items')
    if not isinstance(rows,list) or len(rows)!=len(d.get('inputs',[])): error('role review must contain exactly one item per input')
    expected={x['id']:x for x in d['inputs']}; seen=set(); products=refs=0
    for row in rows:
        iid=row.get('input_id'); role=row.get('role'); conf=row.get('confidence')
        if iid not in expected or iid in seen: error('role review input IDs must match each registered input exactly once')
        if role not in ('product_original','benchmark_reference'): error('role must be product_original or benchmark_reference')
        if not isinstance(conf,(int,float)) or conf < 0.8: error('ambiguous input role confidence below 0.8 is blocked')
        if not isinstance(row.get('visual_observation'),str) or len(row['visual_observation'].strip())<8: error('each role needs a visual observation')
        if not isinstance(row.get('evidence'),list) or not row['evidence']: error('each role needs visual evidence')
        if row.get('sha256') != expected[iid]['sha256']: error('role review SHA-256 does not match registered input')
        expected[iid]['role']=role; expected[iid]['role_confidence']=conf; expected[iid]['role_observation']=row['visual_observation']; expected[iid]['role_evidence']=row['evidence']; seen.add(iid)
        products += role=='product_original'; refs += role=='benchmark_reference'
    if products<1 or refs<1: error('role review must identify at least one product and one benchmark reference')
    d['input_roles_confirmed']=True; d['input_role_review']={'review_kind':review['review_kind'],'path':str(Path(review_path).resolve()),'sha256':sha(review_path),'items':rows}; dump(ws/'artifacts/input-register.json',{'run_id':d['run_id'],'inputs':d['inputs'],'role_status':'confirmed','role_review':d['input_role_review']}); return d

def classify(d, route, count=None, evidence=None):
    require(d,'INPUT_REGISTERED')
    if not d.get('input_roles_confirmed') or any(x.get('role')=='unassigned' for x in d.get('inputs',[])): error('inspect mixed inputs and confirm product/reference roles before route classification')
    if route not in ROUTES: error('unsupported route')
    count = count if count is not None else d.get('screen_count')
    try: c=layout(route,count,evidence)
    except ValueError as e: error(str(e))
    d.update(route=route,canonical_route=canonical(route),screen_count=c['count'],layout=c,state='ROUTE_CLASSIFIED')
    d['count_explicit']=count is not None
    d['count_source']='user' if count is not None else 'route_default_pending_reference_analysis'
    d['route_evidence']=evidence or 'caller selected route; review input roles before proceeding'
    return d

def compile_crops(d, analysis_path, ws):
    require(d,'ROUTE_CLASSIFIED','REFERENCE_DECOMPOSED'); require_route(d)
    if not analysis_path: error('--analysis is required; empty placeholder crops are forbidden')
    a=load_json(analysis_path); units=a.get('units',[])
    if not isinstance(units,list) or len(units)<1: error('analysis.units must contain physical reference units')
    panels=validate_panels(d,a); overview=a['overview_detection']
    unit_ids={u.get('id') for u in units}
    if len(unit_ids)!=len(units) or None in unit_ids: error('sanitized crop IDs must be unique')
    benchmark_ids={x['id'] for x in d['inputs'] if x['role']=='benchmark_reference'}
    for u in units:
        for k in ('id','reference_input_id','source_crop_path','source_role','source_semantics'):
            if not u.get(k): error('reference unit missing '+k)
        if u['source_role'] not in ('style_mechanism_only','layout_only','color_only','human_action_only'): error('invalid style-only source role')
        if u['reference_input_id'] not in benchmark_ids: error('reference unit must cite a confirmed benchmark input')
        p=Path(u['source_crop_path']).resolve()
        if not p.is_file(): error('source crop not found: '+str(p))
        flags=('source_brand_presence','source_packaging_presence','source_logo_presence','source_sku_presence','competitor_presence','source_copy_presence','source_environment_presence')
        if any(u.get(flag) is not False for flag in flags):
            error('reference crop contains forbidden identity/copy/environment; sanitize and re-submit')
        if u.get('review_status') not in ('fixture','manual','visual_model'): error('each crop needs fixture/manual/visual_model review_status')
        if u.get('review_status') != 'fixture' and u.get('source_product_presence') is not False:
            error('non-fixture style crops require explicit source_product_presence=false; remove benchmark product pixels before review')
        ref_paths={x['path'] for x in d['inputs'] if x['role']=='benchmark_reference'}
        if str(p) in ref_paths: error('full benchmark image cannot be promoted as a crop')
        if sha(p) in {x['sha256'] for x in d['inputs'] if x['role']=='benchmark_reference'}: error('crop bytes equal a full benchmark; create a sanitized crop')
        if str(p) in {str(Path(x['source_crop_path']).resolve()) for x in panels} or sha(p) in {x['sha256'] for x in panels}: error('analysis-only full panels cannot be passed as sanitized style crops')
        from PIL import Image
        with Image.open(p) as image: image.verify()
        u['sha256']=sha(p); u['path']=str(p); u['sanitized']=True
    if {u['reference_input_id'] for u in units} != benchmark_ids: error('style crop analysis must cover every benchmark reference')
    if d['route']=='CAROUSEL_SET' and len(benchmark_ids)>1:
        covered={u['reference_input_id'] for u in units}
        if covered != benchmark_ids: error('carousel analysis must cover every benchmark reference image')
        orders=[u.get('source_order') for u in units]
        if any(type(x) is not int for x in orders) or sorted(set(orders)) != list(range(1,len(set(orders))+1)): error('carousel benchmark source_order must be contiguous and explicit')
        d['benchmark_sequence']=[{'source_order':u['source_order'],'reference_input_id':u['reference_input_id'],'unit_id':u['id']} for u in sorted(units,key=lambda x:x['source_order'])]
    m={'schema_version':'aurora-reference-crops-3','route':d['route'],'style_only':True,'source_full_image_forbidden':True,'source_environment_forbidden':True,'overview_detection':overview,'units':units,'record_count':len(units)}
    dump(ws/'artifacts/reference-crops/manifest.json',m); d['reference_crops']=m; d['state']='REFERENCE_DECOMPOSED'; return d

def passport(d, analysis_path):
    require(d,'REFERENCE_DECOMPOSED'); a=load_json(analysis_path); mechanisms=a.get('mechanisms')
    validate_mechanisms(mechanisms,{u['id'] for u in d['reference_crops']['units']})
    if a.get('overview_detection') != d['reference_crops']['overview_detection']: error('panel analysis changed; rerun compile-reference-crops before Passport')
    panels=validate_panels(d,a)
    narrative=a.get('narrative_logic')
    if not isinstance(narrative,dict) or not isinstance(narrative.get('sequence'),list) or not narrative['sequence']: error('passport requires ordered reference narrative_logic')
    for step in narrative['sequence']:
        if not step.get('id') or not step.get('role') or not step.get('description') or not isinstance(step.get('source_reference_ids'),list) or not step['source_reference_ids']: error('each narrative step needs id, role, description and source_reference_ids')
    benchmark_ids={x['id'] for x in d['inputs'] if x['role']=='benchmark_reference'}
    cited={rid for step in narrative['sequence'] for rid in step['source_reference_ids']}
    if not cited.issubset(benchmark_ids): error('narrative logic cites an unregistered benchmark input')
    if len(benchmark_ids)>1 and cited != benchmark_ids: error('narrative logic must cover every benchmark reference image')
    for k in ('opening','proof_progression','takeaway','handoff'):
        if not isinstance(narrative.get(k),str) or not narrative[k].strip(): error('narrative_logic requires '+k)
    content=a.get('source_content_analysis')
    if not isinstance(content,dict) or not isinstance(content.get('set_summary'),str) or not content['set_summary'].strip() or not isinstance(content.get('sequence'),list) or not content['sequence']: error('passport requires complete source_content_analysis sequence')
    benchmark_ids={x['id'] for x in d['inputs'] if x['role']=='benchmark_reference'}; cited=set(); orders=[]
    content_is_fixture = all(u.get('review_status') == 'fixture' for u in d['reference_crops']['units'])
    for step in content['sequence']:
        if not step.get('id') or not isinstance(step.get('source_reference_ids'),list) or not step['source_reference_ids'] or not step.get('content_role') or not step.get('normalized_message') or not step.get('handoff'): error('each content sequence step needs references, content_role, normalized_message and handoff')
        if not content_is_fixture:
            if not isinstance(step.get('opening_function'), str) or step['opening_function'].strip() not in ('result_promise','problem_awareness','product_first','proof_first','other'):
                error('each content sequence step needs opening_function: result_promise, problem_awareness, product_first, proof_first or other; classify the observed opening function before writing target copy')
            if not all(isinstance(step.get(k), str) and step[k].strip() for k in ('conversion_stage','buyer_question','belief_shift','persuasion_job')):
                error('each content sequence step needs conversion_stage, buyer_question, belief_shift and persuasion_job; describe what the reference makes the buyer understand')
            for k in ('evidence_obligations','expression_actions'):
                if not isinstance(step.get(k), list) or not step[k] or not all(isinstance(v, str) and v.strip() for v in step[k]):
                    error(f'each content sequence step needs a non-empty {k} list; record proof duties and visible expression actions')
        if not isinstance(step.get('verbatim_text'),list): error('each content sequence step needs verbatim_text list, even when empty')
        if not set(step['source_reference_ids']).issubset(benchmark_ids): error('content sequence cites an unregistered benchmark input')
        cited.update(step['source_reference_ids']); orders.append(step.get('source_order',len(orders)+1))
    if len(benchmark_ids)>1 and cited != benchmark_ids: error('source content analysis must cover every benchmark reference image')
    if orders!=list(range(1,len(orders)+1)): error('source content sequence order must be contiguous and stored in reading order')
    if all(u.get('review_status') == 'fixture' for u in d['reference_crops']['units']):
        fixture_defaults(content)
    else:
        content.pop('fixture_compatibility', None)
        validate_sequence_method_distinction(content['sequence'])
    validate_content_panels(content,panels)
    d['reference_style_mechanisms']=mechanisms; d['visual_fidelity_contract']={'required_mechanisms':list(MECHANISMS),'style_signature':{k:mechanisms[k]['observation'] for k in MECHANISMS},'match_priority':['typography','composition','hierarchy','carrier','density','material','light_depth','reading_path'],'literal_source_forbidden':True}; d['reference_narrative_logic']=narrative; d['reference_content_analysis']=content; d['overview_detection']=a.get('overview_detection'); d['source_semantic_inventory']=a.get('source_semantics',[])
    if d.get('route')=='CAROUSEL_SET' and not d.get('count_explicit'):
        inferred=max(3,len(content['sequence']))
        if inferred != d['layout']['count']:
            d['layout']=layout('CAROUSEL_SET',inferred,'derived from reference content sequence'); d['screen_count']=inferred
        d['count_source']='reference_content_analysis'; d['count_evidence']='inferred from ordered source_content_analysis.sequence'
    if d.get('benchmark_sequence') is None and isinstance(a.get('benchmark_sequence'),list): d['benchmark_sequence']=a['benchmark_sequence']
    d['state']='VISUAL_PASSPORT_READY'; return d

def facts(d, facts_path):
    require(d,'VISUAL_PASSPORT_READY');
    if not facts_path: error('--facts is required; product facts cannot be invented')
    f=load_json(facts_path); rows=f.get('facts',[])
    if not isinstance(rows,list) or not rows: error('facts must contain at least one product fact')
    product_ids={x['id'] for x in d['inputs'] if x['role']=='product_original'}
    claims=[]
    product_input_ids=sorted(product_ids)
    inventory=f.get('evidence_inventory')
    validate_evidence_inventory(product_input_ids, rows, inventory, fixture=all(u.get('review_status') == 'fixture' for u in d.get('reference_crops', {}).get('units', [])))
    for row in rows:
        if not row.get('id') or not row.get('text') or row.get('status') not in ('user_confirmed','confirmed','image_inferred','needs_review'): error('invalid product fact')
        if not set(row.get('evidence_input_ids',[])).issubset(product_ids) and row.get('status')!='user_confirmed': error('fact evidence must cite current product images')
        if row.get('status') in ('image_inferred','needs_review') and row.get('claim_type') in ('medical','safety','certification','performance'): error('inferred safety/performance facts are blocked')
        if row.get('claim_type') in ('medical','safety','certification','performance') and row.get('status')!='user_confirmed': error('safety/performance/certification claims require user confirmation')
        for claim in row.get('claims',[]):
            if not claim.get('id') or not claim.get('owner') or not claim.get('authorized'): error('claim requires id, owner and authorized=true')
            claims.append(claim)
    if not geometry_fact_present(rows):
        error('product facts lack concrete geometry: at least one fact must describe the real product form (截面形状/宽高比/结构分段), e.g. "圆角矩形截面 正面宽:总高约1:1.9 盖与瓶身同宽接缝". Efficacy-only facts make the model fall back to a generic product.')
    d['product_visual_facts']=rows; d['priority_fact_ids']=[]; d['evidence_inventory']=inventory or []; d['claims']=claims; d['product_category']=f.get('product_category'); d['product_palette_contract']=f.get('palette_contract') or {'source_fact_ids':[x['id'] for x in rows if x.get('visual_attribute')=='color'],'rebuild_from_product':True}; d['product_identity_lock']={'locked':True,'product_input_ids':sorted(product_ids)}; d['state']='PRODUCT_FACTS_READY'; return d

def validate_evidence_inventory(product_input_ids, rows, inventory, fixture=False):
    if fixture or len(product_input_ids) <= 1:
        return
    if (not isinstance(inventory, list) or len(inventory) != len(product_input_ids)
            or not all(isinstance(x, dict) and isinstance(x.get('kind'), str) and x['kind'].strip() for x in inventory)
            or {x.get('input_id') for x in inventory} != set(product_input_ids)):
        error('multiple product inputs require evidence_inventory with one row per product input; inspect patents, certificates and product-information images before planning')
    fact_ids={row.get('id') for row in rows}
    for item in inventory:
        observed=item.get('observed_fact_ids', [])
        if observed is None:
            observed=[]
        if not isinstance(observed, list) or not set(observed).issubset(fact_ids):
            error('evidence_inventory observed_fact_ids must reference known product facts when supplied')

def validate_internal_copy_labels(rows):
    """Keep planning-only labels out of rendered copy without dictating benchmark content."""
    for row in rows:
        copy = tuple(' '.join(str(x).split()).casefold() for x in row.get('visible_copy', []))
        for block in copy:
            if re.match(r'^(?:(?:卖点|产品|全页|本页)\s*)?(?:收束|总结|结尾)(?:页|屏|阶段)?(?:$|[\s：:，,。！!])', block):
                error(f"{row.get('id')} renders an internal closing label; write natural reference-derived product copy")

def validate_sequence_method_distinction(sequence):
    """Reject a long non-fixture sequence whose method fields were flattened."""
    def has_replacement(value):
        if isinstance(value, str):
            return '\ufffd' in value
        if isinstance(value, list):
            return any(has_replacement(item) for item in value)
        return False
    for step in sequence:
        if any(has_replacement(step.get(key)) for key in (
                'verbatim_text', 'normalized_message', 'handoff', 'conversion_stage',
                'buyer_question', 'persuasion_job', 'belief_shift',
                'evidence_obligations', 'expression_actions')):
            error(f"non-fixture source step {step.get('id')} contains Unicode replacement characters; re-read the panel as UTF-8 and record unreadable regions explicitly")
    signatures = {}
    messages = {}
    for step in sequence:
        signature = (
            step.get('conversion_stage'), step.get('buyer_question'),
            step.get('persuasion_job'), step.get('belief_shift'),
            tuple(step.get('evidence_obligations') or []),
            tuple(step.get('expression_actions') or []),
        )
        signatures.setdefault(signature, []).append(step)
        messages.setdefault(signature, set()).add((step.get('content_role'), step.get('normalized_message')))
    for signature, rows in signatures.items():
        if len(rows) < 4 or len(messages[signature]) < 4:
            continue
        ids = ', '.join(str(x.get('id')) for x in rows[:6])
        error('non-fixture source sequence flattens distinct panels into one repeated method template; '
              f'write panel-specific persuasion/evidence/expression fields for {ids}')

def target_reference_bindings(d, crop_manifest, target_rows):
    """Return only the ordered reference crops used by the current target."""
    steps = {x['id']: x for x in d.get('reference_content_analysis', {}).get('sequence', [])}
    wanted = []
    for row in target_rows:
        for sid in row.get('source_content_step_ids', []):
            step = steps.get(sid)
            if not step:
                error(f'current target cites unknown source step {sid}')
            for rid in step.get('source_reference_ids', []):
                wanted.append((step.get('source_order', len(wanted) + 1), rid, sid))
    if not wanted:
        error('current target has no ordered benchmark reference bindings')
    nonfixture = not all(u.get('review_status') == 'fixture' for u in crop_manifest.get('units', []))
    by_ref = {}
    for unit in crop_manifest.get('units', []):
        if nonfixture and not isinstance(unit.get('source_content_step_ids'), list):
            error('non-fixture sanitized crops must bind to their observed source_content_step_ids; generic mechanism crops cannot drive a target Pair')
        by_ref.setdefault(unit.get('reference_input_id'), []).append(unit)
    result = []
    seen = set()
    for order, rid, sid in wanted:
        candidates = by_ref.get(rid, [])
        if nonfixture:
            candidates = [unit for unit in candidates if sid in unit.get('source_content_step_ids', [])]
        for unit in candidates:
            key = (unit.get('path'), rid)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                'path': unit['path'], 'sha256': unit['sha256'],
                'role': 'style_mechanism_only',
                'reference_input_id': rid, 'source_content_step_id': sid,
                'source_order': order,
            })
    if not result:
        error('current target benchmark references have no sanitized crops')
    if nonfixture:
        bound_steps = {x.get('source_content_step_id') for x in result}
        wanted_steps = {sid for _, _, sid in wanted}
        if not wanted_steps.issubset(bound_steps):
            missing = ','.join(sorted(wanted_steps - bound_steps))
            error(f'current target is missing sanitized crops for source steps: {missing}')
    return result

def plan(d, plan_path, ws):
    require(d,'PRODUCT_FACTS_READY','PLAN_AUDIT_REQUIRED'); require_route(d)
    if d.get('artwork') or d.get('active_token'):
        error('replanning is forbidden after a generation token or artwork exists')
    p=load_json(plan_path) if plan_path else {}
    c=d['layout']; ids=c['unit_ids']; rows=p.get('units',[])
    if not isinstance(rows,list) or [x.get('id') for x in rows]!=ids: error('plan units must be ordered exactly '+','.join(ids))
    # Reference-analysis permits source semantics as either stable objects
    # (the preferred ``{"id": ..., "text": ...}`` form) or plain strings.
    # Normalize both forms here so a valid analysis cannot crash planning with
    # ``string indices must be integers``.  Invalid entries still fail closed
    # with an actionable message instead of being silently discarded.
    source_ids=[]
    for index, item in enumerate(d.get('source_semantic_inventory',[]) or [], 1):
        if isinstance(item, str) and item.strip():
            source_ids.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get('id'), str) and item['id'].strip():
            source_ids.append(item['id'].strip())
        else:
            error(f'source_semantic_inventory entry {index} must be a non-empty string or an object with a non-empty id')
    source_ids=set(source_ids); seen=set()
    plan_is_fixture = all(u.get('review_status') == 'fixture' for u in d['reference_crops']['units'])
    detail = d['route'] == 'DETAIL_PAGE'
    source_steps = {x['id']: x for x in d['reference_content_analysis']['sequence']}
    for row in rows:
        if (not row.get('semantic_translation') or not row.get('mechanism_ids')
                or (not detail and not row.get('primary_duty'))):
            error('every target unit needs semantic translation and mechanism_ids; non-detail units also need primary_duty')
        if detail and row.get('primary_duty') is not None and not isinstance(row.get('primary_duty'), str):
            error('detail target primary_duty is an optional product adaptation note and must be text when supplied')
        if not plan_is_fixture and not detail:
            if not isinstance(row.get('source_function'), str) or not row['source_function'].strip():
                error('every target unit needs source_function; preserve the observed opening/content function instead of inferring it from product facts')
            if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('buyer_question','answer','belief_shift','handoff_question')):
                error('every target unit needs buyer_question, answer, belief_shift and handoff_question; map the screen to a buyer decision')
            if not isinstance(row.get('required_visual_roles'), list) or not row['required_visual_roles'] or not all(isinstance(v, str) and v.strip() for v in row['required_visual_roles']):
                error('every target unit needs non-empty required_visual_roles; specify the proof or expression object the screen must show')
            if not isinstance(row.get('evidence_bindings'), list) or not row['evidence_bindings']:
                error('every target unit needs non-empty evidence_bindings; bind claims/signals to source steps and a visual form')
            for binding in row['evidence_bindings']:
                if not isinstance(binding, dict) or not binding.get('source_step_ids') or not binding.get('visual_form') or not binding.get('claim_scope'):
                    error('each evidence_binding needs source_step_ids, visual_form and claim_scope; never reduce evidence to a generic certificate display')
        if len(set(row.get('primary_claim_ids',[])))!=len(row.get('primary_claim_ids',[])): error('duplicate primary claim in unit')
        tr=row['semantic_translation']
        if not isinstance(tr, dict) or not tr:
            error(f"{row['id']} semantic_translation must be an object keyed by source semantic id; do not flatten it into product_facts/translation fields")
        invalid_entries=[key for key, value in tr.items() if not isinstance(value, dict)]
        if invalid_entries:
            error(f"{row['id']} semantic_translation entries must be objects with target_world and retain_literal_source (invalid: {','.join(map(str, invalid_entries))})")
        seen.update(tr.keys())
        if detail:
            # Detail screens inherit narrative role from the mapped physical
            # reference steps.  Callers may add a product adaptation note, but
            # they do not have to invent a second screen-by-screen storyline.
            if not row.get('narrative_role'):
                roles = [source_steps[sid].get('content_role', '').strip()
                         for sid in row.get('source_content_step_ids', [])
                         if isinstance(source_steps[sid].get('content_role'), str)
                         and source_steps[sid].get('content_role', '').strip()]
                row['narrative_role'] = '; '.join(dict.fromkeys(roles)) or 'source_translation'
            if not row.get('narrative_step_ids'):
                row['narrative_step_ids'] = []
        elif not row.get('narrative_role') or not row.get('narrative_step_ids'):
            error('every target unit needs narrative_role and narrative_step_ids')
        narrative_ids={x['id'] for x in d['reference_narrative_logic']['sequence']}
        if not set(row.get('narrative_step_ids', [])).issubset(narrative_ids): error('target unit cites unknown narrative step')
        content_ids={x['id'] for x in d['reference_content_analysis']['sequence']}
        if not row.get('source_content_step_ids') or not set(row['source_content_step_ids']).issubset(content_ids): error('every target unit needs source_content_step_ids')
        if not plan_is_fixture:
            for sid in row['source_content_step_ids']:
                observed = source_steps[sid].get('opening_function')
                if observed and row.get('source_function') and row.get('source_function') != observed:
                    error(f"{row['id']} source_function must preserve {sid}'s observed opening/content function ({observed})")
            if detail and not row.get('source_function'):
                observed = {source_steps[sid].get('opening_function') for sid in row['source_content_step_ids']
                            if source_steps[sid].get('opening_function')}
                if len(observed) == 1:
                    row['source_function'] = next(iter(observed))
        if any(not v.get('target_world') or v.get('retain_literal_source') for v in tr.values()): error('semantic translation must replace source world and cannot retain literal source')
    if source_ids and not source_ids.issubset(seen):
        missing=','.join(sorted(source_ids-seen))
        error(f'every source semantic must be explicitly translated; missing: {missing}')
    if detail:
        validate_internal_copy_labels(rows)
    validate_copy_compliance(rows, {x.get('id') for x in d.get('product_visual_facts', [])}, fixture=plan_is_fixture)
    content_ids={x['id'] for x in d['reference_content_analysis']['sequence']}
    if {sid for row in rows for sid in row['source_content_step_ids']} != content_ids: error('target plan must cover every source content step for every route')
    if not plan_is_fixture:
        # Matching source_function per row does not prevent a later pain/proof
        # step from being moved ahead of the observed opening hero. Preserve
        # the benchmark's physical reading order at the target entry point.
        source_order = {step['id']: step.get('source_order', index + 1)
                        for index, step in enumerate(d['reference_content_analysis']['sequence'])}
        mapped_orders = [source_order[sid]
                         for row in rows
                         for sid in row.get('source_content_step_ids', [])]
        if not mapped_orders or mapped_orders[0] != 1:
            error('target plan must open with the first observed source content step; preserve the benchmark opening function')
        if mapped_orders != sorted(mapped_orders):
            error('target plan source content steps must follow benchmark reading order; do not move a later pain/proof step ahead of the observed opening')
    fixture_mode = all(u.get('review_status') == 'fixture' for u in d['reference_crops']['units'])
    if fixture_mode:
        fixture_plan_defaults(d['reference_content_analysis'], rows)
    rows = compile_target_rhythm(d['reference_content_analysis'], rows)
    d['units']={x['id']:{**x,'status':'pending','revision_index':0} for x in rows}; d['groups']={g['id']:{'unit_ids':g['unit_ids'],'status':'pending','revision_index':0} for g in c['jobs']} if d['route']=='DETAIL_PAGE' else {}
    d['semantic_translation_map']={x['id']:x['semantic_translation'] for x in rows}; d['route_specific_composition_plan']={'route':d['route'],'contract':c,'units':rows}; d['forbidden_literal_inheritance']=['source_world','source_brand','source_packaging','source_logo','source_sku','source_copy','competitor']
    if d['route']=='CAROUSEL_SET' and len({x['id'] for x in d['inputs'] if x['role']=='benchmark_reference'})>1:
        for row in rows:
            if not row.get('source_reference_ids') or not row.get('narrative_role'): error('multi-reference carousel units need source_reference_ids and narrative_role')
        expected_refs=[x['reference_input_id'] for x in sorted(d.get('benchmark_sequence',[]),key=lambda x:x['source_order'])]
        actual_refs=[rid for row in rows for rid in row.get('source_reference_ids',[])]
        if actual_refs != expected_refs: error('carousel unit order must follow the complete benchmark source sequence')
    if d['route']=='CAROUSEL_SET':
        covered_steps={sid for row in rows for sid in row.get('source_content_step_ids',[])}
        if covered_steps != {x['id'] for x in d['reference_content_analysis']['sequence']}: error('carousel target plan must cover every source content step')
    dump(ws/'artifacts/route-plan.json',{'route':d['route'],'layout':c,'units':rows,'benchmark_sequence':d.get('benchmark_sequence',[]),'reference_narrative_logic':d['reference_narrative_logic'],'reference_content_analysis':d['reference_content_analysis'],'visual_fidelity_contract':d['visual_fidelity_contract'],'reference_style_mechanisms':d['reference_style_mechanisms'],'product_visual_facts':d['product_visual_facts'],'priority_fact_ids':d.get('priority_fact_ids',[]),'evidence_inventory':d.get('evidence_inventory',[]),'product_category':d.get('product_category'),'copy_risk_policy':COPY_RISK_POLICY,'product_palette_contract':d['product_palette_contract'],'semantic_translation_map':d['semantic_translation_map'],'route_specific_composition_plan':d['route_specific_composition_plan'],'forbidden_literal_inheritance':d['forbidden_literal_inheritance']}); (Path(ws)/'artifacts/generation-block.json').unlink(missing_ok=True); d['state']='PLAN_AUDIT_REQUIRED'; return d

def audit_plan(d, audit_path):
    require(d,'PLAN_AUDIT_REQUIRED'); a=load_json(audit_path) if audit_path else {}
    if a.get('pass') is not True or a.get('review_kind') not in ('fixture','manual','visual_model'): error('plan audit requires explicit passing fixture/manual/visual_model review')
    if a.get('route') != d['route'] or a.get('unit_ids') != d['layout']['unit_ids']: error('plan audit route/order mismatch')
    d['plan_audit']=a; d['state']='GENERATION_ALLOWED'; return d

def generation(d, key, ws):
    require(d,'GENERATION_ALLOWED','ACCEPTED','GROUP_FAILED_CONTINUE'); require_route(d); detail=d['route']=='DETAIL_PAGE'; pool=d['groups'] if detail else d['units']
    require_trans(d)
    if key not in pool: error('unknown group/unit '+str(key))
    active=[k for k,v in pool.items() if v.get('status') in ('awaiting_artwork','audit_required','revision_allowed')]
    if active: error('finish active target '+active[0]+' before another request; resume preserves the existing generation token')
    item=pool[key]
    if item.get('status') in ('accepted','failed_continue'): error('group/unit is closed')
    pending=[k for k,v in pool.items() if v.get('status') in ('pending','revision_allowed')]
    if pending and key != pending[0]: error('generation must follow declared order')
    block = Path(ws)/'artifacts/generation-block.json'
    if block.is_file():
        try:
            marker = load_json(block)
        except Exception:
            error('generation blocked by an unreadable generation-block marker; rerun plan audit')
        if marker.get('status') == 'GENERATION_BLOCKED':
            error('generation blocked by the recorded plan/audit failure; do not bypass audit-plan')
    crop_manifest=Path(ws)/'artifacts/reference-crops/manifest.json'; m=load_json(crop_manifest); 
    if not m.get('style_only') or not m.get('units'): error('sanitized style-only crops are required')
    for crop in m['units']:
        if crop.get('source_environment_presence') or crop.get('source_brand_presence') or crop.get('source_copy_presence') or crop.get('source_product_presence'): error('source semantic leakage in crop manifest')
        if not Path(crop['path']).is_file() or sha(crop['path'])!=crop['sha256']: error('sanitized crop changed; generation is blocked')
    for source in d['inputs']:
        if not Path(source['path']).is_file() or sha(source['path'])!=source['sha256']: error('registered input changed; generation is blocked')
    validate_panels(d,{'overview_detection':d['overview_detection']})
    token=uuid.uuid4().hex; item.update(status='awaiting_artwork',generation_token=token,generated_at=utc()); d['active_token']=token
    # Commit the token before any external handoff. If the process dies while
    # building the request, resume sees the same in-flight target and refuses
    # to issue a replacement token.
    save(ws,d)
    product_paths=[x['path'] for x in d['inputs'] if x['role']=='product_original']
    output_contract=d['layout']['jobs'][int(key[1:])-1] if detail else d['layout']['jobs'][int(key[1:])-1]
    render_contract={'native_generation_size':output_contract['canvas'],'quality':'high','text_and_image_single_pass':True,'text_rendering':'native_model_rendered','allow_post_text_overlay':False,'allow_low_resolution_upscale':False,'native_output_required':True,'postprocess_allowed':False,'provenance_required_for_nonfixture':True}
    if detail:
        from route_contracts import DETAIL_PAIR_ASPECT_RATIO, DETAIL_PAIR_ASPECT_TOLERANCE
        render_contract['aspect_ratio']={'width':DETAIL_PAIR_ASPECT_RATIO[0],'height':DETAIL_PAIR_ASPECT_RATIO[1], 'tolerance':DETAIL_PAIR_ASPECT_TOLERANCE}
        render_contract['clarity_guidance']='use the clearest native output available; never resize or sharpen a smaller file to satisfy delivery'
    else:
        render_contract['minimum_accepted_dimensions']=output_contract['canvas']
    target_rows=[d['units'][sid] for sid in output_contract['unit_ids']]
    ref_bindings=target_reference_bindings(d,m,target_rows)
    req={'schema_version':'aurora-generation-request-3','run_id':d.get('run_id'),'token':token,'route':d['route'],'canonical_route':canonical(d['route']),'group_id':key if detail else None,'unit_id':None if detail else key,'revision_index':item.get('revision_index',0),'detail_ratio':d.get('detail_ratio') if detail else None,'product_image_bindings':[{'path':p,'sha256':sha(p),'role':'product_identity'} for p in product_paths],'sanitized_style_crop_bindings':ref_bindings,'overview_detection':d.get('overview_detection'),'benchmark_sequence':d.get('benchmark_sequence',[]),'reference_narrative_logic':d['reference_narrative_logic'],'reference_content_analysis':d['reference_content_analysis'],'visual_fidelity_contract':d['visual_fidelity_contract'],'reference_style_mechanisms':d['reference_style_mechanisms'],'product_visual_facts':d['product_visual_facts'],'priority_fact_ids':d.get('priority_fact_ids',[]),'evidence_inventory':d.get('evidence_inventory',[]),'product_category':d.get('product_category'),'copy_risk_policy':COPY_RISK_POLICY,'product_palette_contract':d['product_palette_contract'],'semantic_translation_map':d['semantic_translation_map'],'route_specific_composition_plan':d['route_specific_composition_plan'],'forbidden_literal_inheritance':d['forbidden_literal_inheritance'],'render_contract':render_contract,'prompt_contract':{'preserve':'the exact reference visual grammar and content architecture: typography morphology, composition rhythm, hierarchy, carriers, density, material, light/depth, reading path and narrative progression','rebuild':'product world, palette, environment, props, copy and composition around confirmed product facts','product_only_subject':True,'single_pass_text_and_image':True,'exact_text_must_be_rendered_by_model':True,'no_post_text_overlay':True,'do_not_substitute_generic_ecommerce_style':True,'do_not_copy_source_text_or_claims':True,'forbidden_decorative_label_forms':list(FORBIDDEN_DECORATIVE_LABEL_FORMS),'reference_derived_density_and_style':True,'ban_unconfirmed_product_marks':True,'forbidden_typography_forms':list(FORBIDDEN_TYPOGRAPHY_FORMS)},'output_contract':output_contract}
    from route_contracts import generation_scope
    req['generation_scope']=generation_scope(d['route'],output_contract)
    req['current_target_plan']=target_rows
    req['target_reference_sequence']=[{
        'source_order': item['source_order'],
        'reference_input_id': item['reference_input_id'],
        'source_content_step_id': item['source_content_step_id'],
    } for item in ref_bindings]
    from generation_handoff import build_continuity_contract
    continuity=build_continuity_contract(req,req['current_target_plan'],req['generation_scope'])
    if continuity:
        req['pair_continuity_contract']=continuity
    req['target_transition_plan']=current_transitions([d['units'][sid] for sid in d['layout']['unit_ids']], output_contract['unit_ids'])
    req['prompt_contract']['render_only_current_target_plan']=True
    dump(ws/f'artifacts/generation-request-{key}.json',req)
    from generation_handoff import write_handoff
    write_handoff(req,ws/f'artifacts/imagegen-prompt-{key}.txt')
    # Refresh the manifest with request/prompt hashes only after both files are
    # complete and atomically replaced.
    save(ws,d)
    d['state']='GENERATION_ALLOWED'; return d

def record(d, key, artwork, ws, render_receipt_path=None):
    require(d,'GENERATION_ALLOWED'); detail=d['route']=='DETAIL_PAGE'; pool=d['groups'] if detail else d['units']; item=pool.get(key)
    if not item or item.get('status')!='awaiting_artwork' or not d.get('active_token') or item.get('generation_token')!=d.get('active_token'): error('generation token does not match target')
    p=Path(artwork).resolve() if artwork else None
    if not p or not p.is_file(): error('--artwork must be an existing raster file')
    try:
        from PIL import Image
        with Image.open(p) as im: dims=list(im.size)
    except Exception as e: error('artwork is unreadable: '+str(e))
    expected=d['layout']['jobs'][int(key[1:])-1]['canvas']
    adopted_ratio=None
    if detail:
        count=len(d['layout']['jobs'][int(key[1:])-1]['unit_ids'])
        if dims[1] % count: error('detail master must split into equal native screens')
        screen_h=dims[1]//count
        from route_contracts import DETAIL_PAIR_ASPECT_RATIO, DETAIL_PAIR_ASPECT_TOLERANCE
        expected_ratio=DETAIL_PAIR_ASPECT_RATIO[1]/DETAIL_PAIR_ASPECT_RATIO[0] if count == 2 else 4/3
        actual_ratio=dims[1]/dims[0]
        if abs(actual_ratio-expected_ratio)>DETAIL_PAIR_ASPECT_TOLERANCE:
            error('detail master must use the requested 3:8 native Pair ratio')
        if key == 'G01':
            adopted_ratio={'width':dims[0],'screen_height':screen_h,'ratio':screen_h/dims[0]}
        else:
            baseline=d.get('detail_ratio') or {'ratio':expected_ratio}
            if abs((screen_h/dims[0])-baseline['ratio'])>DETAIL_PAIR_ASPECT_TOLERANCE:
                error('detail master must keep the 3:8 Pair ratio '+str(baseline))
    if not detail and dims != d['layout']['jobs'][int(key[1:])-1].get('canvas',dims): error('artwork dimensions do not match route contract')
    rec={'route':d['route'],'target_id':key,'token':d['active_token'],'path':str(p),'sha256':sha(p),'dimensions':{'width':dims[0],'height':dims[1]},'revision_index':item.get('revision_index',0),'recorded_at':utc()}
    if render_receipt_path:
        rp=load_json(render_receipt_path)
        from render_provenance import validate_receipt
        validate_receipt(rp,p,None if detail else expected)
        rec['render_provenance']={**rp,'receipt_path':str(Path(render_receipt_path).resolve()),'receipt_sha256':sha(render_receipt_path)}
    if detail:
        if adopted_ratio:
            d['detail_ratio']=adopted_ratio
            for job in d['layout']['jobs']: job['canvas']=[dims[0],screen_h*len(job['unit_ids'])]
        d['layout']['jobs'][int(key[1:])-1]['canvas']=dims
        if d.get('route_specific_composition_plan'): d['route_specific_composition_plan']['contract']=d['layout']
    d['artwork'].append(rec); item['artwork']=rec; item['render_provenance']=rec.get('render_provenance'); item['status']='audit_required'; d['state']='AUDIT_REQUIRED'; return d

def audit(d,key,audit_path,ws):
    require(d,'AUDIT_REQUIRED','RE_AUDIT_REQUIRED'); detail=d['route']=='DETAIL_PAGE'; pool=d['groups'] if detail else d['units']; item=pool.get(key)
    if not item or item.get('status')!='audit_required' or not item.get('artwork'): error('record artwork for the active target before audit')
    if sha(item['artwork']['path'])!=item['artwork']['sha256']: error('artwork changed after recording')
    a=load_json(audit_path) if audit_path else {}
    if a.get('review_kind') not in ('fixture','manual','visual_model','real_imagegen'): error('audit review_kind is required')
    dimensions=a.get('dimensions',{}); required=audit_dimensions([d['route']]); missing=[x for x in required if dimensions.get(x) is not True]
    prior_nonfixture_failure=any(x.get('target_id')==key and x.get('review_kind')!='fixture' and x.get('pass') is False for x in d.get('audits',[]))
    rhythm_ok = (a.get('review_kind') == 'fixture' or
                 validate_rhythm_review(a, [d['units'][sid] for sid in item.get('unit_ids', [key])],
                                        current_transitions([d['units'][sid] for sid in d['layout']['unit_ids']], item.get('unit_ids', [key]))))
    passed=a.get('pass') is True and rhythm_ok and not missing and not any(a.get(k) is True for k in CONTAMINANTS) and a.get('product_identity') is True and a.get('fact_safety') is True
    if a['review_kind']!='fixture':
        mechanism_scores=a.get('mechanism_scores',{}); rp=item.get('render_provenance') or {}
        from render_provenance import validate_receipt
        try:
            validate_receipt(rp,item['artwork']['path'],[item['artwork']['dimensions']['width'],item['artwork']['dimensions']['height']])
            native_ok=True
        except (ValueError,OSError,TypeError):
            native_ok=False
        passed=passed and rhythm_ok and isinstance(a.get('style_fidelity_score'),(int,float)) and a['style_fidelity_score']>=0.8 and isinstance(a.get('product_adaptation_score'),(int,float)) and a['product_adaptation_score']>=0.8 and isinstance(a.get('artwork_sha256'),str) and a['artwork_sha256']==item['artwork']['sha256'] and native_ok and all(isinstance(mechanism_scores.get(k),(int,float)) and mechanism_scores[k]>=0.8 for k in MECHANISMS)
    if a['review_kind']=='fixture' and prior_nonfixture_failure:
        passed=False
    row={'target_id':key,'review_kind':a['review_kind'],'pass':passed,'artwork_sha256':item['artwork']['sha256'],'source_sha256':sha(audit_path),'missing_dimensions':missing,'evidence':a,'at':utc()}; d['audits'].append(row); ledger=load_json(Path(ws)/'artifacts/audit-ledger.json'); ledger.setdefault(a['review_kind'],[]).append(row); dump(Path(ws)/'artifacts/audit-ledger.json',ledger)
    if passed: item['status']='accepted'; d['state']='ACCEPTED'
    elif item.get('revision_index',0)<1: item['status']='revision_allowed'; d['state']='REVISION_ALLOWED'
    else: item['status']='failed_continue'; d['state']='GROUP_FAILED_CONTINUE' if detail else 'FINAL_AUDIT_REQUIRED'
    return d

def revise(d,key):
    require(d,'REVISION_ALLOWED'); pool=d['groups'] if d['route']=='DETAIL_PAGE' else d['units']; item=pool.get(key)
    if not item or item.get('status')!='revision_allowed': error('revision target missing or not eligible')
    item['revision_index']=item.get('revision_index',0)+1
    if item['revision_index']>1: error('revision limit exceeded')
    item.pop('artwork',None); item.pop('render_provenance',None); item.pop('generation_token',None); d.pop('active_token',None); item['status']='pending'; d['state']='GENERATION_ALLOWED'; return d

def global_audit(d, audit_path):
    require(d,'ACCEPTED','GROUP_FAILED_CONTINUE','FINAL_AUDIT_REQUIRED')
    pool=d['groups'] if d['route']=='DETAIL_PAGE' else d['units']
    if any(v.get('status') not in ('accepted','failed_continue') for v in pool.values()): error('every group/unit must be audited before global audit')
    if not audit_path: error('--audit is required for global audit')
    a=load_json(audit_path)
    if a.get('review_kind') not in ('fixture','manual','visual_model','real_imagegen') or a.get('pass') is not True: error('global audit requires an explicit passing review receipt')
    if a.get('ordered_ids') != d['layout']['unit_ids']: error('global audit order does not match route plan')
    d['global_audit']={'status':'passed','review_kind':a['review_kind'],'ordered_ids':a['ordered_ids'],'evidence':a}; d['state']='FINAL_AUDIT_REQUIRED'; return d

def finalize_stage(d, ws):
    require(d,'ACCEPTED','GROUP_FAILED_CONTINUE','FINAL_AUDIT_REQUIRED'); pool=d['groups'] if d['route']=='DETAIL_PAGE' else d['units']
    if any(v.get('status') not in ('accepted','failed_continue') for v in pool.values()): error('every group/unit must be audited before global audit')
    if any(v.get('status')=='failed_continue' for v in pool.values()): error('failed groups/units are barred from strict delivery')
    if d.get('global_audit',{}).get('status')!='passed': error('a passing global audit is required')
    external=d['global_audit'].get('review_kind') in ('manual','visual_model','real_imagegen') and all(
        any(a.get('target_id')==key and a.get('pass') is True and a.get('review_kind') in ('manual','visual_model','real_imagegen')
            and a.get('artwork_sha256')==item['artwork']['sha256'] for a in d['audits']) for key,item in pool.items())
    d['state']='FINALIZED'; dump(Path(ws)/'artifacts/final-manifest.json',{'route':d['route'],'ordered_units':d['layout']['unit_ids'],'groups':d.get('groups',{}),'artwork':d['artwork'],'strict_delivery':True,'visual_quality_status':'externally_reviewed' if external else 'fixture_structure_only'}); return d

def export(d,ws):
    require(d,'FINALIZED')
    pool=d['groups'] if d['route']=='DETAIL_PAGE' else d['units']
    for item in pool.values():
        if sha(item['artwork']['path'])!=item['artwork']['sha256']: error('accepted artwork changed before export')
    out=Path(ws)/'exports'; out.mkdir(exist_ok=True)
    from PIL import Image
    if d['route']=='DETAIL_PAGE':
        for g in d['layout']['jobs']:
            item=d['groups'][g['id']];
            with Image.open(item['artwork']['path']) as im:
                screen_h=g['canvas'][1]//len(g['unit_ids'])
                for i,sid in enumerate(g['unit_ids']): im.crop((0,i*screen_h,g['canvas'][0],(i+1)*screen_h)).save(out/(sid+'.png'))
    else:
        for uid in d['layout']['unit_ids']: shutil.copy2(d['units'][uid]['artwork']['path'],out/(uid+'.png'))
    files=[{'id':p.stem,'path':str(p),'sha256':sha(p)} for p in sorted(out.glob('*.png'))]
    if [x['id'] for x in files] != d['layout']['unit_ids']: error('delivery order/count does not match route plan')
    dump(Path(ws)/'artifacts/delivery-manifest.json',{'route':d['route'],'files':files,'strict_delivery':True}); d['state']='DELIVERED'; return d
