"""Readable native imagegen prompt derived from a validated current job.

The request JSON is a machine contract and may be large. The model prompt is a
separate, bounded artifact: it contains only the current unit or Pair, its
relevant facts, and continuity instructions. It never embeds hashes, paths,
the full ten-screen plan, or the full request JSON.
"""
import json
import hashlib
from pathlib import Path


MAX_PROMPT_CHARS = 12000


def _text(value, limit=420):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _unique(values):
    result = []
    for value in values:
        value = _text(value)
        if value and value not in result:
            result.append(value)
    return result


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def resume_handoff(d, ws):
    """Return the persisted handoff; never create a new token or request."""
    pool = d.get('groups', {}) if d.get('route') == 'DETAIL_PAGE' else d.get('units', {})
    active = [(key, item) for key, item in pool.items() if item.get('status') == 'awaiting_artwork']
    if len(active) != 1 or not d.get('active_token'):
        raise ValueError('no single awaiting artwork request is available to resume')
    key, item = active[0]
    request = Path(ws) / f'artifacts/generation-request-{key}.json'
    prompt = Path(ws) / f'artifacts/imagegen-prompt-{key}.txt'
    recovery = Path(ws) / 'artifacts/recovery-manifest.json'
    if not request.is_file() or not prompt.is_file() or not recovery.is_file():
        raise ValueError('resume blocked: persisted request, prompt, or recovery manifest is missing; do not start a new generation')
    req = json.loads(request.read_text(encoding='utf-8-sig'))
    manifest = json.loads(recovery.read_text(encoding='utf-8-sig'))
    if req.get('token') != d.get('active_token') or item.get('generation_token') != d.get('active_token'):
        raise ValueError('resume blocked: workflow token does not match the persisted request')
    if manifest.get('run_id') != d.get('run_id') or manifest.get('target_id') != key or manifest.get('token') != d.get('active_token'):
        raise ValueError('resume blocked: recovery manifest belongs to another run or target')
    if manifest.get('request_sha256') != _sha(request) or manifest.get('prompt_sha256') != _sha(prompt):
        raise ValueError('resume blocked: persisted request or prompt changed; do not regenerate')
    bindings = req.get('product_image_bindings', []) + req.get('sanitized_style_crop_bindings', [])
    for binding in bindings:
        path = Path(binding['path'])
        if not path.is_file() or _sha(path) != binding.get('sha256'):
            raise ValueError('resume blocked: a bound product/reference file is missing or changed; restore it before continuing')
    handoff = {'status': 'IMAGEGEN_RESUME_READY', 'tool': 'artflow', 'run_id': d.get('run_id'),
               'target_id': key, 'token': d.get('active_token'), 'request': str(request.resolve()),
               'prompt': str(prompt.resolve()), 'resume_existing_request': True, 'invocation_count': 1,
               'referenced_image_paths': [x['path'] for x in bindings], 'generation_scope': req.get('generation_scope'),
               'request_sha256': _sha(request), 'prompt_sha256': _sha(prompt)}
    if req.get('route') == 'DETAIL_PAGE':
        handoff['requested_aspect_ratio'] = req.get('render_contract', {}).get('aspect_ratio')
        handoff['clarity_guidance'] = req.get('render_contract', {}).get('clarity_guidance')
    else:
        handoff['requested_size'] = req.get('render_contract', {}).get('native_generation_size')
    return handoff


def _content_steps(req, ids):
    wanted = set(ids)
    rows = req.get('reference_content_analysis', {}).get('sequence', [])
    return [row for row in rows if row.get('id') in wanted]


def _source_sequence(req, target_rows):
    """Describe the ordered single-panel source sequence for this target."""
    steps = {row.get('id'): row for row in req.get('reference_content_analysis', {}).get('sequence', [])}
    result = []
    for row in target_rows:
        for sid in row.get('source_content_step_ids', []):
            step = steps.get(sid)
            if not step:
                continue
            result.append({
                'source_order': step.get('source_order'),
                'source_content_step_id': sid,
                'source_reference_ids': step.get('source_reference_ids', []),
                'content_role': step.get('content_role'),
                'normalized_message': step.get('normalized_message'),
                'expression_actions': step.get('expression_actions', []),
                'handoff': step.get('handoff'),
            })
    return result


