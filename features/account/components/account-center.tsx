"use client";

import { QRCodeSVG } from "qrcode.react";

import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import Link from "next/link";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Coins,
  Crown,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  Layers3,
  LayoutDashboard,
  Menu,
  Plus,
  Search,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  UserRound,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { AccountPanel } from "@/features/auth/components/account-panel";
import { authUserLabel, useCurrentAuthUser } from "@/features/auth/api/me";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { useCreditAccount } from "@/features/billing/api/credits";
import {
  useCreatePurchase,
  useCreditProducts,
  useLedgerHistory,
  useMembership,
  usePurchaseHistory,
  usePurchaseOrder,
  useRefundHistory,
  useReconcilePurchase,
  type Product,
} from "@/features/billing/api/account-center";
import { useCreationRecords } from "@/features/jobs/api/creation-records";
import { JobRecordCover } from "@/features/jobs/components/job-record-cover";
import {
  useAssetAccessUrl,
  useAssets,
  useUploadAsset,
} from "@/features/assets/api/assets";
import { usePublicAnnouncements } from "@/features/announcements/api/announcements";
import { errorMessage } from "@/lib/api/request";
import { isActiveJob, type Asset, type Job } from "@/lib/api/types";
import {
  jobStatusLabel,
  jobTitle,
  jobTypeLabel,
} from "@/lib/jobs/presentation";
import styles from "./account-center.module.css";

type Page =
  | "overview"
  | "membership"
  | "credits"
  | "orders"
  | "creations"
  | "assets"
  | "help"
  | "settings";
const pages: { id: Page; label: string; icon: LucideIcon; group?: string }[] = [
  { id: "overview", label: "账户概览", icon: LayoutDashboard },
  { id: "membership", label: "会员与充值", icon: Crown },
  { id: "credits", label: "积分明细", icon: Coins },
  { id: "orders", label: "订单与发票", icon: FileText },
  { id: "creations", label: "创作记录", icon: Layers3, group: "我的创作" },
  { id: "assets", label: "我的素材", icon: FolderOpen },
  { id: "help", label: "消息与帮助", icon: CircleHelp },
  { id: "settings", label: "账户设置", icon: Settings2, group: "其他" },
];
const subtitles: Record<Page, string> = {
  overview: "管理你的创作、积分与会员，一切都在这里。",
  membership: "选择适合你的创作节奏，让每一次上新更从容。",
  credits: "每一笔消耗与到账，都清楚可查。",
  orders: "查看购买记录，管理订单和发票。",
  creations: "从第一张灵感，到一整套好作品。",
  assets: "把商品与品牌素材整理好，下次创作更轻松。",
  help: "产品动态、使用指南和常见问题。",
  settings: "管理你的账户与登录安全。",
};
const fmt = (value: number) => value.toLocaleString("zh-CN");
const money = (fen: number) =>
  (fen / 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const date = (value: string | null | undefined) =>
  value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : "—";
const recordHref = (job: Pick<Job, "id">) =>
  job.id.startsWith("rep_")
    ? `/style-replica?sessionId=${encodeURIComponent(job.id)}`
    : `/jobs?jobId=${encodeURIComponent(job.id)}`;
function Button({
  children,
  variant = "outline",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string }) {
  return (
    <button type="button" className={`button ${variant}`} {...props}>
      {children}
    </button>
  );
}
function Heading({ title, aside }: { title: string; aside?: ReactNode }) {
  return (
    <div className="panel-heading">
      <h2>{title}</h2>
      {aside}
    </div>
  );
}
function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty-state">
      <FolderOpen size={26} />
      <strong>{children}</strong>
    </div>
  );
}
function Failure({ error, retry }: { error: unknown; retry: () => unknown }) {
  return (
    <div className="empty-state" role="alert">
      <strong>{errorMessage(error)}</strong>
      <Button onClick={() => void retry()}>重新加载</Button>
    </div>
  );
}
function Status({ status }: { status: string }) {
  return (
    <span
      className={`status ${status === "已完成" || status === "已支付" ? "complete" : status === "待付款" || status === "进行中" ? "running" : "neutral"}`}
    >
      {status}
    </span>
  );
}
function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <label className="search-box">
      <Search size={17} />
      <input
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
function Dialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current
      ?.querySelector<HTMLElement>("button, a[href], input")
      ?.focus();
    function key(event: KeyboardEvent) {
      if (event.key === "Escape") close.current();
      if (event.key !== "Tab") return;
      const items = Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), [tabindex="0"]',
        ) ?? [],
      );
      const first = items[0],
        last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = overflow;
      document.removeEventListener("keydown", key);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-dialog-title"
        ref={panel}
      >
        <header className="modal-heading">
          <h2 id="account-dialog-title">{title}</h2>
          <button
            className="icon-button"
            aria-label="关闭弹窗"
            onClick={onClose}
          >
            <X size={21} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function AccountCenterPage() {
  const { workspace, isPending: loading, error } = useWorkspace();
  const credits = useCreditAccount(workspace?.id);
  if (loading || (workspace && credits.isPending))
    return <div className="auth-inline-status">正在载入个人中心…</div>;
  if (error || credits.isError)
    return (
      <div className="auth-inline-status" role="alert">
        个人中心暂时无法加载。
        <button onClick={() => window.location.reload()}>重新加载</button>
      </div>
    );
  if (!workspace)
    return (
      <AppShell sidebar={false}>
        <div className="account-page">
          <AccountPanel />
        </div>
      </AppShell>
    );
  return <AccountCenter key={workspace.id} workspaceId={workspace.id} />;
}

