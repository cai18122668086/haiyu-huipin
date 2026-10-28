"use client";

import { Sparkles } from "lucide-react";
import { DETAIL_SKILLS, type DetailSkill } from "@/lib/detail-skills";
import styles from "./detail-skill-choice.module.css";

export function DetailSkillChoice({ value, disabled, onChange }: {
  value: DetailSkill; disabled: boolean; onChange: (value: DetailSkill) => void;
}) {
  return <fieldset className={styles.fieldset} disabled={disabled}>
    <legend>选择生成方案</legend>
    <p className={styles.hint}>根据这次的制作需求，选择合适的详情图方案。</p>
    <div className={styles.options}>
      {DETAIL_SKILLS.map(option => <label key={option.value} className={styles.option} data-selected={value === option.value}>
        <input type="radio" name="detail-skill" value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} />
        <span className={styles.icon}>{<Sparkles size={20} />}</span>
        <span><strong>{option.title}</strong><small>{option.description}</small></span>
      </label>)}
    </div>
    {disabled && <p className={styles.hint}>此任务已保存所选方案。如需切换，请新建任务。</p>}
  </fieldset>;
}
