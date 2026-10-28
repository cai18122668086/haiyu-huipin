"use client";

import { Eye, EyeOff, KeyRound, LoaderCircle, Settings2, X } from "lucide-react";
import { FormEvent, useState } from "react";

import {
  useDevelopmentProviderSettings,
  useUpdateDevelopmentProviderSettings,
} from "@/features/runtime/api/provider-settings";
import { errorMessage } from "@/lib/api/request";
import styles from "./development-provider-settings.module.css";

export function DevelopmentProviderSettings() {
  const [open, setOpen] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [baseUrlOverride, setBaseUrlOverride] = useState<string>();
  const [imageModelOverride, setImageModelOverride] = useState<string>();
  const [visionModelOverride, setVisionModelOverride] = useState<string>();
  const settings = useDevelopmentProviderSettings(open);
  const update = useUpdateDevelopmentProviderSettings();
  const baseUrl = baseUrlOverride ?? settings.data?.base_url ?? "";
  const imageModel = imageModelOverride ?? settings.data?.image_model ?? "gpt-image-2";
  const visionModel = visionModelOverride ?? settings.data?.vision_model ?? "gpt-5.6-terra";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await update.mutateAsync({
      api_key: apiKey,
      base_url: baseUrl,
      image_model: imageModel,
      vision_model: visionModel,
    });
    setApiKey("");
    setShowKey(false);
  }

  return <div className={styles.wrapper}>
    <button className={styles.trigger} type="button" onClick={() => setOpen(true)}>
      <Settings2 size={16} />配置真实生图服务
    </button>
    {open && <div className={styles.backdrop} role="presentation">
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="provider-settings-title">
        <header><div><span>DEVELOPER ONLY</span><h2 id="provider-settings-title">生图服务配置</h2><p>密钥只发送到本机后端并写入被 Git 忽略的本地环境文件，页面不会回显或保存密钥。</p></div><button type="button" onClick={() => setOpen(false)} aria-label="关闭生图服务配置"><X size={18} /></button></header>
        <form onSubmit={(event) => void submit(event)}>
          <label><span>API Key</span><div className={styles.secret}><KeyRound size={16} /><input autoFocus required minLength={8} autoComplete="new-password" type={showKey ? "text" : "password"} value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.data?.configured ? "已配置；输入新密钥可替换" : "粘贴 API Key"} /><button type="button" onClick={() => setShowKey((value) => !value)} aria-label={showKey ? "隐藏密钥" : "显示密钥"}>{showKey ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></label>
          <label><span>Base URL</span><input required value={baseUrl} onChange={(event) => setBaseUrlOverride(event.target.value)} placeholder={settings.isLoading ? "读取本机配置…" : "输入服务地址"} /></label>
          <div className={styles.models}><label><span>生图模型</span><input required value={imageModel} onChange={(event) => setImageModelOverride(event.target.value)} /></label><label><span>视觉规划模型</span><input required value={visionModel} onChange={(event) => setVisionModelOverride(event.target.value)} /></label></div>
          {settings.isLoading && <p className={styles.state}><LoaderCircle className="spin" size={15} />读取本机配置…</p>}
          {update.error && <p className={styles.error}>{errorMessage(update.error)}</p>}
          {update.data && <p className={styles.success}>{update.data.message}</p>}
          <footer><button type="button" onClick={() => setOpen(false)}>取消</button><button className={styles.save} disabled={update.isPending || !apiKey.trim()} type="submit">{update.isPending ? <LoaderCircle className="spin" size={16} /> : <KeyRound size={16} />}保存并启用</button></footer>
        </form>
      </section>
    </div>}
  </div>;
}
