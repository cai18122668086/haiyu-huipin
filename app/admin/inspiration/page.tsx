"use client";

import { useEffect, useState, type ChangeEvent } from "react";
import { Archive, ImagePlus, Loader2, Plus, Save, Send, Sparkles, Trash2, X } from "lucide-react";
import { AdminShell } from "@/components/admin/admin-shell";
import { getAssetAccessUrl, useUploadAsset } from "@/features/assets/api/assets";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import styles from "./page.module.css";

type AssetRole = "result" | "user_upload" | "reference";
type WorkflowType = "reference_remix" | "product_prompt" | "local_edit" | "legacy_unclassified";
type ReferenceSource = "admin_reference" | "result_image" | "none";
type Asset = { asset_id: string; role: AssetRole; ordinal: number; url: string };
type Item = { id: string; title: string; description: string; role: string; type: string; category: string; platform: string; ratio: string; intent: string; prompt: string; workflow_type: WorkflowType; reference_source: ReferenceSource; configuration_status: string; configuration_errors: string[]; button_label: string; status: string; gallery: Asset[] };
type FormAsset = { asset_id: string; role: AssetRole; ordinal: number; url?: string };
type Form = Omit<Item, "id" | "status" | "gallery" | "configuration_status" | "configuration_errors"> & { assets: FormAsset[]; is_pinned: boolean; sort_order: number };

const workflowLabels: Record<WorkflowType, string> = {
  reference_remix: "参考复刻：商品图 + 参考图",
  product_prompt: "商品提示词：商品图 + 案例 Prompt",
  local_edit: "局部编辑：原图 + 编辑 Prompt + Mask",
  legacy_unclassified: "未分类：暂不支持一键使用",
};
const referenceSourceLabels: Record<ReferenceSource, string> = {
  admin_reference: "管理员参考图",
  result_image: "案例结果图（显式作为参考）",
  none: "不使用参考图",
};
const configErrorLabels: Record<string, string> = {
  prompt_required: "缺少案例 Prompt",
  button_label_required: "缺少按钮文案",
  workflow_type_required: "尚未选择执行工作流",
  reference_source_required: "参考复刻必须选择参考图来源",
  admin_reference_asset_required: "尚未上传参考图",
  result_reference_asset_required: "尚未上传可作为参考的结果图",
  reference_source_must_be_none: "此工作流不应配置参考图来源",
};
const blank: Form = { title: "", description: "", role: "", type: "万能改图", category: "其他", platform: "国内", ratio: "自动", intent: "", prompt: "", workflow_type: "legacy_unclassified", reference_source: "none", button_label: "一键做同款", assets: [], is_pinned: false, sort_order: 0 };
const statusLabels: Record<string, string> = { all: "全部", draft: "草稿", published: "已发布", archived: "已归档" };

