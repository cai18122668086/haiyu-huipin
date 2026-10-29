import json, subprocess, sys, tempfile, os, hashlib
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).parents[1]; ENTRY=ROOT/'scripts'/'entrypoint.py'
def run(*args):
    env=os.environ.copy(); env['PYTHONIOENCODING']='utf-8'; env['PYTHONUTF8']='1'
    p=subprocess.run([sys.executable,str(ENTRY),*map(str,args)],capture_output=True,text=True,encoding='utf-8',errors='replace',env=env)
    assert p.returncode==0, p.stdout+p.stderr; return json.loads(p.stdout)
def test_cli_detail_ten_screen_flow():
    with tempfile.TemporaryDirectory() as td:
        r=Path(td); prod=r/'product.png'; ref=r/'reference.png'; crop=r/'crop.png'; master=r/'master.png'
        for p,size in ((prod,(100,100)),(ref,(120,120)),(crop,(80,80)),(master,(1440,3840))): Image.new('RGB',size,(30,40,50)).save(p)
        facts=r/'facts.json'; facts.write_text(json.dumps({'facts':[{'id':'f1','text':'mesh chair','status':'user_confirmed','claim_type':'appearance','evidence_input_ids':['P01']},{'id':'f_form','text':'圆角矩形靠背与坐垫 宽高比约1:1.9 一体式底座','status':'user_confirmed','claim_type':'appearance','evidence_input_ids':['P01'],'visual_attribute':'shape'}] }),encoding='utf-8')
        a=r/'analysis.json'; a.write_text(json.dumps({'units':[{'id':'C01','reference_input_id':'R01','source_crop_path':str(crop),'source_role':'style_mechanism_only','source_semantics':['s1'],'review_status':'fixture'}], 'overview_detection':{'is_overview':False,'panel_count':1,'panels':[{'panel_order':1,'crop_unit_ids':['C01']}]}, 'mechanisms':{k:{'observation':'observed '+k,'source_unit_ids':['C01']} for k in ('typography','material','composition','hierarchy','carrier','light_depth','density','reading_path')},'source_semantics':[{'id':'s1','label':'source texture'}], 'narrative_logic':{'opening':'question','proof_progression':'evidence','takeaway':'takeaway','handoff':'next','sequence':[{'id':'N01','role':'hook','description':'opening question','source_reference_ids':['R01']}]}, 'source_content_analysis':{'set_summary':'question to proof','sequence':[{'id':'CSTEP01','source_order':1,'source_reference_ids':['R01'],'content_role':'hook','verbatim_text':['headline'],'normalized_message':'opening question','handoff':'next proof'}]}}),encoding='utf-8')
        plan=r/'plan.json'; plan.write_text(json.dumps({'units':[{'id':f'S{i:02}','primary_duty':'duty','narrative_role':'hook' if i==1 else 'proof','narrative_step_ids':['N01'],'source_content_step_ids':['CSTEP01'],'mechanism_ids':['typography'],'primary_claim_ids':[],'semantic_translation':{'s1':{'target_world':'chair-native support texture','retain_literal_source':False}}} for i in range(1,11)]}),encoding='utf-8')
        from analysis_support import physical_analysis
        from workflow import sha, dump
        from render_provenance import build_receipt
        dump(a,physical_analysis(json.loads(a.read_text()),{'inputs':[{'id':'R01','path':str(ref),'sha256':sha(ref),'role':'benchmark_reference'}]},r/'fixture-panels'))
        pa=r/'plan-audit.json'; pa.write_text(json.dumps({'review_kind':'fixture','pass':True,'route':'DETAIL_PAGE','unit_ids':[f'S{i:02}' for i in range(1,11)]}),encoding='utf-8')
        ad=r/'audit.json'; ad.write_text(json.dumps({'review_kind':'fixture','pass':True,'product_identity':True,'fact_safety':True,'dimensions':{k:True for k in ('style_inheritance','product_identity','product_colors','product_material_shape','product_semantics','fact_safety','originality','typography','density','safe_zones','decorative_label_free','buyer_question','edge_handoff','screen_continuity','pair_seam')}}),encoding='utf-8')
        created=run('new','--product-input',prod,'--reference-input',ref,'--facts',facts,'--base-dir',r/'runs'); ws=created['run']; run('register-input','--workspace',ws); run('classify-route','--workspace',ws,'--route','DETAIL_PAGE'); run('compile-reference-crops','--workspace',ws,'--analysis',a); run('compile-visual-passport','--workspace',ws,'--analysis',a); run('register-product-facts','--workspace',ws); run('plan-target-page','--workspace',ws,'--plan',plan); run('audit-plan','--workspace',ws,'--audit',pa)
        globalp=r/'global.json'; globalp.write_text(json.dumps({'review_kind':'fixture','pass':True,'ordered_ids':[f'S{i:02}' for i in range(1,11)]}),encoding='utf-8')
        for i in range(1,6):
            gid=f'G{i:02}'; receipt=r/f'{gid}-render.json'; dump(receipt,build_receipt(master,master,[1440,3840]))
            run('generate-pair-request','--workspace',ws,'--group-id',gid); run('record-pair-artwork','--workspace',ws,'--group-id',gid,'--artwork',master,'--render-receipt',receipt); run('audit-pair','--workspace',ws,'--group-id',gid,'--audit',ad); run('accept-pair','--workspace',ws,'--group-id',gid)
        run('audit-global','--workspace',ws,'--audit',globalp); run('finalize','--workspace',ws); run('export-single-screens','--workspace',ws); assert len(list((Path(ws)/'exports').glob('S*.png')))==10

def test_cli_mixed_input_requires_and_accepts_role_review():
    with tempfile.TemporaryDirectory() as td:
        r=Path(td); product=r/'chair.png'; benchmark=r/'benchmark.png'
        Image.new('RGB',(50,50),(20,30,40)).save(product); Image.new('RGB',(60,60),(100,60,40)).save(benchmark)
        created=run('new','--input',product,'--input',benchmark,'--base-dir',r/'runs'); ws=created['run']
        review=r/'roles.json'; import hashlib
        def h(p): return hashlib.sha256(p.read_bytes()).hexdigest()
        review.write_text(json.dumps({'review_kind':'manual','items':[
            {'input_id':'I01','sha256':h(product),'role':'product_original','confidence':0.95,'visual_observation':'single chair product on neutral background','evidence':['visual inspection']},
            {'input_id':'I02','sha256':h(benchmark),'role':'benchmark_reference','confidence':0.95,'visual_observation':'designed ecommerce reference with text hierarchy','evidence':['visual inspection']}
        ]}),encoding='utf-8')
        run('inspect-inputs','--workspace',ws,'--role-review',review)
        classified=run('classify-route','--workspace',ws,'--route','SEARCH_COVER')
        assert classified['state']=='ROUTE_CLASSIFIED'
