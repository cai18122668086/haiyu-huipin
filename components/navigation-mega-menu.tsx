"use client";

import { PrimaryNavigationLink } from "./primary-navigation-link";

import Link from "next/link";
import { ChevronDown, Flame, Grid2X2, Sparkles } from "lucide-react";
import { useState } from "react";
import { HOT_FEATURE_IDS } from "@/lib/feature-priorities";
import { FEATURE_THUMBNAILS } from "@/lib/creation-entrypoints";
import type {
  NavigationFeatureGroup,
  NavigationMenuDefinition,
} from "@/lib/navigation-registry";

function FeatureLinks({
  groups,
  activeFeatureId,
  onNavigate,
  idPrefix,
}: {
  groups: readonly NavigationFeatureGroup[];
  activeFeatureId?: string;
  onNavigate: () => void;
  idPrefix: string;
}) {
  return groups.map((group, index) => (
    <section key={group.label} aria-labelledby={`${idPrefix}-group-${index}`}>
      <div
        className="nav-mega__head"
        style={index === 0 ? undefined : { marginTop: 10 }}
      >
        <span id={`${idPrefix}-group-${index}`}>
          {index === 0 ? <Sparkles size={14} /> : <Grid2X2 size={14} />}
          {group.label}
        </span>
      </div>
      <div className="nav-mega__grid">
        {group.items.map((item) => {
          const Icon = item.icon;
          const active = item.featureId === activeFeatureId;
          const isHot = HOT_FEATURE_IDS.has(item.featureId);
          return (
            <Link
              key={item.featureId}
              href={item.href}
              className={`nav-tool-link${active ? " is-active" : ""}${isHot ? " is-hot" : ""}`}
              aria-current={active ? "page" : undefined}
              onClick={onNavigate}
            >
              <span className={`tool-icon ${item.tone}`} aria-hidden="true">
                {FEATURE_THUMBNAILS[item.featureId] ? <img src={FEATURE_THUMBNAILS[item.featureId]} alt="" width={42} height={42} loading="lazy" /> : <Icon size={19} />}
              </span>
              <span className="nav-tool-link__copy">
                <span className="nav-tool-link__title">
                  <strong>{item.label}</strong>
                </span>
                <small>{item.description}</small>
              </span>
              {isHot ? (
                <span
                  className="nav-tool-link__hot"
                  aria-label="热门功能"
                  title="热门功能"
                >
                  <Flame size={14} fill="currentColor" />
                </span>
              ) : null}
            </Link>
          );
        })}
      </div>
    </section>
  ));
}

