"""Prepare a native imagegen handoff; never fabricates a raster or visual pass."""
import argparse, json
from pathlib import Path
from style_harness import validate_generation_request
from generation_handoff import write_handoff
from workflow import validate_generation_gate
ap=argparse.ArgumentParser(); ap.add_argument('--workspace',required=True); ap.add_argument('--request',required=True); ap.add_argument('--output',required=True); args=ap.parse_args()
try:
    validate_generation_request(args.request); _,req=validate_generation_gate(args.workspace,args.request)
    prompt_path=Path(args.request).with_suffix('.prompt.txt'); write_handoff(req,prompt_path)
    handoff={'status':'IMAGEGEN_HANDOFF_READY','tool':'artflow','request':str(Path(args.request).resolve()),'output':str(Path(args.output).resolve()),'token':req['token'],'quality':req['render_contract']['quality'],'text_and_image_single_pass':True,'post_text_overlay':False,'invocation_count':1,'generation_scope':req['generation_scope'],'prompt_path':str(prompt_path),'referenced_image_paths':[x['path'] for x in req['product_image_bindings']+req['sanitized_style_crop_bindings']]}
    if req.get('route') == 'DETAIL_PAGE':
        handoff['requested_aspect_ratio']=req['render_contract']['aspect_ratio']
        handoff['clarity_guidance']=req['render_contract']['clarity_guidance']
    else:
        handoff['requested_size']=req['render_contract']['native_generation_size']
    print(json.dumps(handoff,ensure_ascii=False))
except Exception as e:
    raise SystemExit('IMAGEGEN_ADAPTER_BLOCKED: '+str(e))
