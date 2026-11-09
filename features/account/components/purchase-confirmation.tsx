"use client";

import Link from "@/components/seo-link";
import { ArrowRight, Check, Coins, MessageCircle, ShieldCheck } from "lucide-react";
import type { Product } from "@/features/billing/api/account-center";
import styles from "./purchase-confirmation.module.css";

type Channel = "alipay" | "wechat";

export function PurchaseConfirmation({ product, channels, channel, pending, error, onChannelChange, onSubmit }: {
  product: Product;
  channels: Channel[];
  channel: Channel;
  pending: boolean;
  error?: string;
  onChannelChange: (channel: Channel) => void;
  onSubmit: () => void;
}) {
  const membership = product.kind === "membership";
  const amount = (product.amount_fen / 100).toFixed(2);
  const provider = channel === "wechat" ? "微信支付" : "支付宝";
  const validity = membership
    ? `${product.months === 12 ? "年付一次支付，积分分 12 期按月发放" : "月卡按一个月周期发放积分"}，每期积分当期有效。已有会员时顺延生效。`
    : product.validity_days
      ? `积分到账后 ${product.validity_days} 天有效。`
      : product.validity_months
        ? `积分到账后 ${product.validity_months} 个月有效。`
        : "购买积分无固定到期日。";

  return <div className={styles.content}>
    <section className={styles.summary} aria-label="购买内容">
      <div className={styles.productHeading}>
        <span className={styles.productIcon} aria-hidden="true"><Coins size={23} /></span>
        <div><span className={styles.eyebrow}>{membership ? "会员方案" : "积分充值"}</span><h3>{product.name}</h3></div>
      </div>
      <div className={styles.creditLine}>
        <span>{membership ? "每月创作积分" : "到账创作积分"}</span>
        <strong>{product.credits.toLocaleString("zh-CN")}<small>积分{membership ? " / 月" : ""}</small></strong>
      </div>
      <p className={styles.validity}>{validity}</p>
      <div className={styles.renewal}><ShieldCheck size={14} aria-hidden="true" />一次购买，不自动续费</div>
    </section>

    <fieldset className={styles.channels} disabled={pending}>
      <legend>选择支付方式</legend>
      <div className={styles.channelGrid}>
        {channels.map(value => <label key={value} className={`${styles.channel} ${channel === value ? styles.selected : ""}`}>
          <input type="radio" name="payment-channel" value={value} checked={channel === value} onChange={() => onChannelChange(value)} />
          <span className={`${styles.channelIcon} ${value === "wechat" ? styles.wechat : styles.alipay}`} aria-hidden="true">
            {value === "alipay" ? "支" : <MessageCircle size={21} fill="currentColor" />}
          </span>
          <span>{value === "alipay" ? "支付宝" : "微信支付"}</span>
          <span className={styles.radioMark} aria-hidden="true">{channel === value && <Check size={12} strokeWidth={3} />}</span>
        </label>)}
      </div>
    </fieldset>

    <div className={styles.total}><span>实付金额</span><strong><small>¥</small>{amount}</strong></div>
    <p className={styles.terms}>购买前请阅读<Link href="/legal/terms" target="_blank" rel="noopener noreferrer">《用户协议》</Link>与<Link href="/legal/privacy" target="_blank" rel="noopener noreferrer">《隐私政策》</Link>。订单号、套餐名称和金额将提供给{provider}，用于收款与核对。退款或发票请凭订单号联系客服。</p>
    {error && <p className={styles.error} role="alert">{error}</p>}
    <button type="button" className={styles.submit} disabled={pending || !channels.includes(channel)} onClick={onSubmit}>
      {pending ? "正在创建订单…" : `确认支付 ¥${amount}`}<ArrowRight size={18} aria-hidden="true" />
    </button>
  </div>;
}
