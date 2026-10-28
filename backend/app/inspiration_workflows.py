"""Approved immutable routing and content for the published inspiration cases.

This module is deliberately data-only.  The browser must pass these prompts
through unchanged; provider/skill behavior belongs to the provider and skill
modules, not to the gallery component.
"""
# Canonical prompt and asset digest lines intentionally preserve exact source strings.
# ruff: noqa: E501
PRODUCT_PROMPT_CASE_TITLES = (
    "生成AI模特",
    "衣服上身（无中生有）",
    "卖点图（多姿势展示）",
    "场景图（玩偶）",
    "衣服上身（模特无中生有）",
    "改造型",
)
REFERENCE_REMIX_CASE_TITLES = (
    "暖金梳妆台 · 花香时刻",
    "人物与超大产品的接触关系",
    "冷蓝水感 · 清爽洁护",
    "场景复刻+指定模特",
    "场景图（服装）",
    "模特换衣（让它看细节版）",
    "详情页风格模仿",
    "我发现你们好爱抄",
)
LOCAL_EDIT_CASE_TITLES = ("改语言语种", "去掉文字")

FINAL_INSPIRATION_WORKFLOW_BY_TITLE = {
    **{title: "product_prompt" for title in PRODUCT_PROMPT_CASE_TITLES},
    **{title: "reference_remix" for title in REFERENCE_REMIX_CASE_TITLES},
    **{title: "local_edit" for title in LOCAL_EDIT_CASE_TITLES},
}


