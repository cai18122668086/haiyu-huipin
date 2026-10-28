"""State inspection and deterministic fixture runner for the authoritative workflow."""
import argparse, json
from pathlib import Path
from route_contracts import layout

def fixture_states(fixture):
    route=fixture['route']; c=layout(route,fixture.get('screen_count'), 'fixture explicit count' if fixture.get('screen_count') else None)
    states=['INPUT_REGISTERED','ROUTE_CLASSIFIED','REFERENCE_DECOMPOSED','VISUAL_PASSPORT_READY','PRODUCT_FACTS_READY','PLAN_AUDIT_REQUIRED','GENERATION_ALLOWED']
    for job in c['jobs']:
        states += ['AUDIT_REQUIRED']
        if fixture.get('audit_pass', True): states += ['ACCEPTED']
        elif fixture.get('revision_pass'): states += ['REVISION_ALLOWED','GENERATION_ALLOWED','AUDIT_REQUIRED','ACCEPTED']
        else: states += ['REVISION_ALLOWED']
    states += ['FINAL_AUDIT_REQUIRED','FINALIZED','DELIVERED']
    return {'route':route,'screen_count':c['count'],'groups':c['jobs'] if route=='DETAIL_PAGE' else [],'states':states,'provenance':fixture.get('provenance','fixture_structure_only')}
if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--fixture',required=True); a=ap.parse_args(); print(json.dumps(fixture_states(json.loads(Path(a.fixture).read_text(encoding='utf-8-sig'))),ensure_ascii=False,indent=2))