export function NavigationMegaMenu({
  definition,
  activeFeatureId,
  active,
  open,
  onClose,
  onToggle,
  idPrefix = "navigation-menu",
  triggerAsButton = false,
}: {
  definition: NavigationMenuDefinition;
  activeFeatureId?: string;
  active: boolean;
  open: boolean;
  onClose: () => void;
  onToggle: () => void;
  idPrefix?: string;
  triggerAsButton?: boolean;
}) {
  const menuId = `${idPrefix}-${definition.key}`;

  return (
    <div
      className={`nav-menu${open ? " is-open" : ""}`}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          onClose();
        }
      }}
    >
      {triggerAsButton ? <button type="button" className="workspace-tool-picker" onClick={onToggle} aria-label={`切换工具：${definition.label}`} aria-controls={menuId} aria-expanded={open}><span>{definition.label}</span><ChevronDown size={16} style={{ transform: open ? "rotate(180deg)" : undefined }} /></button> : <div className={`nav-menu__trigger ${active ? "active" : ""}`}>
        <Link
          href={definition.href}
          onClick={onClose}
          style={{ color: "inherit", whiteSpace: "nowrap" }}
        >
          {definition.label}
        </Link>
        <button
          type="button"
          onClick={onToggle}
          aria-label={`展开${definition.label}`}
          aria-controls={menuId}
          aria-expanded={open}
          style={{
            display: "inline-grid",
            width: 24,
            height: 30,
            placeItems: "center",
            padding: 0,
            border: 0,
            color: "inherit",
            background: "transparent",
          }}
        >
          <ChevronDown
            size={14}
            style={{ transform: open ? "rotate(180deg)" : undefined }}
          />
        </button>
      </div>}
      <div id={menuId} className="nav-mega" aria-hidden={!open}>
        <FeatureLinks
          groups={definition.groups}
          activeFeatureId={activeFeatureId}
          onNavigate={onClose}
          idPrefix={menuId}
        />
        {definition.footer && (
          <div className="nav-mega__footer">
            <Link href={definition.footer.href} onClick={onClose}>
              {definition.footer.label}
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

export function MobileNavigationMenu({
  open,
  menus,
  onClose,
  panelRef,
  primaryLinks,
  pathname,
}: {
  open: boolean;
  menus: readonly NavigationMenuDefinition[];
  onClose: () => void;
  panelRef: React.RefObject<HTMLDivElement | null>;
  primaryLinks: readonly { label: string; href: string }[];
  pathname: string;
}) {
  const [expandedMenu, setExpandedMenu] = useState<NavigationMenuDefinition["key"] | null>(
    "all-features",
  );

  if (!open) {
    return null;
  }

  return (
    <div
      id="mobile-primary-navigation"
      ref={panelRef}
      role="dialog"
      aria-label="移动端导航"
      style={{
        position: "absolute",
        zIndex: 100,
        top: "calc(100% - 4px)",
        right: 8,
        width: "min(92vw, 430px)",
        maxHeight: "calc(100vh - 72px)",
        overflowY: "auto",
        padding: 14,
        border: "1px solid #e4e7ee",
        borderRadius: "0 0 16px 16px",
        color: "#151827",
        background: "#fff",
        boxShadow: "0 24px 50px rgb(10 20 38 / 22%)",
      }}
    >
      <div style={{ display: "grid", gap: 8 }}>
        <nav
          aria-label="移动端主导航链接"
          style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 7 }}
        >
          {primaryLinks.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <PrimaryNavigationLink key={item.href} href={item.href} label={item.label}
                active={active} onClick={onClose} mobile
                style={{
                  padding: "10px 7px",
                  border: `1px solid ${active ? "#c5df45" : "#eceef3"}`,
                  borderRadius: 9,
                  color: active ? "#242424" : "#343846",
                  background: active ? "#d8ff00" : "#fafbfc",
                  textAlign: "center",
                  fontSize: 12,
                  fontWeight: 700,
                }}
              />
            );
          })}
        </nav>
        {menus.map((menu) => {
          const expanded = expandedMenu === menu.key;
          const sectionId = `mobile-navigation-${menu.key}`;
          return (
            <section
              key={menu.key}
              style={{ border: "1px solid #eceef3", borderRadius: 12, overflow: "hidden" }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  minHeight: 46,
                  paddingLeft: 13,
                  background: expanded ? "#f6f7fb" : "#fff",
                }}
              >
                <Link
                  href={menu.href}
                  onClick={onClose}
                  style={{ flex: 1, color: "#171a27", fontSize: 14, fontWeight: 700 }}
                >
                  {menu.label}
                </Link>
                <button
                  type="button"
                  onClick={() => setExpandedMenu(expanded ? null : menu.key)}
                  aria-label={`${expanded ? "收起" : "展开"}${menu.label}`}
                  aria-controls={sectionId}
                  aria-expanded={expanded}
                  style={{
                    display: "inline-grid",
                    width: 46,
                    alignSelf: "stretch",
                    placeItems: "center",
                    border: 0,
                    color: "#596173",
                    background: "transparent",
                  }}
                >
                  <ChevronDown
                    size={16}
                    style={{ transform: expanded ? "rotate(180deg)" : undefined }}
                  />
                </button>
              </div>
              {expanded && (
                <div
                  id={sectionId}
                  style={{ display: "grid", gap: 7, padding: 10, background: "#fff" }}
                >
                  {menu.groups.map((group) => (
                    <div key={group.label} style={{ display: "grid", gap: 6 }}>
                      <strong
                        style={{
                          padding: "4px 3px 1px",
                          color: "#7a8190",
                          fontSize: 11,
                        }}
                      >
                        {group.label}
                      </strong>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 6 }}>
                        {group.items.map((item) => {
                          const active =
                            pathname === item.href || pathname.startsWith(`${item.href}/`);
                          return (
                            <Link
                              key={item.featureId}
                              href={item.href}
                              onClick={onClose}
                              aria-current={active ? "page" : undefined}
                              style={{
                                minWidth: 0,
                                padding: "10px 9px",
                                paddingRight: HOT_FEATURE_IDS.has(item.featureId) ? 30 : 9,
                                border: `1px solid ${active ? "#c5df45" : "#eceef3"}`,
                                borderRadius: 9,
                                color: active ? "#242424" : "#252938",
                                background: active ? "#d8ff00" : "#fafbfc",
                                fontSize: 12,
                                fontWeight: 650,
                                position: "relative",
                              }}
                            >
                              <span style={{ display: "inline-flex", alignItems: "center", gap: 3, paddingRight: HOT_FEATURE_IDS.has(item.featureId) ? 20 : 0 }}>
                                {FEATURE_THUMBNAILS[item.featureId] && <img className="mobile-tool-thumbnail" src={FEATURE_THUMBNAILS[item.featureId]} alt="" width={30} height={30} loading="lazy" />}
                                {item.label}
                                {HOT_FEATURE_IDS.has(item.featureId) ? (
                                  <Flame
                                    size={13}
                                    fill="currentColor"
                                    color="#786490"
                                    className="mobile-nav-tool-link__hot"
                                    style={{ position: "absolute", top: 7, right: 7, display: "block" }}
                                    aria-label="热门功能"
                                  />
                                ) : null}
                              </span>
                            </Link>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                  {menu.footer && (
                    <Link
                      href={menu.footer.href}
                      onClick={onClose}
                      style={{ padding: "7px 3px 2px", color: "#52683b", fontSize: 12, fontWeight: 700 }}
                    >
                      {menu.footer.label}
                    </Link>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