# Canonical card metadata recovered from the immutable legacy gallery source.
# The generated-AI-model case has no reliable historical card record, so it is
# intentionally absent and must not be given invented legacy metadata.
CANONICAL_METADATA_BY_TITLE = {
    "暖金梳妆台 · 花香时刻": {
        "role": "香水氛围卖点图",
        "type": "卖点图",
        "category": "美妆个护",
        "platform": "国内",
        "ratio": "9:16",
        "description": "粉色香水、紫色绒面与珍珠烛光，适合移动端首屏。",
        "intent": "通过材质与氛围传达浪漫、精致和礼赠感",
    },
    "人物与超大产品的接触关系": {
        "role": "人物主视觉",
        "type": "场景图",
        "category": "美妆个护",
        "platform": "跨境",
        "ratio": "4:5",
        "description": "浅蓝棚拍、白衣人物、放大产品和地面倒影形成清晰扫描路径。",
        "intent": "用人物使用场景与超大产品证明强化高端生活方式联想",
    },
    "冷蓝水感 · 清爽洁护": {
        "role": "洗护清爽卖点图",
        "type": "卖点图",
        "category": "美妆个护",
        "platform": "国内",
        "ratio": "2:3",
        "description": "湿发人物、水花动势与右下产品，适合洗护详情页首屏。",
        "intent": "用水滴、冷光和湿发场景表达清爽洁护",
    },
    "场景复刻+指定模特": {
        "role": "场景复刻",
        "type": "场景图",
        "category": "服饰鞋帽",
        "platform": "国内",
        "ratio": "1:1",
        "description": "用户服装、模特与场景参考组合，生成户外上身画面。",
        "intent": "验证服装在真实户外场景中的上身效果",
    },
    "衣服上身（无中生有）": {
        "role": "服装上身",
        "type": "万能改图",
        "category": "儿童母婴",
        "platform": "国内",
        "ratio": "3:4",
        "description": "从服装产品图生成童装商业摄影上身图。",
        "intent": "展示童装穿着状态与户外使用联想",
    },
    "改语言语种": {
        "role": "文字改版",
        "type": "万能改图",
        "category": "其他",
        "platform": "国内",
        "ratio": "自动",
        "description": "保留原始画面结构，只将中文信息改为英文。",
        "intent": "为跨境平台快速准备英文版本",
    },
    "场景图（服装）": {
        "role": "人物场景图",
        "type": "场景图",
        "category": "服饰鞋帽",
        "platform": "跨境",
        "ratio": "9:16",
        "description": "两套服装参考合成自然室内生活方式画面。",
        "intent": "展示服装穿搭关系与室内生活方式氛围",
    },
    "模特换衣（让它看细节版）": {
        "role": "睡衣换装",
        "type": "万能改图",
        "category": "服饰鞋帽",
        "platform": "国内",
        "ratio": "3:4",
        "description": "将同一套睡衣的细节参考迁移到模特图。",
        "intent": "用细节参考提高换装后的服装还原度",
    },
    "卖点图（多姿势展示）": {
        "role": "多姿势卖点图",
        "type": "卖点图",
        "category": "服饰鞋帽",
        "platform": "跨境",
        "ratio": "自动",
        "description": "围绕袖口、版型、正面、背面和弹力设计组织动作。",
        "intent": "把服装卖点拆成可执行的连续动作",
    },
    "场景图（玩偶）": {
        "role": "儿童家居场景",
        "type": "场景图",
        "category": "儿童母婴",
        "platform": "国内",
        "ratio": "1:1",
        "description": "女孩与猫咪玩偶在卧室床铺上的温暖陪伴场景。",
        "intent": "传递陪伴入眠与安全柔软的情感价值",
    },
    "衣服上身（模特无中生有）": {
        "role": "童装上身",
        "type": "万能改图",
        "category": "儿童母婴",
        "platform": "国内",
        "ratio": "自动",
        "description": "从童装卫裤生成局部腰部至脚部的商业上身图。",
        "intent": "突出童装面料与版型细节",
    },
    "改造型": {
        "role": "商品局部改图",
        "type": "万能改图",
        "category": "服饰鞋帽",
        "platform": "国内",
        "ratio": "自动",
        "description": "只调整帽子形态，衣服图案、版型与颜色保持不变。",
        "intent": "在不破坏商品身份的前提下完成局部造型调整",
    },
    "详情页风格模仿": {
        "role": "详情页套图",
        "type": "电商海报",
        "category": "3C数码",
        "platform": "国内",
        "ratio": "3:4",
        "description": "将产品图放入参考详情页的使用图与细节图结构。",
        "intent": "用详情页结构回答产品使用与细节问题",
    },
    "我发现你们好爱抄": {
        "role": "原创电商海报",
        "type": "电商海报",
        "category": "3C数码",
        "platform": "国内",
        "ratio": "3:4",
        "description": "参考构图与风格关系，重新原创生成电动工具海报。",
        "intent": "在保留产品身份的同时获得更强的电商视觉表达",
    },
    "去掉文字": {
        "role": "文字清理",
        "type": "万能改图",
        "category": "3C数码",
        "platform": "国内",
        "ratio": "自动",
        "description": "移除源头工厂一件代发等画面文字，保持产品与背景。",
        "intent": "清理供应链文字以获得可直接使用的商品素材",
    },
}

