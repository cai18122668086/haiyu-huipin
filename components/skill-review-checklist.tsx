"use client";

import { CheckCircle2, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import styles from "./skill-review-checklist.module.css";

export type SkillReviewChecklistItem = {
  key: string;
  label: string;
};

export function SkillReviewChecklist({
  items,
  title = "交付前人工检查",
  description = "按当前 Skill 的验收规则检查结果；生成式模型不会自动替你确认视觉质量。",
  onCompleteChange,
}: {
  items: readonly SkillReviewChecklistItem[];
  title?: string;
  description?: string;
  onCompleteChange?: (complete: boolean) => void;
}) {
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const complete = useMemo(
    () => items.length > 0 && items.every((item) => checked[item.key] === true),
    [checked, items],
  );

  useEffect(() => {
    onCompleteChange?.(complete);
  }, [complete, onCompleteChange]);

  if (!items.length) return null;

  const checkedCount = items.filter((item) => checked[item.key]).length;
  return (
    <section className={styles.panel} aria-label={title}>
      <div className={styles.heading}>
        <div>
          <span className={styles.icon}><ShieldCheck size={17} /></span>
          <div>
            <h3>{title}</h3>
            <p>{description}</p>
          </div>
        </div>
        <strong data-complete={complete || undefined}>{checkedCount} / {items.length}</strong>
      </div>
      <div className={styles.list}>
        {items.map((item) => (
          <label key={item.key} className={styles.item} data-checked={checked[item.key] || undefined}>
            <input
              type="checkbox"
              checked={checked[item.key] === true}
              onChange={(event) => {
                setChecked((current) => ({ ...current, [item.key]: event.currentTarget.checked }));
              }}
            />
            <span>{item.label}</span>
            {checked[item.key] ? <CheckCircle2 size={15} aria-hidden="true" /> : null}
          </label>
        ))}
      </div>
    </section>
  );
}

