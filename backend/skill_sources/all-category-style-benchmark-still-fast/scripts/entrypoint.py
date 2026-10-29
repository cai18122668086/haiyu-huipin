#!/usr/bin/env python3
"""CLI for the route-aware, product-driven adaptation workflow."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from workflow import new_run, load, save, dump, receipt, classify, inspect_inputs, compile_crops, passport, facts, plan, audit_plan, generation, record, audit, revise, global_audit, finalize_stage, export
from reference_analysis import analysis_template, crop_panels
from generation_handoff import next_action, resume_handoff

def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr,'reconfigure'): sys.stderr.reconfigure(encoding='utf-8')
    ap=argparse.ArgumentParser(description='Aurora mechanism inheritance + product-world rebuild')
    sp=ap.add_subparsers(dest='command',required=True)
    p=sp.add_parser('new',aliases=['start']); p.add_argument('--product-input',action='append'); p.add_argument('--reference-input',action='append'); p.add_argument('--input',action='append'); p.add_argument('--facts'); p.add_argument('--base-dir',required=True); p.add_argument('--screen-count',type=int)
    p=sp.add_parser('resume'); p.add_argument('--workspace',required=True)
    commands=['register-input','inspect-inputs','classify-route','init-reference-analysis','crop-reference-panels','compile-reference-crops','compile-visual-passport','register-product-facts','plan-target-page','audit-plan','generate-pair-request','generate-single-request','resume-imagegen-request','record-pair-artwork','record-single-artwork','audit-pair','audit-single','prepare-revision','prepare-single-revision','accept-pair','accept-single','audit-global','finalize','export-single-screens','export-single','status','generate-via-adapter']
    for name in commands:
        p=sp.add_parser(name); p.add_argument('--workspace',required=True); p.add_argument('--route'); p.add_argument('--screen-count',type=int); p.add_argument('--count-evidence'); p.add_argument('--analysis'); p.add_argument('--facts'); p.add_argument('--plan'); p.add_argument('--audit'); p.add_argument('--role-review'); p.add_argument('--group-id',default='G01'); p.add_argument('--unit-id'); p.add_argument('--artwork'); p.add_argument('--render-receipt')
    a=ap.parse_args()
    try:
        if a.command in ('new','start'):
            products=a.product_input or []; refs=a.reference_input or []
            mixed=None
            if a.input and not products and not refs:
                mixed=a.input
            elif a.input:
                if not products and a.input: products=[a.input[0]]
                refs += a.input[1:]
            ws,d=new_run(a.base_dir,products,refs,a.facts,a.screen_count,mixed); print(json.dumps({'status':'RUN_CREATED','run':str(ws),'state':d['state']},ensure_ascii=False)); return
        ws=Path(a.workspace).resolve(); d=load(ws)
        if a.command=='resume':
            action=next_action(d,ws)
            # Validate the persisted handoff whenever artwork is in flight.
            # This is deliberately read-only: recovery must not mint a token.
            if action.get('resume_existing_request'):
                resume_handoff(d,ws)
            print(json.dumps({'status':'RUN_RESUMED','run':str(ws),'state':d['state'],'next_action':action},ensure_ascii=False)); return
        if a.command=='status':
            action=next_action(d,ws)
            if action.get('resume_existing_request'):
                resume_handoff(d,ws)
            print(json.dumps({**d,'next_action':action},ensure_ascii=False)); return
        if a.command=='resume-imagegen-request':
            print(json.dumps(resume_handoff(d,ws),ensure_ascii=False)); return
        if a.command=='generate-via-adapter': raise ValueError('native imagegen handoff is external: use the generation-request JSON and then record artwork')
        c=a.command
        if c=='register-input':
            if d['state']!='INPUT_REGISTERED': raise ValueError('input is already registered or workflow state changed')
        elif c=='inspect-inputs': inspect_inputs(d,a.role_review,ws)
        elif c=='classify-route': classify(d,a.route,a.screen_count,a.count_evidence or ('explicit route '+str(a.route)))
        elif c in ('init-reference-analysis','crop-reference-panels'):
            if d['state'] not in ('ROUTE_CLASSIFIED','REFERENCE_DECOMPOSED'): raise ValueError('reference preparation requires a classified route before Passport')
            if not a.analysis: raise ValueError('--analysis is required')
            if c=='init-reference-analysis': analysis_template(d,a.analysis)
            else: crop_panels(d,a.analysis,ws)
        elif c=='compile-reference-crops': compile_crops(d,a.analysis,ws)
        elif c=='compile-visual-passport': passport(d,a.analysis)
        elif c=='register-product-facts': facts(d,a.facts or d.get('facts_source'))
        elif c=='plan-target-page': plan(d,a.plan,ws)
        elif c=='audit-plan': audit_plan(d,a.audit)
        elif c in ('generate-pair-request','generate-single-request'):
            if (d['route']=='DETAIL_PAGE') != (c=='generate-pair-request'): raise ValueError('detail uses Pair; every other route uses typed units')
            key=a.group_id if c=='generate-pair-request' else (a.unit_id or 'U01'); generation(d,key,ws)
        elif c in ('record-pair-artwork','record-single-artwork'):
            if (d['route']=='DETAIL_PAGE') != (c=='record-pair-artwork'): raise ValueError('artwork command does not match route')
            if d['route']=='DETAIL_PAGE' and not a.render_receipt:
                raise ValueError('DETAIL_PAGE artwork requires --render-receipt from the native artflow output')
            key=a.group_id if c=='record-pair-artwork' else (a.unit_id or 'U01'); record(d,key,a.artwork,ws,a.render_receipt)
        elif c in ('audit-pair','audit-single'):
            if (d['route']=='DETAIL_PAGE') != (c=='audit-pair'): raise ValueError('audit command does not match route')
            key=a.group_id if c=='audit-pair' else (a.unit_id or 'U01'); audit(d,key,a.audit,ws)
        elif c in ('prepare-revision','prepare-single-revision'):
            if (d['route']=='DETAIL_PAGE') != (c=='prepare-revision'): raise ValueError('revision command does not match route')
            key=a.group_id if c=='prepare-revision' else (a.unit_id or 'U01'); revise(d,key)
        elif c in ('accept-pair','accept-single'):
            if (d['route']=='DETAIL_PAGE') != (c=='accept-pair'): raise ValueError('accept command does not match route')
            if d['state']!='ACCEPTED': raise ValueError('accept requires a passing audit')
            key=a.group_id if c=='accept-pair' else (a.unit_id or 'U01'); pool=d['groups'] if d['route']=='DETAIL_PAGE' else d['units']
            if key not in pool or pool[key]['status']!='accepted': raise ValueError('target has no passing audit')
            from workflow import utc
            pool[key]['accepted_at']=utc()
            if d['route']=='DETAIL_PAGE': pool[key]['passport_frozen']=True
        elif c=='audit-global': global_audit(d,a.audit)
        elif c=='finalize': finalize_stage(d,ws)
        elif c in ('export-single-screens','export-single'): export(d,ws)
        else: raise ValueError('unknown command')
        receipt(d,c,route=d.get('route')); save(ws,d); print(json.dumps({'status':d['state'],'state':d['state'],'route':d.get('route'),'run':str(ws),'next_action':next_action(d,ws)},ensure_ascii=False))
    except (ValueError,KeyError,TypeError,OSError) as e:
        print('WORKFLOW_ERROR: '+str(e),file=sys.stderr)
        if 'ws' in locals() and (ws/'artifacts/workflow.json').is_file():
            if a.command in ('plan-target-page','audit-plan'):
                dump(ws/'artifacts/generation-block.json',{'status':'GENERATION_BLOCKED','command':a.command,'reason':str(e),'state':load(ws).get('state'),'route':load(ws).get('route')})
            persisted=load(ws)
            print(json.dumps({'state_preserved':persisted['state'],'next_action':next_action(persisted,ws),'recovery':'Correct the indicated input and rerun the failed command. Do not advance or replace DETAIL_PAGE with a single-unit route.'},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
if __name__=='__main__': main()