# These prompts are the case-specific visual direction.  They are stored as
# canonical data so the API, admin editor, and browser all agree on one string.
# Reference roles use semantic names rather than relying on a fragile image
# ordinal; the provider receives product_asset_ids and reference_asset_ids as
# separate channels.
CANONICAL_PROMPT_BY_TITLE = {
    "生成AI模特": """[Product Identity Lock]
上传商品图是唯一产品身份来源；保持商品可见的轮廓、比例、材质、颜色、包装、Logo、印刷文字、部件与配件，不替换商品，不虚构不清楚的产品事实。
[Visual Strategy Lock]
根据商品所属品类选择匹配的成人 AI 模特与自然姿态；如果商品适合穿戴，让模特真实穿戴并清晰呈现版型，如果商品不适合穿戴，让模特以自然手持、使用或陪衬关系展示商品。使用干净商业摄影、合理景别与能看清商品的光线。
[Original Prompt]
生成一张可用于电商展示的原创模特商品图，让用户上传的商品成为主要展示对象，模特姿态自然、比例真实、商品完整可辨识，画面留出移动端可读的安全空间。只根据用户商品图生成，不引入任何案例商品或参考品牌。
[Forbidden Transfer]
不得复制其他案例的品牌、Logo、水印、原文案、数字、认证或未经确认的产品事实。""",
    "暖金梳妆台 · 花香时刻": """[Input Roles]
图一是 product_identity_reference，图二是 visual_strategy_reference。图一是唯一产品身份来源。
[Product Identity Lock]
透明方形玻璃瓶，粉色液体，银色纹理蝴蝶结，透明矩形瓶盖与喷头；保持瓶身比例、玻璃边缘、蝴蝶结、标签位置和用户产品上可见文字。只使用一个完整组装产品，不新增瓶型、瓶盖或变体。
[Visual Strategy Lock]
参考图只提供暖香槟梳妆台、紫色绒面、虚化镜面、珍珠、烛光、白花、手写标题和移动端纵向扫描关系。
[Original Remix Prompt]
Create an original 9:16 vertical mobile ecommerce poster. Place exactly one faithful perfume bottle slightly right in the lower-middle area with a soft contact shadow and glass reflection. Keep a two-line original handwritten headline in the upper quarter, a small ribbon below it, light paper-note callouts at mid-left and mid-right, and a small rounded note near the lower-right. Preserve the depth order background decor -> product hero -> foreground pearls/flowers -> reflection. Use warm champagne and lilac lighting; keep the label unobstructed and mobile-readable.
[Copy Boundary]
Use only visible product facts and original campaign copy; do not invent efficacy, ingredients, numbers or certifications.
[Forbidden Transfer]
不得迁移参考品牌、Logo、水印、原文案、原始品牌排版、功效声明、隐藏提示词或未经确认的产品事实。
[Post-Generation Review]
检查产品轮廓、包装文字、产品数量、文字区位置、参考品牌泄漏和移动端可读性。""",
    "人物与超大产品的接触关系": """[Input Roles]
图一是 product_identity_reference，图二是 visual_strategy_reference。图一是唯一产品身份来源。
[Product Identity Lock]
透明矩形玻璃香水瓶、粉色液体、银色蝴蝶结、喷头、透明瓶盖、标签布局和可见产品文字全部以图一为准。放大和手持瓶必须是同一产品的统一缩放，不得发明建筑台座、瓶盖或包装结构。
[Visual Strategy Lock]
参考图只提供 4:5 浅蓝高亮棚拍、右侧放大产品、左侧白衣人物、顶部背景文字、人物与产品倚靠/接触关系和底部镜面倒影。
[Original Remix Prompt]
Create an original vertical 4:5 ecommerce campaign poster. Put one uniformly enlarged faithful perfume bottle on the dominant right side, and one adult model in a crisp white outfit on the left, visibly leaning against the bottle with a natural support/contact point while holding a smaller faithful duplicate. Keep the bottle front-facing and recognizable, the floor reflection beginning near the bottom, and the large original typography device behind the subjects in the upper third. Preserve the scan path typography -> model/contact -> hero product -> reflection, with pale blue studio light and clean commercial spacing.
[Copy Boundary]
Write only original copy or visible user-product facts.
[Forbidden Transfer]
不得复制参考图的品牌、Logo、水印、原文案、数字、认证、专有包装或产品身份。
[Post-Generation Review]
检查人物与产品接触关系、放大瓶体几何、手持瓶一致性、倒影、文字区和参考品牌泄漏。""",
    "冷蓝水感 · 清爽洁护": """[Product Identity Lock] 以用户清扬产品图为唯一身份来源，保留白色按压瓶、银色泵头、紫色 CLEAR/清扬标识、粉色樱花区域与可见包装文字。
[Visual Strategy Lock] 从参考图提取深蓝至电光蓝背景、湿发人物、水花动势、冰块、左侧文字区与右下产品证明关系。
[Original Remix Prompt] 生成 2:3 洗护清爽卖点图，右侧人物近景，右下单一产品与水花，底部原创卖点栏，保持平台移动端扫描顺序。
[Forbidden Transfer] 不转移参考品牌、Logo、水印、原文案、未经确认的功效与参数。""",
    "场景复刻+指定模特": """[Product Identity Lock] 图一服装是唯一服装身份来源，保持面料、颜色、版型、拉链与细节。
[Visual Strategy Lock] 图二提供场景与镜头关系，图三提供模特关系；迁移户外构图、人物尺度与光影。
[Original Remix Prompt] 生成 1:1 场景图，让图三模特穿上图一服装，场景相似但不复刻，保持产品细节完全正确。
[Forbidden Transfer] 不转移参考品牌、Logo、水印与原始文案。""",
    "衣服上身（无中生有）": """[Product Identity Lock] 用户服装图是唯一身份来源，保留冲锋衣的连帽、多拉链口袋、字母标识与版型。
[Visual Strategy Lock] 提取童装商业摄影、东亚男孩、户外背包与登山杖的角色关系。
[Original Remix Prompt] 生成 3:4 写实童装上身图，模特穿着用户服装，户外装备辅助商品角色，背景与光线服务服装细节。
[Forbidden Transfer] 不虚构服装结构、品牌、认证或参考水印。""",
    "改语言语种": """[Product Identity Lock] 保留用户原图中的产品与版式事实。
[Visual Strategy Lock] 保持原构图、字体层级、对齐和留白。
[Original Remix Prompt] 将中文内容准确改为英文，不改产品、颜色、尺寸、结构或信息层级。
[Forbidden Transfer] 不增加未经确认的卖点与数字。""",
    "场景图（服装）": """[Product Identity Lock] 以用户服装图为唯一身份来源，保留款式、颜色、面料与搭配关系。
[Visual Strategy Lock] 提取 CCD 复古质感、米白墙面、编织地毯、双人自拍与柔和自然光。
[Original Remix Prompt] 生成 9:16 室内真实人像场景，两位人物自然并肩，服装细节准确，背景与人物光影融合。
[Forbidden Transfer] 不转移参考人物身份、品牌、文字与水印。""",
    "模特换衣（让它看细节版）": """[Product Identity Lock] 参考输入中的同一睡衣睡裤套装是唯一服装身份来源，细节图用于锁定材质与结构。
[Visual Strategy Lock] 保持原模特姿态、镜头与场景，只迁移服装。
[Original Remix Prompt] 生成 3:4 换装图，准确还原睡衣睡裤的款式、颜色、面料、褶皱和细节。
[Forbidden Transfer] 不改变模特身份、不添加单品、不转移品牌与水印。""",
    "卖点图（多姿势展示）": """[Product Identity Lock] 以现有模特与服装为身份来源，保持毛衣袖口、下摆、版型和纹理。
[Visual Strategy Lock] 提取正面主图、细节动作、背面图和视频封面的组合逻辑。
[Original Remix Prompt] 编译多姿势电商卖点图方案：侧身展示版型，抬手展示弹力，正面无遮挡，背面补充结构，布景极简。
[Forbidden Transfer] 不改变服装结构、颜色、材质或模特身份。""",
    "场景图（玩偶）": """[Product Identity Lock] 用户玩偶图是唯一玩偶身份来源，保持长度、颜色、材质和轮廓。
[Visual Strategy Lock] 提取中近景、白色床品、暖黄床头灯、浅米色墙面与柔和暖光。
[Original Remix Prompt] 生成 1:1 写实儿童家居场景，女孩侧卧环抱玩偶，背景虚化突出产品互动与陪伴感。
[Forbidden Transfer] 不虚构玩偶结构、功效、安全认证或品牌文案。""",
    "衣服上身（模特无中生有）": """[Product Identity Lock] 图一卫裤是唯一产品身份来源，保持麻灰毛圈纹理、缝线、束脚与自然褶皱。
[Visual Strategy Lock] 提取日系休闲童装、白色哑光摄影棚、局部构图与柔和漫射光。
[Original Remix Prompt] 生成自动比例的局部童装实拍，只展示腰部至脚部，干净背景，产品细节清晰。
[Forbidden Transfer] 不改变产品版型、面料、颜色或图案。""",
    "改造型": """[Product Identity Lock] 用户服装图是唯一身份来源，锁定衣服图案、版型、颜色和结构。
[Visual Strategy Lock] 只迁移目标帽型的自然立体关系。
[Original Remix Prompt] 让帽子自然向上立起、帽型挺立不塌陷，其他产品区域像素级保持。
[Forbidden Transfer] 禁止改色、改款、增删装饰或改变产品身份。""",
    "详情页风格模仿": """[Product Identity Lock] 电动扳手产品图是唯一身份来源，保持外壳、颜色、按钮、附件与文字。
[Visual Strategy Lock] 参考图只提供详情页模块、精致排版、信息层级与使用场景关系。
[Original Remix Prompt] 生成 3:4 中文电商详情页视觉套图，包含使用图与细节图，标题只依据产品事实原创。
[Forbidden Transfer] 不把参考图产品、品牌、文案或参数带入结果。""",
    "我发现你们好爱抄": """[Product Identity Lock] 图一电动扳手是唯一产品身份来源。
[Visual Strategy Lock] 图二只提取构图、色彩、信息层级和商业角色，不复制表达。
[Original Remix Prompt] 生成 3:4 原创淘宝电商海报，中文精致排版，高级审美，产品事实准确。
[Forbidden Transfer] 不复制参考品牌、Logo、水印、原始文案、数字或产品外观。""",
    "去掉文字": """[Product Identity Lock] 用户原图中的产品、结构、材质与包装是唯一身份来源。
[Visual Strategy Lock] 保持原画面构图、背景、光影和产品位置。
[Original Remix Prompt] 清除指定文字区域并自然修复背景纹理，不改变产品与其他信息。
[Forbidden Transfer] 不改产品结构、不新增文案、不引入品牌或水印。""",
}

