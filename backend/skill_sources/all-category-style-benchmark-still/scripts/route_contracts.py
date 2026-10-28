"""Typed commercial/geometry contracts. All coordinates are normalized xywh."""
ROUTES = ('SEARCH_COVER','CAROUSEL_SET','DETAIL_PAGE','SELLING_POINT_CARD',
          'PROMO_POSTER','BANNER','ECOMMERCE_BANNER','SOCIAL_COMMERCE_CARD','MARKETING_VISUAL_SET')
ALIASES = {'ECOMMERCE_BANNER': 'BANNER'}
MECHANISMS = ('typography','material','composition','hierarchy','carrier','light_depth','density','reading_path')
DETAIL_PAIR_ASPECT_RATIO = (3, 8)  # width:height for each two-screen master
DETAIL_PAIR_ASPECT_TOLERANCE = 0.01
CONTAMINANTS = ('source_brand','source_packaging','source_logo','source_sku','competitor','source_copy','literal_composition','source_world','unconfirmed_product_mark','forbidden_typography_form')
BASE_CHECKS = ('style_inheritance','product_identity','product_colors','product_material_shape',
               'product_semantics','fact_safety','originality','typography','density','safe_zones',
               'decorative_label_free')
SPEC = {
 'SEARCH_COVER': {'size':[1200,1200], 'fields':['thumbnail_readability','core_benefit'], 'checks':['thumbnail_recognition']},
 'CAROUSEL_SET': {'size':[1080,1440], 'fields':['buyer_question','adjacent_handoff'], 'checks':['buyer_question','sequence_handoff']},
 # 768×1024 is only a geometry seed for regions. Artwork is accepted at any
 # native pixel size when the Pair master keeps the requested 3:8 ratio.
 'DETAIL_PAGE': {'size':[768,1024], 'fields':['buyer_question','edge_handoff'], 'checks':['buyer_question','screen_continuity','pair_seam']},
 'SELLING_POINT_CARD': {'size':[1200,1200], 'fields':['proof_event','evidence_fact_ids'], 'checks':['proof_binding']},
 'PROMO_POSTER': {'size':[1440,1920], 'fields':['campaign_peak','benefit','call_to_action'], 'checks':['campaign_peak','call_to_action']},
 'BANNER': {'size':[2400,800], 'fields':['responsive_crops','interface_exclusions','crop_strategy'], 'checks':['responsive_readability']},
 'SOCIAL_COMMERCE_CARD': {'size':[1080,1440], 'fields':['feed_hook','mobile_reading_order'], 'checks':['feed_readability']},
 'MARKETING_VISUAL_SET': {'size':[1200,1200], 'fields':['format_contract','campaign_passport'], 'checks':['format_geometry','sequence_handoff']},
}

def canonical(route):
    if route not in ROUTES: raise ValueError('unsupported route')
    return ALIASES.get(route, route)

def layout(route, count=None, count_evidence=None):
    route = canonical(route)
    if route == 'DETAIL_PAGE':
        n = 10 if count is None else count
        if type(n) is not int or not 8 <= n <= 12: raise ValueError('detail requires 8–12 screens; default 10')
        if count is not None and not count_evidence: raise ValueError('explicit count requires user count evidence')
        ids = [f'S{i:02}' for i in range(1,n+1)]
        jobs = [{'id':f'G{i//2+1:02}', 'unit_ids':ids[i:i+2], 'canvas':[768,1024*len(ids[i:i+2])]} for i in range(0,n,2)]
    else:
        n = (3 if route in ('CAROUSEL_SET','MARKETING_VISUAL_SET') else 1) if count is None else count
        if type(n) is not int or n < 1 or n > 30: raise ValueError('unit count must be 1–30')
        if route == 'MARKETING_VISUAL_SET' and n < 3: raise ValueError('set requires at least 3 units')
        if route == 'CAROUSEL_SET' and n < 3 and not count_evidence:
            raise ValueError('a carousel below 3 units requires explicit user count evidence')
        if route not in ('CAROUSEL_SET','MARKETING_VISUAL_SET') and n != 1: raise ValueError('route requires one unit')
        ids = [f'U{i:02}' for i in range(1,n+1)]
        jobs = [{'id':uid,'unit_ids':[uid],'canvas':list(SPEC.get(route, {'size':[1200,1200]})['size'])} for uid in ids]
    return {'route':route,'count':n,'unit_ids':ids,'jobs':jobs}

def audit_dimensions(unit_routes):
    return sorted(set(BASE_CHECKS).union(*(SPEC[canonical(r)]['checks'] for r in unit_routes)))


def generation_scope(route, job):
    detail = canonical(route) == 'DETAIL_PAGE'
    ids, canvas = job['unit_ids'], job['canvas']
    screen_height = canvas[1] // len(ids) if detail and ids else None
    return {'mode': ('detail_pair' if len(ids) == 2 else 'detail_single_tail') if detail else 'single_unit',
            'artifact_count': 1, 'target_id': job['id'], 'unit_ids': ids,
            'arrangement': 'vertical_continuous' if detail else 'single_canvas',
            'unit_regions': [{'unit_id': sid, 'bbox': [0, i*screen_height, canvas[0], (i+1)*screen_height] if detail else [0,0,*canvas]}
                             for i, sid in enumerate(ids)],
            'exclude_other_units': True, 'split_only_at_delivery': detail}