def _facts(req, claim_ids):
    wanted = set(claim_ids)
    return [_text(row.get('text'), 260) for row in req.get('product_visual_facts', [])
            if row.get('id') in wanted and row.get('text')]


def _continuity(req, target_rows, scope):
    upper, lower = target_rows[0], target_rows[-1]
    content = _content_steps(req, upper.get('source_content_step_ids', []) + lower.get('source_content_step_ids', []))
    handoffs = _unique(row.get('handoff') for row in content)
    source_duties = _unique(row.get('persuasion_job') or row.get('conversion_stage') or row.get('content_role')
                            for row in content)
    palette = req.get('product_palette_contract', {})
    seam = scope.get('unit_regions', [{},{}])[1].get('bbox',[0,1920,0,0])[1]
    return {
        'shared_visual_world': _unique([
            'same current product identity, proportions and confirmed material across both screens',
            'same product-native palette family; background, scene and light transitions follow the observed adjacent boundary',
            'same typography morphology and carrier language; each screen keeps its own observed density and visual emphasis'
        ]),
        'carryover': _unique([*source_duties, upper.get('primary_duty'), lower.get('primary_duty'),
            'current product remains the same product with consistent identity; camera and environment may change only according to the recorded boundary']),
        'narrative_handoff': handoffs or ['continue the adjacent source step and its observed expression method before adding product-specific detail'],
        'seam_rule': f'continue one vertical editorial page across the {seam}px boundary using the recorded transition treatment; carry at least two visible anchors across the seam (background texture or gradient, light ribbon, frame edge, annotation line, or product silhouette); do not reset to a generic repeated card or unrelated hero',
        'seam_anchors': ['carry at least two visible anchors across the seam', 'keep the lower screen entering from the upper screen edge with no new top margin', 'preserve the reference boundary kind instead of inventing a uniform card grid'],
        'palette_evidence': _unique([json.dumps(palette, ensure_ascii=False, separators=(',', ':'))])
    }


def build_continuity_contract(req, target_rows, scope):
    if req.get('route') != 'DETAIL_PAGE' or len(target_rows) < 2:
        return None
    return _continuity(req, target_rows, scope)