function AccountCenter({ workspaceId }: { workspaceId: string }) {
  const page = useSyncExternalStore(
    (notify) => {
      window.addEventListener("hashchange", notify);
      return () => window.removeEventListener("hashchange", notify);
    },
    () => {
      const hash = window.location.hash.slice(1);
      return pages.some((item) => item.id === hash)
        ? (hash as Page)
        : "overview";
    },
    () => "overview" as Page,
  );
  const [accountOpen, setAccountOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [purchaseTab, setPurchaseTab] = useState<"member" | "pack">("member");
  const [orderId, setOrderId] = useState<string | null>(null);
  const accountRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const user = useCurrentAuthUser();
  const credits = useCreditAccount(workspaceId);
  const member = useMembership(workspaceId);
  const name = authUserLabel(user.data);
  const balance = credits.data?.available_credits;
  useEffect(() => {
    const returned = new URLSearchParams(window.location.search).get(
      "purchase",
    );
    const returnSync = window.setTimeout(() => {
      if (returned && /^pay_[a-zA-Z0-9]+$/.test(returned)) {
        setOrderId(returned);
        window.location.hash = "orders";
      }
    }, 0);
    return () => window.clearTimeout(returnSync);
  }, []);
  useEffect(() => {
    function outside(event: MouseEvent) {
      if (!accountRef.current?.contains(event.target as Node))
        setAccountOpen(false);
    }
    function key(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setAccountOpen(false);
        setMobileOpen(false);
        menuRef.current?.focus();
      }
    }
    document.addEventListener("click", outside);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("click", outside);
      document.removeEventListener("keydown", key);
    };
  }, []);
  function navigate(next: Page) {
    window.location.hash = next;
    setAccountOpen(false);
    setMobileOpen(false);
    window.scrollTo({ top: 0 });
  }
  function recharge() {
    setPurchaseTab("pack");
    navigate("membership");
  }
  const active = pages.find((item) => item.id === page)!;
  return (
    <div className={styles.root}>
      <header className="topbar">
        <div className="topbar-inner">
          <Link className="brand" href="/" aria-label="海宇绘品首页">
            <img src="/brand/haiyu-icon-folded-h.png" alt="" />
            <span>
              海宇绘品<small>HAIYU HUIPIN</small>
            </span>
          </Link>
          <div className="topbar-right">
            {process.env.NODE_ENV !== "production" && (
              <span className="local-badge">
                <span />
                本地联调 · 测试数据
              </span>
            )}
            <div className="account-wrap" ref={accountRef}>
              <button
                className="account-button"
                aria-expanded={accountOpen}
                aria-controls="account-dropdown"
                onClick={() => setAccountOpen((value) => !value)}
              >
                <span className="avatar small">{name.slice(0, 1)}</span>
                <span className="account-label">个人中心</span>
                <ChevronDown size={14} />
              </button>
              {accountOpen && (
                <div className="account-dropdown" id="account-dropdown">
                  <div className="dropdown-profile">
                    <span className="avatar">
                      <UserRound size={20} />
                    </span>
                    <div>
                      <strong>{name}</strong>
                      <span>{member.data?.plan_name ?? "创作账户"}</span>
                    </div>
                  </div>
                  <div className="dropdown-balance">
                    <span>
                      可用积分
                      <strong>
                        {balance === undefined ? "—" : fmt(balance)}
                      </strong>
                    </span>
                    <button onClick={recharge}>
                      充值
                      <ArrowUpRight size={13} />
                    </button>
                  </div>
                  {pages.map((item) => (
                    <button
                      className="dropdown-link"
                      key={item.id}
                      onClick={() => navigate(item.id)}
                    >
                      <item.icon size={16} />
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button
              ref={menuRef}
              className="icon-button mobile-menu"
              aria-label="展开个人中心导航"
              aria-expanded={mobileOpen}
              onClick={() => setMobileOpen((value) => !value)}
            >
              {mobileOpen ? <X size={21} /> : <Menu size={21} />}
            </button>
          </div>
        </div>
      </header>
      {mobileOpen && (
        <button
          className="sidebar-scrim"
          aria-label="收起导航"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <div className="app-layout">
        <aside className={`sidebar ${mobileOpen ? "mobile-open" : ""}`}>
          <div className="sidebar-title">
            个人中心<span>MY SPACE</span>
          </div>
          <nav aria-label="个人中心导航">
            {pages.map((item) => (
              <div key={item.id}>
                {item.group && <div className="nav-group">{item.group}</div>}
                <button
                  className={`side-link ${page === item.id ? "active" : ""}`}
                  aria-current={page === item.id ? "page" : undefined}
                  onClick={() => navigate(item.id)}
                >
                  <item.icon size={18} strokeWidth={1.7} />
                  {item.label}
                </button>
              </div>
            ))}
          </nav>
          <div className="sidebar-footer">
            <span className="mini-star">✳</span>海宇绘品 · 为生意而创作
          </div>
        </aside>
        <main className="main">
          <div className="breadcrumb">
            个人中心
            <ChevronRight size={13} />
            <span>{active.label}</span>
          </div>
          <div className="page-heading">
            <div>
              <h1>{active.label}</h1>
              <p>{subtitles[page]}</p>
            </div>
          </div>
          {page === "overview" && (
            <Overview
              workspaceId={workspaceId}
              name={name}
              navigate={navigate}
              recharge={recharge}
            />
          )}
          {page === "membership" && (
            <Membership
              workspaceId={workspaceId}
              tab={purchaseTab}
              setTab={setPurchaseTab}
              navigate={navigate}
              openOrder={setOrderId}
            />
          )}
          {page === "credits" && (
            <Ledger workspaceId={workspaceId} recharge={recharge} />
          )}
          {page === "orders" && (
            <Orders workspaceId={workspaceId} openOrder={setOrderId} />
          )}
          {page === "creations" && <Creations workspaceId={workspaceId} />}
          {page === "assets" && <Assets workspaceId={workspaceId} />}
          {page === "help" && <Help />}
          {page === "settings" && (
            <div className="settings-integration">
              <AccountPanel />
              <Link className="button outline" href="/auth/forgot-password">
                修改或重置密码
                <ShieldCheck size={16} />
              </Link>
            </div>
          )}
        </main>
      </div>
      {orderId && (
        <OrderDialog
          workspaceId={workspaceId}
          orderId={orderId}
          onClose={() => setOrderId(null)}
        />
      )}
    </div>
  );
}

function Overview({
  workspaceId,
  name,
  navigate,
  recharge,
}: {
  workspaceId: string;
  name: string;
  navigate: (page: Page) => void;
  recharge: () => void;
}) {
  const credits = useCreditAccount(workspaceId),
    member = useMembership(workspaceId),
    jobs = useCreationRecords(workspaceId);
  const data = jobs.data.items;
  const running = data.filter((job) => isActiveJob(job.status)).length;
  return (
    <div className="page-content">
      <section className="welcome-card">
        <div className="welcome-content">
          <div className="eyebrow">
            <span className="hello-dot" />
            YOUR CREATIVE SPACE
          </div>
          <h2>
            你好，{name}
            <span>让好商品，自带好画面。</span>
          </h2>
          <p>从账户到作品，把每一次创作都收好。</p>
          <div className="member-line">
            <span>
              <Crown size={14} />
              {member.isPending
                ? "正在读取会员信息"
                : member.data?.plan_name
                  ? `${member.data.plan_name}会员`
                  : "创作账户"}
            </span>
            {member.data?.ends_at && (
              <small>有效期至 {date(member.data.ends_at)}</small>
            )}
            <button onClick={() => navigate("membership")}>
              查看权益
              <ArrowRight size={14} />
            </button>
          </div>
        </div>
        <div className="hero-art" aria-hidden="true">
          <span className="orbit one" />
          <span className="orbit two" />
          <span className="hero-star">✦</span>
          <span className="tiny-star a">✦</span>
          <span className="tiny-star b">✦</span>
          <span className="hero-tag">MAKE IT SHINE.</span>
        </div>
      </section>
      <section className="stats-grid">
        <div className="balance-stat">
          <div className="stat-top">
            <span>
              <Coins size={17} />
              可用创作积分
            </span>
            <button onClick={() => navigate("credits")}>
              明细
              <ArrowUpRight size={14} />
            </button>
          </div>
          <div className="balance-value">
            {credits.data ? fmt(credits.data.available_credits) : "—"}
            <span>积分</span>
          </div>
          <div className="balance-bottom">
            <span>
              生成中占用{" "}
              {credits.data ? fmt(credits.data.reserved_credits) : "—"} 积分
            </span>
            <button onClick={recharge}>
              充值积分
              <Plus size={14} />
            </button>
          </div>
        </div>
        <button className="stat-card" onClick={() => navigate("creations")}>
          <span className="stat-icon lime">
            <Layers3 size={20} />
          </span>
          <div className="stat-label">
            正在创作
            <ArrowUpRight size={15} />
          </div>
          <strong>
            {jobs.isPending ? "—" : running}
            <span>个任务</span>
          </strong>
          <small>查看实时进度</small>
        </button>
        <button className="stat-card" onClick={() => navigate("creations")}>
          <span className="stat-icon lilac">
            <ImageIcon size={20} />
          </span>
          <div className="stat-label">
            累计创作
            <ArrowUpRight size={15} />
          </div>
          <strong>
            {jobs.isPending ? "—" : data.length}
            <span>条记录</span>
          </strong>
          <small>作品与下载统一管理</small>
        </button>
      </section>
      <div className="overview-columns">
        <section className="panel">
          <Heading
            title="最近创作"
            aside={
              <button
                className="text-link"
                onClick={() => navigate("creations")}
              >
                查看全部
                <ArrowRight size={14} />
              </button>
            }
          />
          {jobs.error ? (
            <Failure error={jobs.error} retry={jobs.refetch} />
          ) : jobs.isPending ? (
            <Empty>正在载入创作记录…</Empty>
          ) : data.length ? (
            <TaskRows jobs={data.slice(0, 3)} workspaceId={workspaceId} />
          ) : (
            <Empty>还没有创作记录</Empty>
          )}
          <div className="panel-bottom-note">
            <ShieldCheck size={14} />
            生成进度、历史记录和作品下载，统一在创作记录查看。
          </div>
        </section>
        <section className="panel wallet-panel">
          <Heading title="我的积分" aside={<Coins size={18} />} />
          <div className="wallet-row">
            <span>
              <span className="source-dot purple" />
              会员积分
            </span>
            <strong>
              {member.data ? fmt(member.data.member_credits) : "—"}
            </strong>
          </div>
          <div className="wallet-row">
            <span>
              <span className="source-dot lime" />
              充值与历史积分
            </span>
            <strong>{member.data ? fmt(member.data.pack_credits) : "—"}</strong>
          </div>
          <div className="wallet-divider" />
          <div className="wallet-footer">
            <div>
              <small>本期会员额度</small>
              <strong>
                {member.data ? fmt(member.data.monthly_credits) : "—"}
                <span> 积分 / 月</span>
              </strong>
            </div>
            <button
              className="icon-button"
              aria-label="查看会员额度"
              onClick={() => navigate("membership")}
            >
              <ArrowUpRight size={19} />
            </button>
          </div>
          <p>
            {member.data?.next_expiry_at
              ? `${fmt(member.data.expiring_credits)} 积分将于 ${date(member.data.next_expiry_at)} 到期`
              : "优先使用即将到期的积分"}
          </p>
          {member.data?.next_plan_name && (
            <p>
              已购买 {member.data.next_plan_name}，将于{" "}
              {date(member.data.next_plan_starts_at)} 生效
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
function TaskRows({ jobs, workspaceId }: { jobs: Job[]; workspaceId: string }) {
  return (
    <div className="task-list">
      {jobs.map((job) => (
        <Link className="task-row" href={recordHref(job)} key={job.id}>
          <JobRecordCover workspaceId={workspaceId} job={job} />
          <div className="task-name">
            <strong>{jobTitle(job)}</strong>
            <span>
              {jobTypeLabel(job.type)}
              <i />
              {job.expected_item_count} 张<i />
              {date(job.created_at)}
            </span>
          </div>
          <Status
            status={
              isActiveJob(job.status) ? "进行中" : jobStatusLabel(job.status)
            }
          />
          <ChevronRight size={16} />
        </Link>
      ))}
    </div>
  );
}

const planCopy: Record<string, { name: string; description: string }> = {
  light: { name: "轻享版", description: "偶尔上新，也能从容出图" },
  pro: { name: "专业版", description: "为持续上新的商家准备" },
  team: { name: "团队版", description: "为高频创作准备更多额度" },
};
function Membership({
  workspaceId,
  tab,
  setTab,
  navigate,
  openOrder,
}: {
  workspaceId: string;
  tab: "member" | "pack";
  setTab: (tab: "member" | "pack") => void;
  navigate: (page: Page) => void;
  openOrder: (id: string) => void;
}) {
  const [annual, setAnnual] = useState(false),
    [selected, setSelected] = useState<Product | null>(null);
  const [channel, setChannel] = useState<"alipay" | "wechat">("alipay");
  const catalog = useCreditProducts(workspaceId),
    credits = useCreditAccount(workspaceId),
    create = useCreatePurchase(workspaceId);
  const products =
    catalog.data?.packages.filter((pack) =>
      tab === "pack"
        ? pack.kind === "pack"
        : pack.kind === "membership" && pack.months === (annual ? 12 : 1),
    ) ?? [];
  function select(product: Product) {
    create.newIntent();
    setSelected(product);
  }
  return (
    <div className="page-content membership-page">
      <div className="pricing-intro">
        <div>
          <span className="eyebrow">MORE ROOM FOR YOUR IDEAS</span>
          <h2>给创作，留足发挥空间。</h2>
          <p>创作积分全站通用，单图、套图、详情页随心创作。</p>
        </div>
        <div className="pricing-balance">
          <Coins size={22} />
          <div>
            当前可用积分
            <strong>
              {credits.data ? fmt(credits.data.available_credits) : "—"}
              <span> 积分</span>
            </strong>
          </div>
          <button aria-label="查看积分明细" onClick={() => navigate("credits")}>
            <ArrowUpRight size={19} />
          </button>
        </div>
      </div>
      <div className="pricing-toolbar">
        <div className="purchase-tabs" role="tablist" aria-label="购买方式">
          <button
            role="tab"
            aria-selected={tab === "member"}
            className={tab === "member" ? "active" : ""}
            onClick={() => setTab("member")}
          >
            <Crown size={17} />
            会员月卡
          </button>
          <button
            role="tab"
            aria-selected={tab === "pack"}
            className={tab === "pack" ? "active" : ""}
            onClick={() => setTab("pack")}
          >
            <Coins size={17} />
            积分充值
          </button>
        </div>
        {tab === "member" ? (
          <div
            className="billing-switch"
            role="group"
            aria-label="会员付费周期"
          >
            <button
              className={!annual ? "active" : ""}
              aria-pressed={!annual}
              onClick={() => setAnnual(false)}
            >
              按月付
            </button>
            <button
              className={annual ? "active" : ""}
              aria-pressed={annual}
              onClick={() => setAnnual(true)}
            >
              按年付<span>约省 20%</span>
            </button>
          </div>
        ) : (
          <span className="purchase-hint">
            <Clock3 size={14} />
            按需充值 · 不自动续费
          </span>
        )}
      </div>
      {catalog.isError ? (
        <Failure error={catalog.error} retry={catalog.refetch} />
      ) : catalog.isPending ? (
        <Empty>正在读取套餐…</Empty>
      ) : !products.length ? (
        <Empty>当前暂无可购买的套餐</Empty>
      ) : (
        <div className={`plan-grid ${tab === "pack" ? "pack-grid" : ""}`}>
          {products.map((product, index) => {
            const copy = product.plan_id
              ? planCopy[product.plan_id]
              : {
                  name: product.name,
                  description: product.id === "pack_small"
                    ? "先试试，找到你的出图节奏"
                    : product.id === "pack_large"
                      ? "为下一轮上新多做些准备"
                      : "备好额度，随时上新",
                };
            const featured = product.plan_id === "pro" || product.id === "pack_medium";
            return (
              <article
                className={`plan-card ${featured ? "featured" : ""}`}
                key={product.id}
              >
                {featured && (
                  <span className="plan-badge">
                    <Sparkles size={12} />
                    {tab === "member" ? "商家推荐" : "最受欢迎"}
                  </span>
                )}
                <div className="plan-symbol">
                  {tab === "pack" ? (
                    <Coins size={23} />
                  ) : index === 0 ? (
                    <Zap size={23} />
                  ) : index === 1 ? (
                    <Crown size={24} />
                  ) : (
                    <Layers3 size={24} />
                  )}
                </div>
                <h3>{copy?.name ?? "创作积分包"}</h3>
                <p className="plan-desc">
                  {copy?.description ?? "备好额度，随时上新"}
                </p>
                <div className="plan-price">
                  <span>¥</span>
                  <strong>{money(product.amount_fen)}</strong>
                  <small>
                    / {tab === "pack" ? "次" : annual ? "年" : "月"}
                  </small>
                </div>
                {annual && tab === "member" && (
                  <div className="annual-note">
                    折合 ¥{money(product.amount_fen / 12)} / 月，按年一次支付
                  </div>
                )}
                <div className="plan-credit">
                  <strong>{fmt(product.credits)}</strong>
                  <span>创作积分{tab === "member" ? " / 月" : ""}</span>
                </div>
                <div className="plan-output">
                  <ImageIcon size={14} />
                  <span>
                    {tab === "member" ? "每月约可创作" : "约可创作"}{" "}
                    <strong>{fmt(Math.floor(product.credits / 5))}</strong>{" "}
                    张图片
                  </span>
                </div>
                <Button
                  variant={featured ? "lime-button" : "outline"}
                  disabled={!catalog.data?.enabled}
                  onClick={() => { setChannel(catalog.data?.channels[0] ?? "alipay"); select(product); }}
                >
                  {tab === "pack"
                    ? "购买积分包"
                    : `选择${copy?.name ?? "会员"}`}
                  <ArrowUpRight size={15} />
                </Button>
                <div className="plan-features">
                  {[
                    `${tab === "member" ? "每月发放" : "到账"} ${fmt(product.credits)} 创作积分`,
                    "全部单图与套图工具",
                    tab === "member"
                      ? "年付积分逐月发放，月卡积分当期有效"
                      : product.validity_months
                        ? `${product.validity_months} 个月有效，按需使用`
                        : "购买积分无固定到期日，按需使用",
                    "一次购买，不自动续费",
                  ].map((text) => (
                    <div key={text}>
                      <Check size={15} />
                      {text}
                    </div>
                  ))}
                </div>
                <div className="plan-footer">
                  {tab === "member"
                    ? "积分按月发放 · 到期不自动扣款"
                    : "按需充值 · 无需订阅会员"}
                </div>
              </article>
            );
          })}
        </div>
      )}
      {!catalog.data?.enabled && <p role="status" className="checkout-note">会员方案已同步，在线购买尚未开放。</p>}
      <div className="pricing-disclaimer">
        <p>
          图片数量按普通单图估算，不同功能消耗不同，以生成前展示为准。已有会员时，新购会员在已购买周期结束后生效。
        </p>
      </div>
      <Faqs />
      {selected && (
        <Dialog
          title="确认购买"
          onClose={() => {
            if (!create.isPending) setSelected(null);
          }}
        >
          <div className="receipt">
            <div>
              <span>购买内容</span>
              <strong>{selected.name}</strong>
            </div>
            <div>
              <span>
                {selected.kind === "membership" ? "每月额度" : "充值积分"}
              </span>
              <strong>{fmt(selected.credits)} 积分</strong>
            </div>
            <div>
              <span>应付金额</span>
              <strong>¥{money(selected.amount_fen)}</strong>
            </div>
          </div>
          <p className="checkout-note">
            {selected.kind === "membership"
              ? `${selected.months === 12 ? "年付一次支付，分 12 期按月发放" : "月卡按一个月周期发放"}，每期积分当期有效。已有会员时顺延生效。`
              : selected.validity_months
                ? `积分到账后 ${selected.validity_months} 个月有效。`
                : "购买积分无固定到期日。"}
            到期不自动续费。
          </p>
          <p className="checkout-note">支付会向支付宝提供本订单号、套餐名称和金额，用于收款与核对。
            购买前请阅读 <Link href="/legal/terms" target="_blank">用户协议</Link> 和 <Link href="/legal/privacy" target="_blank">隐私政策</Link>；
            退款或发票请凭订单号联系客服人工处理。
          </p>
          <fieldset className="checkout-note">
            <legend>支付方式</legend>
            {catalog.data?.channels.map(value => <label key={value} style={{ marginRight: 20 }}>
              <input type="radio" name="payment-channel" value={value} checked={channel === value}
                disabled={create.isPending} onChange={() => setChannel(value)} />
              {value === "alipay" ? "支付宝" : "微信支付"}
            </label>)}
          </fieldset>
          {create.isError && (
            <p className="account-error" role="alert">
              {errorMessage(create.error)}
            </p>
          )}
          <div className="modal-actions">
            <Button
              onClick={() => setSelected(null)}
              disabled={create.isPending}
            >
              返回
            </Button>
            <Button
              variant="primary"
              disabled={create.isPending}
              onClick={() =>
                create.mutate({ productId: selected.id, channel }, {
                  onSuccess: (order) => {
                    setSelected(null);
                    openOrder(order.id);
                  },
                })
              }
            >
              {create.isPending ? "正在创建订单…" : "确认订单，前往付款"}
              <ArrowRight size={16} />
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

const purchaseStatus = (status: string) => ({ paid: "已支付", pending: "待付款", partially_refunded: "部分退款", refunded: "已退款" })[status] ?? status;

function OrderDialog({
  workspaceId,
  orderId,
  onClose,
}: {
  workspaceId: string;
  orderId: string;
  onClose: () => void;
}) {
  const order = usePurchaseOrder(workspaceId, orderId);
  const reconcile = useReconcilePurchase(workspaceId, orderId);
  const refunds = useRefundHistory(workspaceId, orderId);
  return (
    <Dialog title="订单详情" onClose={onClose}>
      {order.isError ? (
        <Failure error={order.error} retry={order.refetch} />
      ) : !order.data ? (
        <Empty>正在读取订单…</Empty>
      ) : (
        <>
          <div className="receipt">
            <div>
              <span>购买内容</span>
              <strong>{order.data.package_name}</strong>
            </div>
            <div>
              <span>订单号</span>
              <span className="breakable">{order.data.id}</span>
            </div>
            <div>
              <span>订单金额</span>
              <strong>¥{money(order.data.amount_fen)}</strong>
            </div>
            <div>
              <span>
                {order.data.product_kind === "membership"
                  ? "每月积分"
                  : "购买积分"}
              </span>
              <strong>{fmt(order.data.credits)} 积分</strong>
            </div>
            {order.data.membership_starts_at && (
              <div>
                <span>会员生效时间</span>
                <span>{date(order.data.membership_starts_at)}</span>
              </div>
            )}
            {order.data.membership_ends_at && (
              <div>
                <span>本次会员到期</span>
                <span>{date(order.data.membership_ends_at)}</span>
              </div>
            )}
            <div>
              <span>创建时间</span>
              <span>{date(order.data.created_at)}</span>
            </div>
            <div>
              <span>支付状态</span>
              <Status
                status={purchaseStatus(order.data.status)}
              />
            </div>
            {order.data.paid_at && (
              <div>
                <span>支付时间</span>
                <span>{date(order.data.paid_at)}</span>
              </div>
            )}
          </div>
          {order.data.status !== "pending" ? (
            <p className="checkout-note" role="status">
              {order.data.refunded_amount_fen > 0 ? `已原路退款 ¥${money(order.data.refunded_amount_fen)}。退款对应的积分与会员权益已回收。` : order.data.product_kind === "membership"
                ? "付款已确认。会员与积分按购买周期生效，可在账户概览核对。"
                : "付款已确认，积分已到账。"}
            </p>
          ) : (
            <>
              <p className="checkout-note">
                付款后余额与订单会自动更新。如已扣款，请点击“我已付款，核对到账”，无需重复支付。
              </p>
              <Button variant="primary" disabled={reconcile.isPending}
                onClick={() => reconcile.mutate()}>
                {reconcile.isPending ? "正在核对收款…" : "我已付款，核对到账"}
                <RefreshCw size={16} />
              </Button>
              {reconcile.isError && <p className="account-error" role="alert">{errorMessage(reconcile.error)}</p>}
              {reconcile.data?.status === "pending" && <p className="checkout-note" role="status">
                支付渠道尚未确认到账。如微信或支付宝账单已显示成功，请勿重复支付，保留账单和订单号，在消息与帮助中联系核对。
              </p>}
              {order.data.code_url && <div className="checkout-note" style={{ textAlign: "center" }}>
                <QRCodeSVG value={order.data.code_url} size={220} marginSize={4} title="微信支付二维码" />
                <p>打开微信扫一扫完成付款</p>
              </div>}
              {order.data.checkout_url && (
                <a
                  className="button outline full-width"
                  href={order.data.checkout_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  前往收银台付款
                  <ArrowUpRight size={16} />
                </a>
              )}
            </>
          )}
          {refunds.isError && <Failure error={refunds.error} retry={refunds.refetch} />}
          {refunds.data?.items.map(refund => <div className="checkout-note" key={refund.id}>
            <strong>{({ requested: "退款待处理", processing: "退款处理中", succeeded: "退款成功", failed: "退款未成功，已恢复仍有效的权益" })[refund.status] ?? refund.status} · ¥{money(refund.amount_fen)}</strong>
            <p>{refund.reason} · {date(refund.created_at)}</p><small className="breakable">退款单号：{refund.id}</small>
          </div>)}
          <div className="modal-actions">
            <Button onClick={onClose}>关闭</Button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function Orders({
  workspaceId,
  openOrder,
}: {
  workspaceId: string;
  openOrder: (id: string) => void;
}) {
  const orders = usePurchaseHistory(workspaceId),
    [filter, setFilter] = useState("全部订单"),
    [query, setQuery] = useState("");
  const items = orders.data?.pages.flatMap((page) => page.items) ?? [];
  const shown = items.filter(
    (order) =>
      (filter === "全部订单" ||
        (filter === "已支付"
          ? order.status === "paid"
          : filter === "退款订单" ? order.refunded_amount_fen > 0 : order.status === "pending")) &&
      `${order.id}${order.package_name}`.includes(query),
  );
  return (
    <div className="page-content">
      <div className="info-banner">
        <FileText size={21} />
        <div>
          <strong>购买记录，清楚收好</strong>
          <p>
            待付款订单可继续支付，到账后可核对积分流水。在线发票申请暂未开放。
          </p>
        </div>
      </div>
      <section className="panel">
        <div className="table-toolbar">
          <div className="filter-tabs">
            {["全部订单", "待付款", "已支付", "退款订单"].map((value) => (
              <button
                className={filter === value ? "active" : ""}
                key={value}
                onClick={() => setFilter(value)}
              >
                {value}
              </button>
            ))}
          </div>
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="搜索套餐或订单号"
          />
        </div>
        {orders.isError ? (
          <Failure error={orders.error} retry={orders.refetch} />
        ) : (
          <>
            <div className="table-scroll">
              <table className="orders-table">
                <thead>
                  <tr>
                    <th>订单信息</th>
                    <th>订单金额</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((order) => (
                    <tr key={order.id}>
                      <td>
                        <strong>{order.package_name}</strong>
                        <small className="order-id">{order.id}</small>
                      </td>
                      <td className="number">¥{money(order.amount_fen)}</td>
                      <td>
                        <Status
                          status={purchaseStatus(order.status)}
                        />
                      </td>
                      <td className="table-date">{date(order.created_at)}</td>
                      <td>
                        <button
                          className="text-link"
                          onClick={() => openOrder(order.id)}
                        >
                          {order.status === "pending" ? "继续付款" : "查看详情"}
                          <ArrowUpRight size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!shown.length && (
              <Empty>
                {orders.isPending ? "正在载入订单…" : "没有找到相关订单"}
              </Empty>
            )}
            {orders.hasNextPage && (
              <div className="table-footer">
                <Button
                  disabled={orders.isFetchingNextPage}
                  onClick={() => void orders.fetchNextPage()}
                >
                  加载更多订单
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function Ledger({
  workspaceId,
  recharge,
}: {
  workspaceId: string;
  recharge: () => void;
}) {
  const ledger = useLedgerHistory(workspaceId),
    credits = useCreditAccount(workspaceId),
    [filter, setFilter] = useState("全部"),
    [query, setQuery] = useState("");
  const rows = ledger.data?.pages.flatMap((page) => page.items) ?? [];
  const kind = (row: (typeof rows)[number]) =>
    row.entry_type === "reserve"
      ? "冻结"
      : row.entry_type === "release"
        ? "退回"
        : row.entry_type === "expire"
          ? "到期"
          : row.delta_balance_credits > 0
            ? "获得"
            : row.entry_type === "renewal"
              ? "续费"
              : "消耗";
  const shown = rows.filter(
    (row) =>
      (filter === "全部" || kind(row) === filter) && row.title.includes(query),
  );
  function exportRows() {
    const cell = (value: string | number) =>
      `"${String(value)
        .replace(/"/g, '""')
        .replace(/^[=+\-@]/, "'$&")}"`;
    const csv =
      "\uFEFF" +
      [
        ["时间", "项目", "类型", "余额变动", "冻结变动", "可用余额"],
        ...shown.map((row) => [
          date(row.created_at),
          row.title,
          kind(row),
          row.delta_balance_credits,
          row.delta_reserved_credits,
          row.available_after_credits,
        ]),
      ]
        .map((row) => row.map(cell).join(","))
        .join("\r\n");
    const url = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "海宇绘品-积分明细.csv";
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="page-content">
      <div className="credit-summary">
        <div>
          <span>可用创作积分</span>
          <strong>
            {credits.data ? fmt(credits.data.available_credits) : "—"}
            <small>积分</small>
          </strong>
          <p>
            生成中占用 {credits.data ? fmt(credits.data.reserved_credits) : "—"}{" "}
            积分
          </p>
        </div>
        <Button variant="primary" onClick={recharge}>
          充值积分
          <Plus size={15} />
        </Button>
      </div>
      <section className="panel ledger-panel">
        <div className="table-toolbar">
          <div className="filter-tabs">
            {["全部", "消耗", "获得", "冻结", "退回", "到期"].map((value) => (
              <button
                key={value}
                className={filter === value ? "active" : ""}
                onClick={() => setFilter(value)}
              >
                {value}
              </button>
            ))}
          </div>
          <SearchBox
            value={query}
            onChange={setQuery}
            placeholder="搜索项目名称"
          />
        </div>
        {ledger.isError ? (
          <Failure error={ledger.error} retry={ledger.refetch} />
        ) : (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>积分事项</th>
                    <th>类型</th>
                    <th>积分变动</th>
                    <th>可用余额</th>
                    <th>时间</th>
                    <th>关联任务</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <strong>{row.title}</strong>
                        {row.released_credits > 0 && (
                          <small className="order-id">
                            释放 {fmt(row.released_credits)} 积分
                          </small>
                        )}
                      </td>
                      <td>{kind(row)}</td>
                      <td className="number">
                        {row.delta_balance_credits > 0 ? "+" : ""}
                        {fmt(row.delta_balance_credits)}
                        {row.delta_reserved_credits !== 0 && (
                          <small className="order-id">
                            冻结 {row.delta_reserved_credits > 0 ? "+" : ""}
                            {fmt(row.delta_reserved_credits)}
                          </small>
                        )}
                      </td>
                      <td>{fmt(row.available_after_credits)}</td>
                      <td className="table-date">{date(row.created_at)}</td>
                      <td>
                        {row.job_id ? (
                          <Link
                            className="text-link"
                            href={recordHref({ id: row.job_id })}
                          >
                            查看任务
                            <ArrowUpRight size={13} />
                          </Link>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!shown.length && (
              <Empty>
                {ledger.isPending ? "正在载入积分明细…" : "没有找到相关明细"}
              </Empty>
            )}
            <div className="table-footer">
              <span>已加载 {rows.length} 条；导出当前筛选结果</span>
              <Button onClick={exportRows} disabled={!shown.length}>
                <ArrowDownToLine size={15} />
                导出明细
              </Button>
              {ledger.hasNextPage && (
                <Button
                  disabled={ledger.isFetchingNextPage}
                  onClick={() => void ledger.fetchNextPage()}
                >
                  加载更多
                </Button>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function Creations({ workspaceId }: { workspaceId: string }) {
  const jobs = useCreationRecords(workspaceId),
    [filter, setFilter] = useState("全部"),
    [query, setQuery] = useState("");
  const items = jobs.data.items.filter(
    (job) =>
      (filter === "全部" ||
        (filter === "进行中"
          ? isActiveJob(job.status)
          : jobStatusLabel(job.status) === filter)) &&
      jobTitle(job).includes(query),
  );
  return (
    <div className="page-content">
      <div className="content-toolbar">
        <div className="filter-tabs">
          {["全部", "进行中", "已完成", "部分完成", "失败"].map((value) => (
            <button
              key={value}
              className={value === filter ? "active" : ""}
              onClick={() => setFilter(value)}
            >
              {value}
            </button>
          ))}
        </div>
        <SearchBox
          value={query}
          onChange={setQuery}
          placeholder="搜索创作名称"
        />
      </div>
      {jobs.error ? (
        <Failure error={jobs.error} retry={jobs.refetch} />
      ) : jobs.isPending ? (
        <Empty>正在载入创作记录…</Empty>
      ) : items.length ? (
        <div className="creation-grid">
          {items.map((job) => (
            <Link className="creation-card" key={job.id} href={recordHref(job)}>
              <div className="creation-image">
                <JobRecordCover workspaceId={workspaceId} job={job} />
                <Status
                  status={
                    isActiveJob(job.status)
                      ? "进行中"
                      : jobStatusLabel(job.status)
                  }
                />
              </div>
              <div className="creation-card-body">
                <h3>{jobTitle(job)}</h3>
                <p>
                  {jobTypeLabel(job.type)}
                  <span>
                    {job.succeeded_item_count} / {job.expected_item_count} 张
                  </span>
                </p>
                <div>
                  <span>{date(job.created_at)}</span>
                  <span>
                    {job.cost.actual_credits == null
                      ? "查看进度"
                      : `${fmt(job.cost.actual_credits)} 积分`}
                    <ArrowUpRight size={14} />
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      ) : (
        <Empty>没有找到相关创作</Empty>
      )}
    </div>
  );
}
function AssetCard({
  workspaceId,
  asset,
  open,
}: {
  workspaceId: string;
  asset: Asset;
  open: () => void;
}) {
  const access = useAssetAccessUrl(workspaceId, asset.id, "thumbnail");
  return (
    <button className="asset-card" onClick={open}>
      <div className="asset-image">
        {access.data?.url ? (
          <img
            src={access.data.url}
            alt={asset.original_filename}
            loading="lazy"
          />
        ) : (
          <ImageIcon size={30} />
        )}
      </div>
      <strong>{asset.original_filename}</strong>
      <small>
        {asset.origin_kind === "upload" ? "上传素材" : "创作素材"} ·{" "}
        {date(asset.created_at)}
      </small>
    </button>
  );
}
function Assets({ workspaceId }: { workspaceId: string }) {
  const assets = useAssets(workspaceId),
    upload = useUploadAsset(workspaceId),
    [filter, setFilter] = useState("全部素材");
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<Asset | null>(null);
  const items =
    assets.data?.items.filter(
      (asset) =>
        filter === "全部素材" ||
        (filter === "上传素材"
          ? asset.origin_kind === "upload"
          : asset.origin_kind !== "upload"),
    ) ?? [];
  return (
    <div className="page-content">
      <div className="content-toolbar">
        <div className="filter-tabs">
          {["全部素材", "上传素材", "创作素材"].map((value) => (
            <button
              key={value}
              className={value === filter ? "active" : ""}
              onClick={() => setFilter(value)}
            >
              {value}
            </button>
          ))}
        </div>
        <Button
          variant="primary"
          disabled={upload.isPending}
          onClick={() => input.current?.click()}
        >
          <Plus size={16} />
          {upload.isPending ? "正在上传…" : "添加素材"}
        </Button>
        <input
          ref={input}
          type="file"
          accept="image/*"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate({ file });
            event.target.value = "";
          }}
        />
      </div>
      {upload.isError && (
        <p role="alert" className="account-error">
          {errorMessage(upload.error)}
        </p>
      )}
      {assets.isError ? (
        <Failure error={assets.error} retry={assets.refetch} />
      ) : assets.isPending ? (
        <Empty>正在载入素材…</Empty>
      ) : items.length ? (
        <div className="asset-grid">
          {items.map((asset) => (
            <AssetCard
              key={asset.id}
              workspaceId={workspaceId}
              asset={asset}
              open={() => setSelected(asset)}
            />
          ))}
        </div>
      ) : (
        <Empty>还没有相关素材</Empty>
      )}
      {selected && (
        <AssetDialog
          workspaceId={workspaceId}
          asset={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function AssetDialog({
  workspaceId,
  asset,
  onClose,
}: {
  workspaceId: string;
  asset: Asset;
  onClose: () => void;
}) {
  const access = useAssetAccessUrl(workspaceId, asset.id);
  return (
    <Dialog title={asset.original_filename} onClose={onClose}>
      {access.isError ? (
        <Failure error={access.error} retry={access.refetch} />
      ) : access.data?.url ? (
        <>
          <img src={access.data.url} alt={asset.original_filename} />
          <div className="modal-actions">
            <a
              className="button outline"
              href={access.data.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              查看原图
              <ArrowUpRight size={16} />
            </a>
            <Link className="button primary" href="/assets">
              管理素材
            </Link>
          </div>
        </>
      ) : (
        <Empty>正在载入原图…</Empty>
      )}
    </Dialog>
  );
}

const faqs = [
  [
    "普通单图与规划创作怎样扣积分？",
    "普通单图 5 积分/张；需要规划流程的详情图、主图、套图和一键复刻统一 15 积分/张。确认生成前会展示本次总消耗。例如，6 张套图共 90 积分，8 张套图共 120 积分。",
  ],
  [
    "月卡积分与充值积分有什么区别？",
    "会员积分按月发放，在对应周期内使用；新购积分包自到账起 12 个月有效。年付一次支付，分 12 期逐月发放。优先使用即将到期的积分，历史订单按购买时的条款处理。",
  ],
  [
    "生成失败会扣积分吗？",
    "提交时预留积分，任务结束后按成功图片结算，失败部分释放。已冻结的积分在任务结束后结算；释放时已过有效期的部分会记为到期。",
  ],
  [
    "会员会自动续费吗？",
    "到期不自动扣款。已有会员时，新购会员在已购买周期结束后生效，可在账户概览及订单中核对。",
  ],
  [
    "付款后还没有到账怎么办？",
    "收银台返回页面不会直接增加积分，系统以收款通知确认为准。可以在订单详情点击“我已付款，核对到账”，由服务器向支付渠道核对；若渠道仍未确认，请保留账单与订单号联系帮助，避免重复付款。",
  ],
  [
    "可以在线申请发票吗？",
    "在线发票申请暂未开放。订单页保留购买记录，可凭订单号联系帮助渠道处理。",
  ],
];
function Faqs() {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <section className="panel faq-panel">
      <Heading title="你可能想了解" />
      {faqs.map(([question, answer], index) => (
        <div className="faq-item" key={question}>
          <button
            aria-expanded={open === index}
            onClick={() => setOpen(open === index ? null : index)}
          >
            {question}
            <ChevronDown size={17} />
          </button>
          {open === index && <p>{answer}</p>}
        </div>
      ))}
    </section>
  );
}
function Help() {
  const [tab, setTab] = useState("帮助中心"),
    announcements = usePublicAnnouncements();
  return (
    <div className="page-content">
      <div className="help-hero">
        <CircleHelp size={27} />
        <div>
          <h2>让每一步创作，都更顺手。</h2>
          <p>常见问题、产品动态和使用指南，在这里查看。</p>
        </div>
      </div>
      <div className="content-toolbar">
        <div className="filter-tabs">
          {["帮助中心", "产品消息"].map((value) => (
            <button
              className={tab === value ? "active" : ""}
              key={value}
              onClick={() => setTab(value)}
            >
              {value}
            </button>
          ))}
        </div>
        <Link className="text-link" href="/help">
          使用帮助
          <ArrowUpRight size={15} />
        </Link>
      </div>
      {tab === "帮助中心" ? (
        <Faqs />
      ) : announcements.isError ? (
        <Failure error={announcements.error} retry={announcements.refetch} />
      ) : announcements.data?.items.length ? (
        <section className="panel">
          {announcements.data.items.map((notice) => (
            <article className="notice" key={notice.id}>
              <Bell size={18} />
              <div>
                <h3>{notice.title}</h3>
                <small>{date(notice.published_at)}</small>
                <p className="notice-content">{notice.content}</p>
              </div>
            </article>
          ))}
        </section>
      ) : (
        <Empty>
          {announcements.isPending ? "正在载入产品消息…" : "暂时没有产品消息"}
        </Empty>
      )}
    </div>
  );
}