# SHA-256 values are used instead of filenames because imported/generated
# assets can share filenames.  The migration only links assets that already
# exist; the operator import script supplies any missing admin references.
CANONICAL_ASSETS_BY_TITLE = {
    "暖金梳妆台 · 花香时刻": (
        ("case-perfume-product.png", "user_upload", 0),
        ("case-perfume-vanity-reference.jpg", "reference", 0),
        ("perfume-vanity.png", "result", 0),
    ),
    "人物与超大产品的接触关系": (
        ("case-perfume-product.png", "user_upload", 0),
        ("case-perfume-blue-reference.jpg", "reference", 0),
        ("perfume-blue-studio.png", "result", 0),
    ),
    "冷蓝水感 · 清爽洁护": (
        ("case-clear-product.png", "user_upload", 0),
        ("case-clear-reference.jpg", "reference", 0),
        ("clear-blue-fresh.png", "result", 0),
    ),
    "场景复刻+指定模特": (
        ("focus-00-input-1.jpg", "user_upload", 0),
        ("focus-00-input-2.png", "reference", 0),
        ("focus-00-input-3.png", "reference", 1),
        ("focus-00-result.png", "result", 0),
    ),
    "衣服上身（无中生有）": (
        ("focus-01-input.png", "user_upload", 0),
        ("focus-01-result.png", "result", 0),
    ),
    "改语言语种": (
        ("focus-02-input.jpg", "user_upload", 0),
        ("focus-02-result.png", "result", 0),
    ),
    "场景图（服装）": (
        ("focus-03-input-1.png", "user_upload", 0),
        ("focus-03-input-2.png", "reference", 0),
        ("focus-03-result.jpg", "result", 0),
    ),
    "模特换衣（让它看细节版）": (
        ("focus-04-input-1.png", "user_upload", 0),
        ("focus-04-input-2.png", "reference", 0),
        ("focus-04-input-3.png", "reference", 1),
        ("focus-04-input-4.jpg", "reference", 2),
        ("focus-04-result.jpg", "result", 0),
    ),
    "卖点图（多姿势展示）": (("focus-05-result.jpg", "result", 0),),
    "场景图（玩偶）": (
        ("focus-06-input-1.png", "user_upload", 0),
        ("focus-06-result.png", "result", 0),
    ),
    "衣服上身（模特无中生有）": (
        ("focus-07-input.jpg", "user_upload", 0),
        ("focus-07-result.jpg", "result", 0),
    ),
    "改造型": (
        ("focus-08-input.jpg", "user_upload", 0),
        ("focus-08-result.jpg", "result", 0),
    ),
    "详情页风格模仿": (
        ("focus-09-input-1.jpg", "user_upload", 0),
        ("focus-09-input-2.jpg", "reference", 0),
        ("focus-09-result.png", "result", 0),
    ),
    "我发现你们好爱抄": (
        ("focus-10-input-1.jpg", "user_upload", 0),
        ("focus-10-input-2.jpg", "reference", 0),
        ("focus-10-result.png", "result", 0),
    ),
    "去掉文字": (
        ("focus-11-input.jpg", "user_upload", 0),
        ("focus-11-result.png", "result", 0),
    ),
}

