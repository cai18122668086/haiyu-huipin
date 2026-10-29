"""Synthetic physical panels for deterministic workflow fixtures only."""
from pathlib import Path
from reference_analysis import crop_panels, write

FLAGS = ('source_brand_presence','source_packaging_presence','source_logo_presence','source_sku_presence',
         'competitor_presence','source_copy_presence','source_environment_presence')


def physical_analysis(a, d, ws, panels=None):
    from PIL import Image
    if panels is None:
        panels=[]
        for i, ref in enumerate((x for x in d['inputs'] if x['role']=='benchmark_reference'),1):
            with Image.open(ref['path']) as im: w,h=im.size
            panels.append({'panel_id':f'B{i:02}','panel_order':i,'reference_input_id':ref['id'],'bbox':[0,0,w,h]})
    a['overview_detection']={'is_overview':len({p['reference_input_id'] for p in panels})<len(panels),
                             'panel_count':len(panels),'panels':panels,
                             'reading_order':'fixture stored order','detection_notes':'synthetic fixture panel boundaries'}
    for unit in a['units']:
        for flag in FLAGS: unit.setdefault(flag,False)
    for step in a.get('source_content_analysis',{}).get('sequence',[]):
        step.setdefault('source_panel_ids',[p['panel_id'] for p in panels if p['reference_input_id'] in step['source_reference_ids']])
        step.setdefault('text_status','transcribed' if step['verbatim_text'] else 'no_visible_text')
    path=Path(ws)/'fixture-analysis.json'; write(path,a)
    return crop_panels(d,path,ws)
