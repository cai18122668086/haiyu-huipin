"""Original prompt-only cards from f86d232's 18-card gallery.

Their historical covers were unrelated pose-library placeholders, not verified
generated results. Preserve the original copy without importing those covers.
"""

LEGACY_PROMPT_TEMPLATES = (
    {
        "slug": "detail",
        "title": "放大看见工艺",
        "role": "细节特写",
        "category": "饰品",
        "platform": "跨境",
        "type": "细节图",
        "description": "主体加一个局部放大窗，移动端也能读懂细节。",
        "prompt": (
            "整合用户确认的产品细节，使用微距镜头、硬朗轮廓光和简短原创标注；"
            "禁止补造不存在的材质、认证或参数。"
        ),
        "ratio": "4:5",
        "intent": "把‘品质好’变成可见证据",
    },
    {
        "slug": "white",
        "title": "清晰、克制的第一眼",
        "role": "平台合规主图",
        "category": "3C数码",
        "platform": "国内",
        "type": "主图",
        "description": "干净白底与准确比例，适配搜索流量。",
        "prompt": (
            "白底图策略：纯净白背景、自然接触阴影、产品居中且不裁切；"
            "用户产品图是唯一身份权威，禁止替换 Logo 或产品结构。"
        ),
        "ratio": "1:1",
        "intent": "提升搜索页识别度与点击率",
    },
    {
        "slug": "poster",
        "title": "一张图说清购买理由",
        "role": "活动电商海报",
        "category": "食品饮料",
        "platform": "跨境",
        "type": "电商海报",
        "description": "大留白、强层级，适合节日活动与站外广告。",
        "prompt": (
            "采用上方原创标题、下方产品证明的海报结构；只使用用户提供的卖点，"
            "橙色作为品牌强调色，不转移参考品牌资产。"
        ),
        "ratio": "4:5",
        "intent": "让信息在三秒内完成扫描",
    },
)
