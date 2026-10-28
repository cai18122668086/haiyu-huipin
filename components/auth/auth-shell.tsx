import Link from "next/link";
import { BrandMark } from "@/components/brand-mark";
import styles from "./auth-shell.module.css";

export function AuthShell({ title, description, children, footer }: { title: string; description?: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <main className={styles.page}>
      <div className={styles.backdrop} aria-hidden="true">
        <header><BrandMark /><span>你的 AI 电商设计工作台</span><span>简体中文</span></header>
        <aside><strong>创作首页</strong><span>灵感广场</span><span>我的素材</span><span>创作记录</span><span>帮助中心</span></aside>
        <div className={styles.workspace}><div className={styles.banner}>好商品，值得被看见。<small>从灵感到成片，让创作更简单</small></div><h2>开始你的下一次创作</h2><div className={styles.tools}>{[
          ["局部精修", "card-01-local-edit-after-v1.webp"],
          ["背景替换", "card-02-background-after-v1.webp"],
          ["一键复刻", "card-03-remix-result-serum-landscape-v1.png"],
          ["详情页套图", "card-04-detail-after-cover-v1.webp"],
          ["跨境电商主图", "card-05-amazon-hero-v2.png"],
        ].map(([label, image]) => <div key={label}><span>{label}</span>{/* 项目自有作品仅用于登录背景，不请求生成任务。 */}<img src={`/images/homepage-features/${image}`} alt="" /></div>)}</div></div>
      </div>
      <div className={styles.scrim} />
      <section className={styles.panel} aria-labelledby="auth-title">
        <Link className={styles.home} href="/" aria-label="返回首页">‹</Link>
        <div className={styles.heading}><h1 id="auth-title">{title}</h1>{description ? <p>{description}</p> : null}</div>
        {children}{footer ? <div className={styles.footer}>{footer}</div> : null}
        <p className={styles.brandNote}>海宇绘品 · HAIYU HUIPIN</p>
      </section>
    </main>
  );
}
