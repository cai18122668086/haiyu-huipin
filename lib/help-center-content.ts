export type HelpCategoryId =
  | "getting-started"
  | "assets"
  | "tasks"
  | "retention"
  | "troubleshooting";

export const HELP_CATEGORIES = [
  { id: "all", label: "全部" },
  { id: "getting-started", label: "开始使用" },
  { id: "assets", label: "素材上传" },
  { id: "tasks", label: "任务与结果" },
  { id: "retention", label: "保存期限" },
  { id: "troubleshooting", label: "问题排查" },
] as const;

export type HelpTopic = {
  id: string;
  category: HelpCategoryId;
  title: string;
  summary: string;
  items: readonly string[];
  keywords: readonly string[];
};

export const HELP_TOPICS: readonly HelpTopic[] = [
  {
    id: "first-task",
    category: "getting-started",
    title: "完成第一个生成任务",
    summary: "从选功能、准备素材到下载结果的完整路径。",
    items: [
      "先按最终用途选择功能；不确定时可在下方“全部功能”中比较输入和交付结果。",
      "上传同一商品的清晰原图，补充真实卖点、目标平台、画面比例和必须保留的细节。",
      "提交后可以离开当前页面，任务会继续运行；从顶部“任务”或“生成记录”查看进度。",
      "任务完成后逐张检查商品结构、文字和卖点，再下载单图或功能支持的完整结果包。",
    ],
    keywords: ["新手", "第一次", "怎么用", "创建任务", "下载"],
  },
  {
    id: "choose-source-and-reference",
    category: "assets",
    title: "区分商品原图与参考图",
    summary: "商品原图决定主体身份，参考图只用来说明目标风格或构图。",
    items: [
      "商品原图应完整展示本次要生成的同一商品，不要把不同款式、颜色或规格混在一组。",
      "参考图可以表达背景、光线、构图、模特或姿势，但不会替代商品原图中的真实结构。",
      "服装和带 Logo 商品尽量提供正面、侧面及关键细节图，减少纹理、印花和结构误差。",
    ],
    keywords: ["商品图", "参考图", "素材分组", "多角度", "logo", "原图"],
  },
  {
    id: "upload-requirements",
    category: "assets",
    title: "图片上传要求",
    summary: "支持 JPG/JPEG、PNG、WebP；单张不超过 20MB、4000 万像素。",
    items: [
      "优先上传对焦清晰、主体完整、无强压缩噪点的原图，避免截图、拼接图和带大面积遮挡的图片。",
      "上传失败时先检查格式、20MB 文件大小和 4000 万像素限制，再重新选择文件。",
      "浏览器提示网络中断时不要连续重复提交；恢复网络后重新上传，并确认素材缩略图已出现。",
    ],
    keywords: ["JPG", "JPEG", "PNG", "WebP", "20MB", "4000万像素", "上传失败", "格式"],
  },
  {
    id: "job-statuses",
    category: "tasks",
    title: "看懂任务状态",
    summary: "不同状态对应不同的下一步，不需要反复提交同一个任务。",
    items: [
      "排队中、规划中、处理中：任务仍在运行，可以离开页面，稍后从生成记录查看。",
      "待确认：旧任务可能需要打开方案继续；新套图在后台规划校验后自动生成。",
      "部分成功：已成功的图片可以先下载，只重试失败项，不必重做整套任务。",
      "已完成、失败、已取消：任务已经结束；失败时打开详情查看错误摘要和可用的重试按钮。",
    ],
    keywords: ["排队", "规划", "待确认", "处理", "部分成功", "完成", "失败", "取消", "卡住"],
  },
  {
    id: "results-and-downloads",
    category: "tasks",
    title: "检查、重试和下载结果",
    summary: "先看任务详情，再按单项处理失败结果。",
    items: [
      "结果区暂时没有图片时，先看任务状态；仍在运行就稍后刷新，失败则查看错误摘要。",
      "出现“部分成功”时保留已成功结果，只对失败项执行重试。按钮不可用通常表示当前状态不允许该操作。",
      "图片链接加载失败或下载失败时刷新页面获取新的访问链接；完整套图可在任务详情中下载结果包。",
      "下载后请在投放前核对商品外观、Logo、文字、规格和平台规则，AI 结果不应代替人工审核。",
    ],
    keywords: ["没有结果", "图片不显示", "重试", "下载失败", "ZIP", "结果包", "按钮不可用"],
  },
  {
    id: "retention-policy",
    category: "retention",
    title: "素材、图片和记录保存 30 天",
    summary: "到期内容会被清理且不可恢复，请在到期前下载需要留存的文件。",
    items: [
      "用户上传素材从上传成功起保存 30 天。",
      "生成图片从创建成功起保存 30 天。",
      "完成、失败或取消等终态任务记录，从任务结束起保存 30 天。",
      "尚未提交的草稿从创建起保存 30 天。到期后相关内容不可恢复，请提前下载备份。",
    ],
    keywords: ["30天", "一个月", "保存多久", "过期", "到期", "删除", "不可恢复", "草稿"],
  },
  {
    id: "task-taking-too-long",
    category: "troubleshooting",
    title: "任务很久没有结果",
    summary: "先判断任务是在等待操作、正常运行，还是已经失败。",
    items: [
      "打开生成记录查看最新状态和更新时间；旧任务的“待确认”需要继续确认，新套图将自动规划并生成。",
      "仍显示排队、规划或处理时不要重复提交，否则会创建多个独立任务并重复消耗额度。",
      "如果状态长时间没有变化，联系支持时附上任务编号、提交时间、功能名称、当前状态和页面截图。",
    ],
    keywords: ["很久", "卡住", "一直生成", "没结果", "重复提交", "国内电商主图"],
  },
  {
    id: "action-unavailable",
    category: "troubleshooting",
    title: "提交、确认或重试按钮不可用",
    summary: "按钮只会在输入完整且任务状态允许操作时启用。",
    items: [
      "提交前检查必填文字、商品素材、参考素材或授权选区是否完整，上传是否已经结束。",
      "确认按钮只在待确认阶段可用；重试按钮只会出现在可重试的失败任务或失败项上。",
      "如果页面状态与生成记录不一致，刷新后从生成记录重新打开任务详情。",
    ],
    keywords: ["按钮灰色", "不能提交", "不能确认", "不能重试", "必填", "选区"],
  },
];

