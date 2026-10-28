# 详情页：一张母图包含上下两屏

路由确定为 DETAIL_PAGE 后保持此路由；前置命令失败不构成切换到单图路由的理由。默认 S01–S10、G01–G05，不因原图为总览或只上传一张参考图而改变。

1. 按 [reference-analysis.md](reference-analysis.md) 真实逐屏拆解，读完逐屏文字和整套叙事；通过 Passport。详情页目标屏不预设固定的“第几屏讲什么”清单，只绑定对应的物理对标步骤、密度样本和相邻边界；展开逻辑由这些对标步骤驱动。每个 Pair 还必须携带 `target_reference_sequence`，按 source order 只绑定当前 Pair 对应的单屏对标裁剪图，并把源步骤、内容职责、表达动作和交接写入提示；不能把整套参考图无序地传给每个 Pair。
2. register-product-facts 登记当前产品事实，plan-target-page 写出全部目标屏，audit-plan 审核。全套目标必须覆盖全部 source_content_step_ids，允许根据用户数量要求合并或展开，仍须保持故事衔接。
3. 执行 `generate-pair-request --workspace <run> --group-id G01`。
4. 阅读 `artifacts/generation-request-G01.json` 及 `artifacts/imagegen-prompt-G01.txt`。generation_scope 明确目标只有 S01/S02、单张母图、上下各自 bbox；current_target_plan 只含这两屏。完整计划是上下文，不是本次要画的全部屏。txt 是给模型的短指令，代码限制为 12,000 字符；不要把 JSON 原文复制到 prompt。
5. 显式调用 artflow 生图 provider：`python scripts/artflow_render.py --workspace <run> --request <run>/artifacts/generation-request-Gxx.json --output <run>/actual-pair-Gxx.png`。传入当前产品绑定与清洗风格裁图绑定作为 referenced_image_paths；先看这些文件。以生成的 prompt 为合同，每次画一张宽高比 3:8 的连续竖向 Pair 母图：两屏必须占同一整幅宽度的上、下半区，以横向接缝承接；禁止左右分栏、竖直分隔线、长栏重排或用额外内容填满画布。每屏只执行自己的 density contract 和 visible_copy，文字与画面同时由模型生成。总览及完整分析屏裁图不进入生图附件。
6. 检查工具返回的原始文件，不用聊天缩略图。用下方脚本比较工具原始文件与 run 内的原样副本，再执行 `record-pair-artwork --workspace <run> --group-id G01 --artwork <实际母图路径> --render-receipt <render-receipt.json>`。脚本读取双方真实尺寸和 SHA-256；原生文件必须满足请求中的画布，副本必须逐字节相同。artflow provider 会把真实像素、比例与 SHA-256 写入旁路回执；那只是本地文件核验，不是服务商签章。单屏、并排图、低分辨率文件或放大文件不能代替两屏母图。失败时保留原生文件和问题记录；不能手写一个 `resized: false` 绕过核验。
7. 查看实际母图，对照已记录的视觉机制、产品和叙事审查，写对应来源类别的审查证据；执行 audit-pair，通过后 accept-pair。若失败，prepare-revision 后重新 generate-pair-request，最多一次定向修订。

首组 G01 必须使用宽高比 3:8 的 Pair 母图；G02 之后继续使用同一比例，避免同一详情页各屏跳比例。画面中不得输出 S01、S02、G01 等内部编号。
8. 完成 G01 后依次 G02（S03/S04）、G03（S05/S06）、G04（S07/S08）、G05（S09/S10）。不并行生成，不跳组，不覆盖等候返回的请求。
9. 全部通过后 audit-global、finalize、export-single-screens，最终导出十张保持母图原生宽度、等高切片的独立 PNG。生成时五张母图，交付时十屏。

显式 8–12 屏覆盖保留；用户指定奇数时，最后一组是明确标记 detail_single_tail 的单屏 1440×1920，前面各组仍为两屏。默认十屏永远没有单屏尾组。

每条命令都返回 next_action。外部工具暂时不可用时保留请求，恢复后用 `resume` 接着处理。不要把“生成请求已写出”或 fixture 空白母图说成真实生图成功。

宽高比 3:8 和 high 质量是生成要求，工具是否实际返回清晰原生文件必须验证。artflow provider 接受显式画布与 quality：3:8 作为生成尺寸下发，并在返回文件上复核真实像素与比例。如果工具只返回低清文件，先确认是否有对应原始高清文件可取；确实无法满足时保留失败证据并如实报告外部能力限制，不放大、不静默换成分屏或单屏产出。

```text
py -3.12 scripts/render_provenance.py --request <run>/artifacts/generation-request-G01.json --native-output <工具返回的原始文件路径> --artwork <原样复制的母图路径> --output <run>/artifacts/render-G01.json
```

回执含 `native_output_path`、`native_sha256`、`native_dimensions`、`path`、`output_sha256` 和 `verification: local_file_comparison_not_provider_attestation`。保留原始文件到审查完成；它证明两份文件的尺寸和字节关系，不能自动证明模型来源、真实锐度或中文字形正确。原始路径必须来自真实工具返回，不能用放大文件冒充。所有生产审查必须为人工或视觉模型真实观察，禁止使用 fixture 审查交付真实图片。

每个物理参考屏还必须记录 visual_density（文字块、卡片、可见字数、主视觉和留白比例）及观察说明；每个相邻参考屏必须记录 transitions。目标屏引用一个密度样本，visible_copy 只列本屏要显示的文字；英雄、证明、收束屏可以有不同疏密，density contract 是硬上限，禁止新增标题、解释段、细节图、参数行或第二主视觉，禁止用全套平均值填成同一组卡片。专利、检测、认证和性能事实是否出现由对标该步骤的内容职责决定；资料存在本身不产生覆盖、恰好一次或跨组去重要求。末屏的收束职责只存在于计划和 prompt 的内部字段，禁止把“收束/总结/结尾”等词画进成品。产品本体不得出现未经确认的产品名、品牌名、Logo 或 wordmark。
如果一个源步骤因为详情页长度被展开到多个目标屏，按对标观察到的展开方式处理；对标重复就保留重复，对标区分才要求区分。不要为了满足 Skill 自定义的扩展备注或卖点去重规则而改写对标逻辑。

详情页文案也遵守四档合规：普通规格、材质、质地、容量和用法不因潜在跨品类风险而被删；专利、认证、检测、量化、排名、比较和强安全/功效宣称需绑定事实证据；医疗或功效敏感词按品类复核；明确禁止表达直接失败。合规门不能把对标要求的正常信息密度压成短词。若对标使用胶囊、徽章、贴纸、标签云或重复圆角卡片承载短词，保留其层级和节奏但改用开放式正文、标题、注释、引线或分栏表达，不能复制这些 AI 标签载体。
