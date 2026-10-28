import json, zipfile, sys, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[1]; sys.path.insert(0,str(ROOT/'scripts'))
from route_contracts import ROUTES, SPEC, layout
def test_default_contract_and_schema():
    c=layout('DETAIL_PAGE'); assert c['count']==10 and len(c['jobs'])==5 and c['jobs'][-1]['unit_ids']==['S09','S10']
    schema=json.loads((ROOT/'schemas/generation-request.schema.json').read_text()); assert set(['token','product_image_bindings','sanitized_style_crop_bindings','semantic_translation_map']).issubset(schema['required'])
def test_every_route_has_geometry_and_scope():
    for r in ROUTES:
        c=layout(r); assert c['count']>=1
        if r not in ('DETAIL_PAGE','CAROUSEL_SET','MARKETING_VISUAL_SET'): assert c['count']==1
def test_docs_name_real_commands():
    entry=(ROOT/'scripts/entrypoint.py').read_text(encoding='utf-8')
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    for name in ('compile-reference-crops','compile-visual-passport','register-product-facts','plan-target-page','audit-plan','generate-pair-request','generate-single-request','audit-global','finalize'):
        assert "'"+name+"'" in entry or '"'+name+'"' in entry
        assert name in readme

def test_activation_gate_forbids_direct_image_tool_bypass():
    skill=(ROOT/'SKILL.md').read_text(encoding='utf-8')
    interface=(ROOT/'agents'/'openai.yaml').read_text(encoding='utf-8')
    workflow=(ROOT/'references'/'execution-workflow.md').read_text(encoding='utf-8')
    assert 'Mandatory activation gate' in skill
    assert 'entrypoint.py new --base-dir <run-parent>' in skill
    assert 'Reading this file or a reference is not workflow execution' in skill
    assert 'only permitted bridge' in skill
    assert 'Do not call the provider, any other image API, or a hand-written prompt directly' in skill
    assert 'first operational action is always `new`' in workflow
    assert 'adapter handoff exist' in interface
def test_no_package_cache():
    with tempfile.TemporaryDirectory() as td:
        out=Path(td)/'package.skill'
        subprocess.check_call([sys.executable,str(ROOT/'scripts/package_skill.py'),str(ROOT),'--output',str(out)])
        with zipfile.ZipFile(out) as z: assert not any('__pycache__' in n or '.pytest_cache' in n or n.endswith('.pyc') for n in z.namelist())

def test_generated_artifacts_match_json_schemas(tmp_path):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from test_reference_pair_recovery import overview_run
    from test_workflow import setup
    from workflow import compile_crops, generation, load_json
    schemas={p.name:json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'schemas').glob('*.json')}
    for schema in schemas.values(): Draft202012Validator.check_schema(schema)
    analysis=schemas['reference-analysis.schema.json']
    registry=Registry().with_resource(analysis['$id'],Resource.from_contents(analysis))
    ws,d,ap,a=overview_run(tmp_path)
    Draft202012Validator(analysis,registry=registry).validate(a)
    compile_crops(d,ap,ws)
    Draft202012Validator(schemas['reference-crops.schema.json'],registry=registry).validate(d['reference_crops'])
    for route in ROUTES:
        directory=tmp_path/route; directory.mkdir()
        ws,d,_=setup(directory,route)
        key='G01' if route=='DETAIL_PAGE' else 'U01'
        generation(d,key,ws)
        req=load_json(ws/f'artifacts/generation-request-{key}.json')
        Draft202012Validator(schemas['generation-request.schema.json'],registry=registry).validate(req)
        from style_harness import validate_generation_request
        assert validate_generation_request(ws/f'artifacts/generation-request-{key}.json')


def test_required_execution_protocols_are_linked_and_template_is_canonical(tmp_path):
    from reference_analysis import analysis_template
    from route_contracts import MECHANISMS
    from test_reference_pair_recovery import overview_run
    ws,d,_,_=overview_run(tmp_path); output=tmp_path/'template.json'; analysis_template(d,output)
    schema=json.loads((ROOT/'schemas/reference-analysis.schema.json').read_text(encoding='utf-8'))
    template=json.loads(output.read_text(encoding='utf-8'))
    assert list(template['mechanisms'])==schema['$defs']['mechanisms']['required']==list(MECHANISMS)
    skill=(ROOT/'SKILL.md').read_text(encoding='utf-8')
    for name in ('reference-analysis.md','detail-pair-execution.md'):
        assert f'(references/{name})' in skill and (ROOT/'references'/name).is_file()
    from test_cli_e2e import run
    for command in ('init-reference-analysis','crop-reference-panels'):
        import os
        p=subprocess.run([sys.executable,str(ROOT/'scripts/entrypoint.py'),command,'--help'],capture_output=True,text=True,encoding='utf-8')
        assert p.returncode==0 and '--analysis' in p.stdout