def write_handoff(req, output):
    scope = req['generation_scope']
    size = req['render_contract']['native_generation_size']
    target_rows = req.get('current_target_plan', [])
    if [row.get('id') for row in target_rows] != scope.get('unit_ids'):
        raise ValueError('current_target_plan does not match generation_scope')
    detail = req.get('route') == 'DETAIL_PAGE'
    prompt = [f"本次只生成当前详情页的{'上下两屏' if len(target_rows)==2 else '末屏'}。Pair 母图宽高比必须为 3:8（宽:高）；使用可获得的最清晰原生输出，不要为了达到某个像素尺寸而放大、锐化或重采样。" if detail else f"本次只生成 {scope['target_id']}：{', '.join(scope['unit_ids'])}。输出 1 张 {size[0]}×{size[1]} PNG。"]
    if req.get('detail_ratio'):
        r = req['detail_ratio']
        prompt.append(f"首组已记录原生比例（高宽比 {r['ratio']:.4f}）；本组继续使用 3:8 Pair 比例，不要在画面显示比例数字。")
    if scope['mode'] == 'detail_pair':
        prompt.append('这是详情页两屏一组的连续竖向母图：上屏占母图上半幅、下屏占母图下半幅，二者共享整张画布宽度，唯一屏间分界是横向接缝；禁止左右双栏、竖直中线、并排拼屏、把每屏拉成长栏或把两屏内容压到同一屏。上屏与下屏各自只执行绑定的一个目标屏、一个主视觉和该屏列出的可见文案，不能自行添加第二主视觉、额外解释段或下一屏卖点。禁止只画一屏、整套缩略总览或多文件代替母图。')
    prompt += ['只执行下面列出的当前目标；不要绘制其他屏。内部屏幕编号、组编号、JSON 字段名、位置坐标和制作标记都是执行指令，禁止出现在画面中。' if detail else '只执行下面列出的当前目标；不要绘制其他屏，不要把完整十屏计划写入画面。',
               '全部文字、标签、尺寸和产品画面由图像模型一次性共同生成；使用可获得的最清晰原生分辨率；禁止后期叠字、HTML 排版或低清放大。',
               '继承参考图的具体八维视觉机制和内容展开方式，同时围绕当前产品事实重建产品世界。不得复制来源品牌、包装、Logo、SKU、原文案、竞品或未经确认的功效。产品本体、织物、吊牌和配件上不得出现任何未经产品事实确认的品牌名、系列名、产品名、英文或中文 wordmark、Logo、水印或可辨识字样；未确认字样一律用无字材质、纯色或不可读纹理替代。',
               '全路由只禁止 AI 标签化的载体形式：不得把文字包装进胶囊、徽章、贴纸、标签云或重复的圆角卡片，也不要用重复的图标短句框代替对标原有的信息版式。这个规则不限制正文、标题、说明、规格、证据、成分、用法或对标同等信息量；这些文字必须按对标的文字块数量、字数、层级和阅读顺序完整展开。',
               '全路由禁止示例中的手写、毛笔、书法、马克笔或花体连笔字形作为文字载体；这是字体形式约束，不是删减文字约束。保留对标要求的正文、标题、说明、规格、证据、成分、用法和同等信息密度，改用清晰的非手写字体表达相同内容和层级。',
               '高级感与信息密度必须从对标观察结果继承，不使用本 Skill 自定义的统一留白、图标数量、主视觉比例或极简审美。先保持对标的字号比例、材质、光影、构图、文字量和阅读节奏，再只把当前产品事实替换进去。',
               '文案合规按四档执行：普通描述只需使用已确认或可直接观察的产品事实；专利、认证、检测、量化、排名、比较和强安全/功效表述必须绑定证据；治疗/疾病等品类词先按商品类别复核，不做全品类自动拦截；明确禁止的保证、虚假背书和违法表达不得渲染。不要因为某个普通词可能在其他品类高风险，就删掉正常规格、材质、质地、容量或使用说明。',
               '详情页只按对标步骤的实际内容职责、证据义务、可见密度和阅读顺序展开。专利、检测、认证等资料只是可选的产品事实来源：只有当对应对标步骤需要这类事实时才放入；资料存在本身不会新增一屏、改变卖点顺序或产生“必须展示”的覆盖任务。对标没有展示的资料不要强行塞入画面；若某条事实被选入文案，再按其证据范围执行风险复核。最后一屏的收束/总结/结尾只是内部规划职责，禁止把这些内部词语渲染为画面文案；画面只呈现对标对应的自然产品信息。',
               '', '【当前屏位与职责】']
    for index, (row, region) in enumerate(zip(target_rows, scope['unit_regions']), 1):
        content = _content_steps(req, row.get('source_content_step_ids', []))
        target_world = _unique((row.get('semantic_translation') or {}).get(key, {}).get('target_world')
                               for key in (row.get('semantic_translation') or {}))
        label = ('上半部' if index==1 else '下半部') if detail and len(target_rows)==2 else ('当前画面' if detail else row['id'])
        budget = row.get('density_contract') or {}
        copy = row.get('visible_copy') or []
        evidence = row.get('evidence_bindings') or []
        visual_roles = row.get('required_visual_roles') or []
        source_stage = '; '.join(_text(x.get('conversion_stage')) for x in content)
        source_question = '; '.join(_text(x.get('buyer_question')) for x in content)
        source_shift = '; '.join(_text(x.get('belief_shift')) for x in content)
        source_actions = '; '.join(action for x in content for action in (x.get('expression_actions') or []))
        source_opening = '; '.join(_text(x.get('opening_function')) for x in content)
        source_job = '; '.join(_text(x.get('persuasion_job')) for x in content)
        source_evidence = '; '.join(item for x in content for item in (x.get('evidence_obligations') or []))
        source_roles = '; '.join(item for x in content for item in (x.get('expression_actions') or []))
        detail_duty = source_job or source_stage or '; '.join(_text(x.get('content_role')) for x in content)
        lines = [f"{label} 位置={region['bbox']}；职责={_text(detail_duty if detail else row.get('primary_duty'))}；角色={_text(row.get('narrative_role')) or '; '.join(_text(x.get('content_role')) for x in content)}",
                 f"参考屏信息密度合同：最多 {budget.get('text_blocks_max','')} 个文字块、可见字数约 {budget.get('text_chars_max','')}、参考卡片/载体数量 {budget.get('card_count_max','')}、主视觉与留白比例 {budget.get('main_visual_share_min','')}/{budget.get('whitespace_share_min','')}；这些是硬上限，不得新增标题、解释段、细节图、参数行或第二主视觉去填满画布。完整保留对标的文字量、解释层级和阅读顺序，不得因禁用标签载体而删减正文、规格、证据或使用说明。",
                 f"本屏只渲染这些可见文案块：{'；'.join(_text(x,180) for x in copy)}",
                 f"本屏文案风险档位={','.join(row.get('copy_risk_tiers', [])) or 'ordinary'}；文案证据 ID={','.join(row.get('copy_evidence_ids', [])) or '无额外证据要求'}；品类复核={_text(req.get('product_category'),120) or '由调用方根据商品事实判断'}",
                 f"对标屏功能={source_opening or '按实际拆解填写'}；目标继承功能={_text(row.get('source_function'),180) or '按对标屏功能继承'}",
                 f"当前产品事实={'; '.join(_facts(req, row.get('primary_claim_ids', []))) or '只使用请求中已确认的产品事实'}；本屏绑定事实 ID={','.join(row.get('primary_claim_ids', [])) or '按对标步骤选择'}",
                 f"产品世界转译={'; '.join(target_world) or '按已确认产品事实重构环境、道具、色彩和构图'}",
                 f"对标内容阶段={source_stage or '按当前屏职责展开'}；对标回答的问题={source_question or '以实际拆解为准'}；对标造成的认知变化={source_shift or '以实际拆解为准'}",
                 f"对标展开备注={_text(row.get('source_expansion_note'),180) or '按该源步骤的实际表达动作展开；对标重复就保留重复，对标区分才要求区分'}",
                 f"对标的可见表达动作={source_actions or '以实际拆解为准'}"]
        if detail:
            lines.extend([
                f"本屏展开逻辑只继承绑定的对标步骤：说服任务={_text(source_job,260) or '按对标步骤实际职责'}；证据义务={_text(source_evidence,320) or '按对标步骤实际证据'}；表达对象={_text(source_roles,320) or '按对标步骤实际表达动作'}。",
                '不要从详情页默认模板、产品事实顺序或内部屏幕编号推导本屏职责；先复现对标步骤的提问→证据/信号→表达→交接，再把当前产品事实放入同一方法。',
            ])
        else:
            lines.extend([
                f"本屏买家疑问={_text(row.get('buyer_question'),260)}；本屏回答={_text(row.get('answer'),260)}；认知变化={_text(row.get('belief_shift'),260)}",
                f"本屏必须出现的表达对象={'; '.join(_text(x,180) for x in visual_roles)}",
                f"证据绑定={json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}",
                f"本屏出口问题={_text(row.get('handoff_question'),260)}",
            ])
        prompt.extend(lines)
    if detail:
        prompt += ['', '【当前 Pair 对标单屏顺序】',
                   '当前 Pair 不是从整套参考图中自由挑选画面；必须按下面的 source_order 逐个吸收单屏对标的构图职责、信息节奏和交接。若一个目标屏绑定多个源步骤，按顺序把它们作为同一屏内的连续信息层展开，不得只采用第一个步骤。源图附件按此顺序提供，source_reference_ids 只用于定位附件，不得渲染到画面。']
        for item in _source_sequence(req, target_rows):
            prompt.append('源序号={source_order}；源图={source_reference_ids}；源步骤={source_content_step_id}；功能={content_role}；信息={normalized_message}；表达动作={actions}；交接={handoff}'.format(
                source_order=item.get('source_order'),
                source_reference_ids=','.join(item.get('source_reference_ids') or []),
                source_content_step_id=item.get('source_content_step_id'),
                content_role=_text(item.get('content_role')),
                normalized_message=_text(item.get('normalized_message')),
                actions='；'.join(_text(x) for x in item.get('expression_actions') or []),
                handoff=_text(item.get('handoff')),
            ))
    mechanisms = req.get('visual_fidelity_contract', {}).get('style_signature', {})
    prompt += ['', '【共享视觉机制】']
    for key in ('typography', 'material', 'composition', 'hierarchy', 'carrier', 'light_depth', 'density', 'reading_path'):
        if mechanisms.get(key): prompt.append(f"{key}: {_text(mechanisms[key], 260)}")
    prompt += ['', '【字体与文字风格硬约束】',
               '参考图的字体机制应继承字面宽度、笔画粗细/对比、字重层级、行距、字距、对齐方式、数字比例、英文比例、描边或金属/发光处理以及文字所在载体；安全与形式禁令优先于风格继承，禁止的手写、毛笔、书法、马克笔和花体连笔只可替换为清晰的非手写字形。不要用“科技感”或默认粗黑字体替代其余允许的参考字形机制。',
               '标题、数字、正文和微小说明必须形成与参考相同的层级节奏；可读性来自字号和留白，禁止缩小文字去塞入更多卖点。']
    prompt += ['', '【版式载体与节奏硬约束】',
               '优先复现参考物理面板的非标签载体和阅读路径：细线、引线、分栏间距、留白位置、字号层级和信息分组在对应目标中保留同等作用。若参考使用胶囊、徽章、贴纸、标签云或重复圆角卡片来承载短词，继承其信息层级和节奏，但改用自然正文、标题、数据注释、引线或开放式版式表达，禁止复制这些 AI 标签载体；参考没有的框不要新增。不要把参考的多栏/卡片节奏压平成一张满版产品海报，也不要把所有屏幕统一成相同的三卡片网格。']
    prompt.append(('整套机制描述只提供视觉语言；详情页每屏的展开逻辑以当前屏绑定的对标物理步骤为唯一叙事来源，按对标的提问→证据/信号→表达→交接展开，不能用默认详情页顺序、产品资料顺序或调用方自定义的固定屏幕职责替代。若对标首屏是 result_promise，目标首屏必须保留结果承诺；只有对标明确是 problem_awareness 时才从痛点切入。产品事实只能支持绑定且已确认的主张，不得编造数字、效果或文件结论。'
                    if detail else
                    '整套机制描述只提供视觉语言；逐屏转化合同、对标屏功能和密度预算同等优先。必须把本屏买家疑问、回答、认知变化、证据绑定和表达对象落实为画面结构。产品事实只能支持已绑定的主张，不得编造数字、效果或文件结论。') +
                   ' 对标没有的框不添加；总览的展示外框和列间距不是内屏设计元素。')
    continuity = req.get('pair_continuity_contract') or build_continuity_contract(req, target_rows, scope)
    if continuity:
        prompt += ['', '【上下屏连续性合同】',
                   '共享世界：' + '；'.join(continuity['shared_visual_world']),
                   '承接关系：' + '；'.join(continuity['carryover']),
                   '叙事交接：' + '；'.join(continuity['narrative_handoff']),
                   '连续性规则：' + continuity['seam_rule']]
        prompt.append('跨缝锚点：' + '；'.join(continuity.get('seam_anchors', [])))
        edges = req.get('target_transition_plan', {})
        for edge in edges.get('within', []):
            prompt.append(f"当前组上下屏边界：采用 {edge.get('kind')}；{edge.get('target_treatment')}；保留承接：{edge.get('carryover')}。禁止把该边界改成模板化重复圆角卡片，也不要在画面显示内部编号。")
        if edges.get('incoming'):
            edge = edges['incoming']; prompt.append(f"从上一屏承接：采用 {edge.get('kind')}；{edge.get('target_treatment')}；{edge.get('carryover')}。")
        if edges.get('outgoing'):
            edge = edges['outgoing']; prompt.append(f"为下一屏留下出口：采用 {edge.get('kind')}；{edge.get('target_treatment')}；{edge.get('carryover')}。")
    if not detail:
        edges = req.get('target_transition_plan', {})
        for label in ('incoming','outgoing'):
            edge = edges.get(label)
            if edge:
                prompt.append(f"独立画布的翻页{'承接' if label=='incoming' else '出口'}：{edge['target_treatment']}；{edge['carryover']}。保持单张独立画布，不绘制两屏连续母图。")
    prompt += ['', '参考图附件只用于提炼机制，当前产品附件只用于产品身份；不要把任何参考图的环境、人物、竞品产品、品牌或可辨识物体直接搬入当前产品。每个附件只服务于其绑定的 source_content_step_id；不要把一个源步骤的裁图当成整套详情页的通用模板。']
    text = '\n'.join(prompt).strip() + '\n'
    if len(text) > MAX_PROMPT_CHARS:
        raise ValueError(f'imagegen prompt exceeds {MAX_PROMPT_CHARS} characters; reduce current target fields, never embed full request JSON')
    Path(output).write_text(text, encoding='utf-8')