export type HelpFaq = {
  id: string;
  category: HelpCategoryId;
  question: string;
  answer: string;
  keywords: readonly string[];
};

export const HELP_FAQS: readonly HelpFaq[] = [
  {
    id: "leave-page",
    category: "tasks",
    question: "提交后可以关闭页面吗？",
    answer: "可以。任务提交成功后会在后台继续运行，你可以切换功能或关闭当前页面，之后从顶部“任务”或“生成记录”查看进度。不要因为页面暂时没有结果而重复提交。",
    keywords: ["关闭页面", "后台运行", "切换页面"],
  },
  {
    id: "partial-success",
    category: "tasks",
    question: "“部分成功”是什么意思？",
    answer: "套图中的一部分已经生成，另一部分失败。成功图片仍可查看和下载；进入任务详情，只重试失败项即可，不需要重新创建整套任务。",
    keywords: ["部分成功", "失败项", "重试"],
  },
  {
    id: "awaiting-confirmation",
    category: "tasks",
    question: "为什么任务一直显示“待确认”？",
    answer: "新建套图会在后台规划、校验并自动生成，无需逐项确认方案。历史任务保留原流程；若旧任务显示待确认，请打开任务详情继续。",
    keywords: ["待确认", "方案", "不继续"],
  },
  {
    id: "download-failed",
    category: "troubleshooting",
    question: "图片能预览但下载失败怎么办？",
    answer: "先刷新页面以获取新的图片访问链接，再重新下载；套图优先从任务详情下载完整结果包。如果仍然失败，请把任务编号、图片序号和报错截图发给支持邮箱。",
    keywords: ["下载", "链接失效", "预览", "结果包"],
  },
  {
    id: "local-edit-selection",
    category: "getting-started",
    question: "一句话改图为什么要画授权选区？",
    answer: "选区是本次任务允许修改的范围。请只覆盖需要变化的区域，并在描述中写明材质、颜色和必须保留的内容；选区外会按严格锁定规则处理。",
    keywords: ["一句话改图", "选区", "蒙版", "像素锁定"],
  },
  {
    id: "mixed-products",
    category: "assets",
    question: "可以把不同款式或颜色放进同一组素材吗？",
    answer: "不建议。一个任务的商品素材应属于同一商品和同一规格；需要生成不同款式或颜色时分别创建任务，避免主体身份和细节相互混淆。",
    keywords: ["不同商品", "款式", "颜色", "分组"],
  },
  {
    id: "domestic-main-output",
    category: "getting-started",
    question: "国内电商主图会生成哪些图片？",
    answer: "每个任务会先提供六图方案供确认，再生成 1 张搜索主图和 5 张轮播图。请上传同一商品的真实图片，并只填写已经核实的卖点。",
    keywords: ["国内电商主图", "1张搜索主图", "5张轮播图", "六图"],
  },
  {
    id: "expiration",
    category: "retention",
    question: "内容到期后还能恢复吗？",
    answer: "不能。用户上传素材、生成图片、终态任务记录和草稿均按各自起算时间保留 30 天，到期清理后不可恢复。请在到期前下载需要长期留存的原图和结果。",
    keywords: ["恢复", "过期", "30天", "备份"],
  },
];

export const SUPPORT_EMAIL = "main@haiyushuke.cn";