export default function AdminInspirationPage() {
  const { workspace } = useWorkspace();
  const upload = useUploadAsset(workspace?.id);
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<Item | null>(null);
  const [form, setForm] = useState<Form>(blank);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<{ url: string; label: string } | null>(null);

  const load = async () => {
    const response = await fetch(`/api/v1/admin/inspiration/cases?status=${filter}`);
    if (!response.ok) throw new Error("案例列表加载失败");
    const data = await response.json() as { items: Item[] };
    setItems(data.items ?? []);
  };
  useEffect(() => { fetch("/api/v1/admin/inspiration/cases/access").then((r) => setAllowed(r.ok)).catch(() => setAllowed(false)); }, []);
  useEffect(() => {
    if (allowed) {
      // Refresh the admin case list after the permission check/filter changes.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      load().catch((e) => setMessage(e.message));
    }
    // load closes over the selected filter and is intentionally invoked only
    // when the permission/filter inputs change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, filter]);

  const update = (key: keyof Form, value: string | boolean | number) => setForm((current) => ({ ...current, [key]: value }));
  const startNew = () => { setSelected(null); setForm({ ...blank }); setMessage(""); };
  const edit = (item: Item) => { setSelected(item); setForm({ ...blank, ...item, assets: item.gallery.map(({ asset_id, role, ordinal, url }) => ({ asset_id, role, ordinal, url })) }); setMessage(""); };
  const uploadRole = async (event: ChangeEvent<HTMLInputElement>, role: AssetRole) => {
    const file = event.target.files?.[0]; event.target.value = ""; if (!file) return;
    try { const asset = await upload.mutateAsync({ file, mediaKind: "image" }); const access = workspace?.id ? await getAssetAccessUrl(workspace.id, asset.id) : null; setForm((current) => ({ ...current, reference_source: role === "reference" && current.workflow_type === "reference_remix" && current.reference_source === "none" ? "admin_reference" : current.reference_source, assets: [...current.assets, { asset_id: asset.id, role, ordinal: current.assets.filter((x) => x.role === role).length, url: access?.url }] })); setMessage("图片已上传，保存后关联到案例"); } catch (error) { setMessage(error instanceof Error ? error.message : "上传失败"); }
  };
  const save = async (publish = false) => {
    setSaving(true); setMessage("");
    try {
      const response = await fetch(selected ? `/api/v1/admin/inspiration/cases/${selected.id}` : "/api/v1/admin/inspiration/cases", { method: selected ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: { message?: string } | string } | null;
        const detail = typeof body?.detail === "string" ? body.detail : body?.detail?.message;
        throw new Error(detail || "保存失败，请检查必填字段");
      }
      const item = await response.json() as Item;
      if (publish) {
        const published = await fetch(`/api/v1/admin/inspiration/cases/${item.id}/publish`, { method: "POST" });
        if (!published.ok) {
          const body = await published.json().catch(() => null) as { detail?: { message?: string } | string } | null;
          const detail = typeof body?.detail === "string" ? body.detail : body?.detail?.message;
          throw new Error(detail || "发布失败，请先补齐工作流配置");
        }
      }
      setMessage(publish ? "案例已发布，前台会立即更新" : "草稿已保存"); await load(); setSelected(item);
    } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); } finally { setSaving(false); }
  };
  const archive = async () => { if (!selected) return; setSaving(true); try { await fetch(`/api/v1/admin/inspiration/cases/${selected.id}/archive`, { method: "POST" }); setMessage("案例已归档，前台已隐藏"); await load(); } finally { setSaving(false); } };

  useEffect(() => { const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setPreview(null); }; window.addEventListener("keydown", closeOnEscape); return () => window.removeEventListener("keydown", closeOnEscape); }, []);
  useEffect(() => { const openImage = (event: MouseEvent) => { const target = event.target; if (!(target instanceof Element) || target.closest("button")) return; const card = target.closest('[class*="assetPreview"]'); const image = card?.querySelector("img"); if (!image?.src) return; setPreview({ url: image.src, label: image.alt || "图片" }); }; document.addEventListener("click", openImage); return () => document.removeEventListener("click", openImage); }, []);
  if (allowed === null) return <AdminShell><main className={styles.loading}><Loader2 className="spin" /> 正在检查管理权限…</main></AdminShell>;
  if (!allowed) return <AdminShell><main className={styles.loading}><h1>暂无灵感案例管理权限</h1><p>请使用公告后台相同的管理员身份登录。</p></main></AdminShell>;
  return <AdminShell><main className={styles.page}>
    <header className={styles.header}><div><span className={styles.eyebrow}><Sparkles size={14} /> 运营工具</span><h1>灵感案例后台</h1><p>管理前台展示的视觉策略案例，发布后无需重新部署。</p></div><button className={styles.primary} onClick={startNew}><Plus size={17} /> 新建案例</button></header>
    <div className={styles.layout}>
      <aside className={styles.sidebar}><div className={styles.sidebarHead}><div><strong>案例库</strong><small>{items.length} 条记录</small></div><button className={styles.iconButton} onClick={() => load()} aria-label="刷新"><Loader2 size={16} /></button></div><div className={styles.tabs}>{Object.entries(statusLabels).map(([key, label]) => <button key={key} className={filter === key ? styles.tabActive : ""} onClick={() => setFilter(key)}>{label}</button>)}</div>{items.length === 0 ? <div className={styles.empty}>还没有案例<br /><button onClick={startNew}>创建第一条案例</button></div> : <div className={styles.list}>{items.map((item) => <button key={item.id} className={`${styles.listItem} ${selected?.id === item.id ? styles.listSelected : ""}`} onClick={() => edit(item)}><span className={`${styles.dot} ${styles[`dot_${item.status}`]}`} /><span><strong>{item.title || "未命名案例"}</strong><small>{statusLabels[item.status] ?? item.status} · {item.type}</small></span></button>)}</div>}</aside>
      <section className={styles.editor}><div className={styles.editorHead}><div><span className={styles.eyebrow}>EDITOR</span><h2>{selected ? "编辑案例" : "创建案例"}</h2></div><span className={styles.statusBadge}>{selected ? statusLabels[selected.status] : "新草稿"}</span></div>
        <div className={styles.formGrid}><label className={styles.fieldWide}><span>执行工作流 <em>*</em></span><select value={form.workflow_type} onChange={(e) => { const value = e.target.value as WorkflowType; setForm((current) => ({ ...current, workflow_type: value, reference_source: value === "reference_remix" ? current.reference_source : "none" })); }}><option value="legacy_unclassified">{workflowLabels.legacy_unclassified}</option><option value="reference_remix">{workflowLabels.reference_remix}</option><option value="product_prompt">{workflowLabels.product_prompt}</option><option value="local_edit">{workflowLabels.local_edit}</option></select></label><label><span>按钮文案</span><input value={form.button_label} onChange={(e) => update("button_label", e.target.value)} maxLength={80} /></label>{form.workflow_type === "reference_remix" && <label><span>参考图来源</span><select value={form.reference_source} onChange={(e) => update("reference_source", e.target.value as ReferenceSource)}><option value="none">{referenceSourceLabels.none}</option><option value="admin_reference">{referenceSourceLabels.admin_reference}</option><option value="result_image">{referenceSourceLabels.result_image}</option></select></label>}<label className={styles.fieldWide}><span>案例标题 <em>*</em></span><input value={form.title} onChange={(e) => update("title", e.target.value)} placeholder="例如：暖金梳妆台 · 花香时刻" maxLength={160} /></label><label><span>类型</span><input value={form.type} onChange={(e) => update("type", e.target.value)} placeholder="卖点图" /></label><label><span>品类</span><input value={form.category} onChange={(e) => update("category", e.target.value)} placeholder="美妆个护" /></label><label><span>平台</span><select value={form.platform} onChange={(e) => update("platform", e.target.value)}><option>国内</option><option>跨境</option></select></label><label><span>画面比例</span><input value={form.ratio} onChange={(e) => update("ratio", e.target.value)} placeholder="1:1 / 3:4 / 自动" /></label><label className={styles.fieldWide}><span>图片角色</span><input value={form.role} onChange={(e) => update("role", e.target.value)} placeholder="场景图、服装上身、卖点图…" /></label><label className={styles.fieldWide}><span>案例说明</span><textarea rows={3} value={form.description} onChange={(e) => update("description", e.target.value)} placeholder="用一句话说明这个案例解决什么问题" maxLength={500} /></label><label className={styles.fieldWide}><span>商业意图</span><input value={form.intent} onChange={(e) => update("intent", e.target.value)} placeholder="例如：让用户快速理解产品使用场景" /></label><label className={styles.fieldWide}><span>原创 Remix Prompt <em>*</em></span><textarea className={styles.prompt} rows={9} value={form.prompt} onChange={(e) => update("prompt", e.target.value)} placeholder="Product Identity Lock…\nVisual Strategy Lock…\nOriginal Remix Prompt…\nForbidden Transfer…" /></label></div><div style={{ marginTop: 12, padding: "10px 12px", borderRadius: 10, background: form.workflow_type === "legacy_unclassified" || form.workflow_type === "local_edit" ? "#fff7ed" : "#eefbf4", color: "#445", fontSize: 12 }}><strong>{workflowLabels[form.workflow_type]}</strong><span style={{ marginLeft: 10 }}>参考来源：{referenceSourceLabels[form.reference_source]}</span>{(form.workflow_type === "product_prompt" || form.workflow_type === "local_edit") && <p style={{ margin: "6px 0 0" }}>该路径不会自动把案例结果图当作参考图；用户运行时仅补充所需素材。</p>}{form.workflow_type === "legacy_unclassified" && <p style={{ margin: "6px 0 0" }}>保存草稿可以暂不分类，但发布前必须选择可执行工作流。</p>}{selected?.configuration_errors?.length ? <p style={{ margin: "6px 0 0", color: "#b45309" }}>当前缺失：{selected.configuration_errors.map((error) => configErrorLabels[error] || error).join("、")}</p> : null}</div>
        <div className={styles.assets}><div className={styles.sectionTitle}><div><h3>图片资产</h3><small>上传后会复用现有对象存储，不写入 public/。</small></div><span>{form.assets.length} 个已添加</span></div><div className={styles.assetGrid}>{(["result", "user_upload", "reference"] as AssetRole[]).map((role) => <label key={role} className={styles.uploadCard}><ImagePlus size={22} /><strong>{role === "result" ? "结果图" : role === "user_upload" ? "用户上传图" : "参考图"}</strong><small>点击上传图片</small><input hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={(e) => uploadRole(e, role)} /></label>)}</div><div className={styles.assetPreviewGrid} style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10, marginTop: 14 }}>{form.assets.map((asset, index) => <div className={styles.assetPreview} style={{ display: "grid", gridTemplateColumns: "58px minmax(0, 1fr) 22px", alignItems: "center", gap: 9, minWidth: 0, padding: 7, border: "1px solid #e1e3e9", borderRadius: 10, background: "#fff" }} key={`${asset.asset_id}-${index}`}>{asset.url ? <button type="button" onClick={() => setPreview({ url: asset.url!, label: asset.role === "result" ? "结果图" : asset.role === "user_upload" ? "用户上传图" : "参考图" })} aria-label="查看原图" style={{ display: "block", width: 58, height: 58, padding: 0, border: 0, borderRadius: 7, overflow: "hidden", cursor: "zoom-in", background: "transparent" }}><img style={{ width: 58, height: 58, borderRadius: 7, objectFit: "cover" }} src={asset.url} alt={`${asset.role} ${asset.ordinal + 1}`} /></button> : <div className={styles.assetPreviewPlaceholder} style={{ display: "grid", width: 58, height: 58, placeItems: "center", borderRadius: 7, background: "#f2f3f7" }}><Loader2 size={16} /></div>}<div style={{ minWidth: 0 }}><strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11 }}>{asset.role === "result" ? "结果图" : asset.role === "user_upload" ? "用户上传图" : "参考图"} {asset.ordinal + 1}</strong><small style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 3, color: "#a1a6b6", fontSize: 9 }}>{asset.asset_id}</small></div><button onClick={() => setForm((current) => ({ ...current, assets: current.assets.filter((_, i) => i !== index) }))} aria-label="移除"><Trash2 size={13} /></button></div>)}</div></div>
        <div className={styles.actions}><span className={message ? styles.message : ""}>{message}</span><div><button className={styles.secondary} onClick={startNew}><Plus size={15} /> 清空</button><button className={styles.secondary} onClick={() => save(false)} disabled={saving}><Save size={15} /> 保存草稿</button>{selected?.status !== "published" && <button className={styles.publish} onClick={() => save(true)} disabled={saving}><Send size={15} /> 发布</button>}{selected && selected.status !== "archived" && <button className={styles.archive} onClick={archive} disabled={saving}><Archive size={15} /> 归档</button>}</div></div>
      </section>
    </div>
  </main>{preview && <div role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreview(null); }} style={{ position: "fixed", inset: 0, zIndex: 200, display: "grid", placeItems: "center", padding: 28, background: "rgb(13 16 33 / 78%)", backdropFilter: "blur(8px)" }}><div role="dialog" aria-modal="true" aria-label={`${preview.label}原图预览`} style={{ position: "relative", display: "grid", width: "min(1100px, 94vw)", height: "min(820px, 92vh)", placeItems: "center", padding: 22, border: "1px solid rgb(255 255 255 / 16%)", borderRadius: 18, background: "#111522", boxShadow: "0 24px 80px rgb(0 0 0 / 35%)" }}><button onClick={() => setPreview(null)} aria-label="关闭原图预览" style={{ position: "absolute", zIndex: 1, top: 12, right: 12, display: "grid", width: 36, height: 36, placeItems: "center", borderRadius: 10, color: "#fff", background: "rgb(255 255 255 / 12%)" }}><X size={19} /></button><img src={preview.url} alt={`${preview.label}原图`} style={{ display: "block", width: "100%", height: "100%", minWidth: 0, minHeight: 0, objectFit: "contain", borderRadius: 8 }} /><span style={{ position: "absolute", right: 22, bottom: 14, left: 22, color: "rgb(255 255 255 / 72%)", fontSize: 12, textAlign: "center" }}>{preview.label} · 原图</span></div></div>}</AdminShell>;
}