def next_action(d, ws):
    state, detail = d['state'], d.get('route') == 'DETAIL_PAGE'
    block = Path(ws) / 'artifacts/generation-block.json'
    if block.is_file():
        try:
            marker = json.loads(block.read_text(encoding='utf-8-sig'))
        except Exception:
            return {'command': 'audit-plan', 'state': state, 'blocked': True,
                    'reason': 'generation-block.json is unreadable; rerun plan-target-page and audit-plan'}
        if marker.get('status') == 'GENERATION_BLOCKED':
            return {'command': marker.get('command', 'audit-plan'), 'state': state,
                    'blocked': True, 'reason': marker.get('reason', 'persisted generation block; correct the failed step')}
    pool = d.get('groups', {}) if detail else d.get('units', {})
    suffix, flag = ('pair', '--group-id') if detail else ('single', '--unit-id')
    active = next(((key, item) for key, item in pool.items() if item.get('status') not in ('accepted','failed_continue')), None)
    if active:
        key, item = active
        if item['status'] == 'awaiting_artwork':
            return {'command': 'resume-imagegen-request', 'target_id': key,
                    'request': str(Path(ws)/f'artifacts/generation-request-{key}.json'),
                    'prompt': str(Path(ws)/f'artifacts/imagegen-prompt-{key}.txt'),
                    'then': f'record-{suffix}-artwork {flag} {key} --artwork <actual-generated-file> --render-receipt <render.json>',
                    'native_verification': 'Run scripts/render_provenance.py with --request, --native-output (tool-returned original path), --artwork (unchanged copy), --output before recording.',
                    'resume_existing_request': True,
                    'guard': 'Run resume-imagegen-request first; never run generate-* or create a replacement request for this target.'}
        if item['status'] == 'audit_required': return {'command': f'audit-{suffix}', 'arguments': [flag,key,'--audit','<review.json>']}
        if item['status'] == 'revision_allowed': return {'command': 'prepare-revision' if detail else 'prepare-single-revision', 'arguments': [flag,key]}
        if state in ('GENERATION_ALLOWED','ACCEPTED','GROUP_FAILED_CONTINUE'):
            return {'command': f'generate-{suffix}-request', 'arguments': [flag,key]}
    commands = {'INPUT_REGISTERED': 'classify-route' if d.get('input_roles_confirmed') else 'inspect-inputs',
                'ROUTE_CLASSIFIED':'init-reference-analysis / crop-reference-panels / compile-reference-crops',
                'REFERENCE_DECOMPOSED':'compile-visual-passport', 'VISUAL_PASSPORT_READY':'register-product-facts',
                'PRODUCT_FACTS_READY':'plan-target-page', 'PLAN_AUDIT_REQUIRED':'audit-plan',
                'FINAL_AUDIT_REQUIRED':'finalize', 'FINALIZED':'export-single-screens' if detail else 'export-single',
                'ACCEPTED':'audit-global', 'GROUP_FAILED_CONTINUE':'audit-global', 'DELIVERED':'done'}
    return {'command': commands.get(state, 'status'), 'state': state}
