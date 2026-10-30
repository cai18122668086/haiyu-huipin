"use client";

import { useEffect, useState } from "react";
import {
  AtSign,
  BellRing,
  CalendarDays,
  ChevronDown,
  Megaphone,
  Newspaper,
  Pin,
  X,
} from "lucide-react";

import { usePublicAnnouncements } from "@/features/announcements/api/announcements";
import { errorMessage } from "@/lib/api/request";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { formatBeijingDate } from "@/lib/datetime.mjs";

const tabs = [
  { key: "notice", label: "公告通知", Icon: Megaphone },
  { key: "commerce", label: "电商资讯", Icon: Newspaper },
  { key: "related", label: "与我相关", Icon: AtSign },
] as const;
type AnnouncementTab = (typeof tabs)[number]["key"];

function formatPublishedAt(value: string | null) {
  if (!value) return "";
  return formatBeijingDate(value, {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function AnnouncementBanner() {
  const announcements = usePublicAnnouncements();
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<AnnouncementTab>("notice");
  useBodyScrollLock(open);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const items = announcements.data?.items ?? [];

  return (
    <div className="announcement-center">
      <button
        type="button"
        className="announcement-center__trigger"
        aria-label="公告通知"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <BellRing size={15} aria-hidden="true" />
        <span>公告通知</span>
        {items.length > 0 && <i className="announcement-center__trigger-dot" aria-hidden="true" />}
        <ChevronDown className="announcement-center__trigger-chevron" size={13} aria-hidden="true" />
      </button>

      {open && (
        <div
          className="announcement-center__overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className="announcement-center__dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="announcement-center-title"
          >
            <header className="announcement-center__header">
              <div>
                <span className="announcement-center__eyebrow">HAIYU HUIPIN</span>
                <h1 id="announcement-center-title">公告中心</h1>
              </div>
              <button
                type="button"
                className="announcement-center__close"
                onClick={() => setOpen(false)}
                aria-label="关闭公告中心"
              >
                <X size={19} aria-hidden="true" />
              </button>
            </header>

            <div className="announcement-center__tabs" role="tablist" aria-label="公告分类">
              {tabs.map(({ key, label: tabLabel, Icon }) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === key}
                  className={`announcement-center__tab${activeTab === key ? " is-active" : ""}`}
                  onClick={() => setActiveTab(key)}
                >
                  <Icon size={15} aria-hidden="true" />
                  <span>{tabLabel}</span>
                </button>
              ))}
            </div>

            <div className="announcement-center__scroll">
              {activeTab === "notice" && announcements.isPending ? (
                <div className="announcement-center__empty"><strong>正在加载公告</strong></div>
              ) : activeTab === "notice" && items.length > 0 ? (
                items.map((item) => (
                  <article className="announcement-center__article" key={item.id}>
                    <div className="announcement-center__article-topline">
                      <time dateTime={item.published_at ?? undefined}>{formatPublishedAt(item.published_at)}</time>
                      {item.is_pinned && <span className="announcement-center__pinned"><Pin size={12} />置顶</span>}
                    </div>
                    <h2>{item.title}</h2>
                    {item.summary && <p className="announcement-center__summary">{item.summary}</p>}
                    {item.image_url && (
                      <img className="announcement-center__image" src={item.image_url} alt={item.image_alt || item.title} />
                    )}
                    <div className="announcement-center__content">{item.content}</div>
                    {item.link_url && (
                      <a className="announcement-center__link" href={item.link_url} target="_blank" rel="noreferrer">
                        查看相关页面
                      </a>
                    )}
                    <div className="announcement-center__meta" aria-label="公告信息">
                      <span><Megaphone size={14} aria-hidden="true" />公告通知</span>
                      <span className="announcement-center__meta-date"><CalendarDays size={14} aria-hidden="true" />{formatPublishedAt(item.published_at)}</span>
                    </div>
                  </article>
                ))
              ) : (
                <div className="announcement-center__empty">
                  <span className="announcement-center__empty-icon" aria-hidden="true">
                    {activeTab === "notice" ? <Megaphone size={22} /> : activeTab === "commerce" ? <Newspaper size={22} /> : <AtSign size={22} />}
                  </span>
                  <strong>{activeTab === "notice" ? "暂无公告通知" : activeTab === "commerce" ? "暂无电商资讯" : "暂无与你相关的内容"}</strong>
                  <p>后续有新的内容时，会在这里展示。</p>
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export function AnnouncementLoadError() {
  const announcements = usePublicAnnouncements();
  if (!announcements.isError) return null;
  return <span className="sr-only">{errorMessage(announcements.error)}</span>;
}