CANONICAL_SHA_BY_FILENAME = {
    "perfume-vanity.png": "de28136bf89f9e91b0a8b90aba69b3b69124f6c9a07e8ed096b81b2983a3a818",
    "case-perfume-product.png": "68613c037eef32f0276c4d3cf1cc811ca08b28e98b7b2f34de19d76a7e8fbc4b",
    "case-perfume-vanity-reference.jpg": "59ee54e641d5f762720c3913bc31e76dea4434bcc11c47a0723d0d929525016f",
    "perfume-blue-studio.png": "f10976c87a68b928e505de0f1820e59a4e5490e9ff89401ab29f00b46bfad139",
    "case-perfume-blue-reference.jpg": "f004a2bbadce651b205cca842eef918e71770fa84d7263c90a50a8ed1e005495",
    "clear-blue-fresh.png": "90e472f860db3d472750f28e3bf033fdb1705780288b8878bab91ab3a824b56f",
    "case-clear-product.png": "bb8e28cf2914a3f20ec0f46c77444e58005375f551abfbe7e38f8676602caf06",
    "case-clear-reference.jpg": "2366da90c659c67bc3600252ba5c66cf043c61c0e6d05ec21baec253ffb91949",
    "focus-00-input-1.jpg": "0b469e674ee5d60249e304c78b457da851c1179da64b0ef6ca511b7b6a7df795",
    "focus-00-input-2.png": "5dfb0c9bf29c2055969ddcf54d1a5ba545a8af712f6b82d493cbe511ba8c3c0c",
    "focus-00-input-3.png": "c18a03f56c8ae24379eb9086737d428dc196f2113a5dcd26311f4cb702db0a88",
    "focus-00-result.png": "4ef7de748dbc53ee89e1ec0826be589828a3b604c419b54a29c28760d3f3f22b",
    "focus-01-input.png": "2369444c01f8dd2554e6b438c14d097f496d20c2a68cfced3e52564006076143",
    "focus-01-result.png": "51d274c1460fc095ae7f96c668600789707074167b7b74ebb1dc38f3c0ee9601",
    "focus-02-input.jpg": "f301d76e78889af2a5fa9a33c9bb0608c51f638786a0b19d370ac0653d2a0725",
    "focus-02-result.png": "a67cefd07747f6eac1e17795a16da83280d8cc190c05ef4500373786f6e8e4d7",
    "focus-03-input-1.png": "95ce61f2688a7e8625eb70cd2b80ee07db8e2e7bb6045a762203614288c86c97",
    "focus-03-input-2.png": "0a6504ad8f6623ca1f147186a025ac398a26a5fcad22246549d7f40995284f72",
    "focus-03-result.jpg": "7d71afbf8ef78136972545c466bd3ab43c5a3fc4ec90e24876a916dd0023c1f2",
    "focus-04-input-1.png": "03814424ddc5c6221be841e3ee8b74c6cc8a954dc430e5a0197c4225862008f0",
    "focus-04-input-2.png": "755fdceec1e643dbb899f3681e2048454931201350398ae54c41f35fd00ca148",
    "focus-04-input-3.png": "6b3c4269921743c4305ac0c951175ebc44aa16ecfa7aa05e542bf12093db5ba7",
    "focus-04-input-4.jpg": "c980d9f13221b48dce315744264c12ec62b7074325f5eaf1f1984f649f5e6e14",
    "focus-04-result.jpg": "c0c1c18541d7fa472f9723daa25862043c37305a47277dd0b17ef3dc8710d718",
    "focus-05-result.jpg": "3d78bb9312c577ca39e082efef850bc7b3b9b403c7df51f57171f22a8532dd47",
    "focus-06-input-1.png": "4824230a575725e1d117bcdf62e4c3b4da32c3ed7344efe125c5039f493a697a",
    "focus-06-result.png": "df20f0a07f40baf72b4d23584d9c5c1101e165aa2160af0db3f2991dc8f34f2a",
    "focus-07-input.jpg": "3785e582b463dc3241cc6f144d8fd474a6a87d30cf87196b62dfde657d111f79",
    "focus-07-result.jpg": "fabb1c076fe43f10a919170f37481a55049443b618c69b5edbf5fcc9ba833f7a",
    "focus-08-input.jpg": "e7a8f0956c2d5be0fb3c13b0fef0189ad42efbc3c9edee882ecc57f06aef5954",
    "focus-08-result.jpg": "cfcc0bec8eb675df09938d45645315d92a43df0b69a12ae2df0f0c5fd1d11b2c",
    "focus-09-input-1.jpg": "68e3ec96811503633c4a0f3af3b0e482176451abae931fe9f3dd657b157f7b6c",
    "focus-09-input-2.jpg": "c805150a0cd5d5215467a5f68603b00f3038e8cda910409286d516c8f1d7ef7d",
    "focus-09-result.png": "2228abae6901333f1ca8096479e90822d39573e66107eb7fa1d7f244f7a2d3a7",
    "focus-10-input-1.jpg": "68e3ec96811503633c4a0f3af3b0e482176451abae931fe9f3dd657b157f7b6c",
    "focus-10-input-2.jpg": "c0822a7241c8aa4b969bb47d10555a5a32755ee25526d61efbc926f9dfd22ce0",
    "focus-10-result.png": "275249a2923223462e7ecd2683d7211088c6cd6d955a6afc995d80b90eb8f254",
    "focus-11-input.jpg": "9f0cc95da2afc53c53fc18a8b880921c03408aea58a9af0c049c2741e8cb38e6",
    "focus-11-result.png": "3a42690230f829d64c5377b0bce41f3f2b06e7ca738421362d73ab6bd4cd8939",
}

# The historical "生成AI模特" demo has no verified product/result pair.  It
# intentionally has no static asset mapping and must be supplied separately;
# never borrow focus-00 assets to make it appear configured.
CASES_REQUIRING_ASSET_REVIEW = ("生成AI模特",)

assert len(FINAL_INSPIRATION_WORKFLOW_BY_TITLE) == 16
assert set(CANONICAL_PROMPT_BY_TITLE) == set(FINAL_INSPIRATION_WORKFLOW_BY_TITLE)
assert set(CANONICAL_ASSETS_BY_TITLE) | set(CASES_REQUIRING_ASSET_REVIEW) == set(
    FINAL_INSPIRATION_WORKFLOW_BY_TITLE
)
