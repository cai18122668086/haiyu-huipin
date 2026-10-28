"use client";

import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  Lightbulb,
  ShieldCheck,
} from "lucide-react";

import {
  presentWorkflow,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";

export function HelpPanel({
  mode,
  taskTitle,
  assetSummary,
  outputSummary,
  currentStep,
}: {
  mode: "local-edit" | "detail-pack";
  taskTitle?: string;
  assetSummary?: string;
  outputSummary?: string;
  currentStep?: number;
}) {
  const local = mode === "local-edit";
  const runtime = useRuntimeStatus();
  const workflow = presentWorkflow(
    local
      ? runtime.data?.capabilities.local_edit
      : runtime.data?.capabilities.detail_pack,
  );
  const providerEnabled = workflow.canDeliverImages;
  const mockEnabled = workflow.mode === "mock";
  const steps = local
    ? [
        "上传清晰原图",
        "绘制并确认授权选区",
        "描述期望变化",
        providerEnabled
          ? "检查结果并下载精修图片"
          : mockEnabled
            ? "完成操作预览；当前暂不生成图片"
            : "等待生成服务恢复后提交任务",
      ]
    : [
        "上传同一商品多角度图",
        "完善卖点与渠道信息",
        "后台规划并校验页面",
        providerEnabled
          ? "逐页生成并检查图片结果"
          : mockEnabled
            ? "完成流程预览；当前暂不生成图片"
            : "等待生成服务恢复后开始生成",
      ];

  return (
    <aside className="help-panel">
      {!local && <figure className="workspace-example"><img src="/images/homepage-features/native-v2-webp/04-detail-primary-desktop.webp" alt="商品详情图的视觉与排版参考" width={1374} height={1145} /><figcaption>从商品卖点，到完整的详情视觉</figcaption></figure>}
      <div className="help-panel__title">
        <span>
          <BookOpen size={17} />
        </span>
        <div>
          <strong>创作助手</strong>
          <p>
            {providerEnabled
              ? "跟随步骤完成创作并检查结果"
              : mockEnabled
                ? "当前可预览操作流程，暂不生成图片"
                : "当前生成服务暂不可用"}
          </p>
        </div>
      </div>
      <section className="help-card current-summary">
        <div className="help-card__head">
          <span>当前任务</span>
          <em>{local ? "随输入更新" : "已自动保存"}</em>
        </div>
        <strong>{taskTitle || (local ? "等待填写修改要求" : "等待填写商品名称")}</strong>
        <div className="summary-row">
          <span>{local ? "编辑画布" : "商品素材"}</span>
          <b>{assetSummary || "尚未上传"}</b>
        </div>
        <div className="summary-row">
          <span>{local ? "生成服务" : "输出规格"}</span>
          <b>
            {local
              ? providerEnabled
                ? "可生成 · 支持下载"
                : mockEnabled
                  ? "流程预览 · 暂无图片"
                  : "暂不可用 · 暂无图片"
              : outputSummary || "等待选择输出规格"}
          </b>
        </div>
      </section>
      <section className="help-card">
        <div className="help-card__head">
          <span>操作路径</span>
          <em>{local ? "约 2 分钟" : "约 8 分钟"}</em>
        </div>
        <ol className="help-steps">
          {steps.map((step, index) => (
            <li
              key={step}
              aria-current={index === (currentStep ?? 2) ? "step" : undefined}
              className={index < (currentStep ?? 2) ? "done" : index === (currentStep ?? 2) ? "current" : ""}
            >
              <i>{index < (currentStep ?? 2) ? <CheckCircle2 size={15} /> : index + 1}</i>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      </section>
      <section className="help-card suggestion-card">
        <div className="help-card__icon">
          <Lightbulb size={17} />
        </div>
        <div>
          <strong>{local ? "选区边缘建议" : "素材一致性建议"}</strong>
          <p>
            {local
              ? providerEnabled
                ? "选区比目标多覆盖 8–16px，有助于自然融合；生成结果仍只修改授权区域。"
                : mockEnabled
                  ? "选区将作为可修改范围保存；当前仅支持流程预览，不会生成图片。"
                  : "生成服务暂不可用；选区可继续编辑，恢复后可提交生成。"
              : "使用相同光线与焦段的多角度照片，可提升商品结构识别与跨页一致性。"}
          </p>
        </div>
      </section>
      {local ? (
        <section className="help-card safety-card">
          <div className="help-card__icon">
            <ShieldCheck size={17} />
          </div>
          <div>
            <strong>授权边界状态</strong>
            <p>
              {providerEnabled
                ? "原图是唯一画布，选区外像素将经过严格比对，不合格结果不会覆盖原始产物。"
                : mockEnabled
                  ? "当前可检查原图、选区与任务流程；暂不提供生成结果下载。"
                  : "生成服务暂不可用；不会提交生成任务或提供下载结果。"}
            </p>
          </div>
        </section>
      ) : null}
      <button className="help-more">
        查看完整使用指南
        <ChevronRight size={16} />
      </button>
      {!local && (
        <div className="risk-note">
          <AlertTriangle size={15} />
          <p>
            {providerEnabled
              ? "页面规划确认完成前，不会开始图片生成。"
              : mockEnabled
                ? "可继续预览操作流程，当前暂不生成图片。"
                : "生成服务暂不可用，规划确认入口暂时关闭。"}
          </p>
        </div>
      )}
    </aside>
  );
}
