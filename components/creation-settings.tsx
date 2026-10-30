"use client";

import type { ReactNode } from "react";
import { Check, Minus, Plus, Sparkles } from "lucide-react";
import styles from "./creation-settings.module.css";

export function CreationSettings({ children, summary, title = "输出设置", balanced = false }: { children: ReactNode; summary?: ReactNode; title?: string; balanced?: boolean }) {
  return <section className={styles.section} aria-label={title} data-balanced={balanced || undefined}>
    <header className={styles.heading}><h3>{title}</h3>{summary && <span>{summary}</span>}</header>
    <div className={styles.grid}>{children}</div>
  </section>;
}

export function SettingField({ label, icon, children, span = "third", hint }: {
  label: string; icon?: ReactNode; children: ReactNode; span?: "full" | "half" | "third" | "quarter"; hint?: string;
}) {
  return <div className={styles.field} data-span={span} role="group" aria-label={label}>
    <div className={styles.label}>{icon}<span>{label}</span></div>
    {children}
    {hint && <small className={styles.hint}>{hint}</small>}
  </div>;
}

type Choice<T extends string> = { value: T; label: string; description?: string; price?: number; recommended?: boolean; visual?: ReactNode };

export function ChoiceControl<T extends string>({ value, onChange, options, name, label, disabled = false, variant = "segments" }: {
  value: T; onChange: (value: T) => void; options: readonly Choice<T>[]; name: string; label: string; disabled?: boolean; variant?: "segments" | "cards" | "ratios";
}) {
  return <div className={styles.choices} role="radiogroup" aria-label={label} data-variant={variant} data-option-count={options.length} data-control="choices">
    {options.map(option => <label key={option.value} className={styles.choice} data-selected={value === option.value || undefined} data-disabled={disabled || undefined}>
      <input type="radio" name={name} value={option.value} checked={value === option.value} disabled={disabled} onChange={() => onChange(option.value)} />
      {option.visual && <span className={styles.visual} aria-hidden="true">{option.visual}</span>}
      <span className={styles.choiceTitle}>{option.label}{option.recommended && <span className={styles.recommendation}>推荐</span>}</span>
      {option.description && <small className={styles.description}>{option.description}</small>}
      {option.price != null && <span className={styles.price}>{option.price}<small>积分 / 张</small></span>}
      <Check className={styles.check} size={13} aria-hidden="true" />
    </label>)}
  </div>;
}

export function GenerationVersionControl({ value, onChange, disabled = false, name }: {
  value: "fast" | "standard"; onChange: (value: "fast" | "standard") => void; disabled?: boolean; name: string;
}) {
  return <ChoiceControl label="生成版本" name={name} value={value} onChange={onChange} disabled={disabled} variant="cards" options={[
    { value: "fast", label: "快速版", description: "约 5 分钟", price: 8, recommended: true },
    { value: "standard", label: "精致版", description: "约 40 分钟", price: 15 },
  ]} />;
}

export function GenerationVersionField(props: Parameters<typeof GenerationVersionControl>[0] & { span?: "full" | "half" | "third" | "quarter"; hint?: string }) {
  return <SettingField label="生成版本" icon={<Sparkles size={15} />} span={props.span ?? "half"} hint={props.hint}><GenerationVersionControl {...props} /></SettingField>;
}

export function QuantityControl({ value, onChange, allowedValues, label = "生成数量", unit = "张", disabled = false, compact = false }: {
  value: number; onChange: (value: number) => void; allowedValues: readonly number[]; label?: string; unit?: string; disabled?: boolean; compact?: boolean;
}) {
  const values = [...new Set(allowedValues)].sort((a, b) => a - b);
  const previous = [...values].reverse().find(item => item < value);
  const next = values.find(item => item > value);
  const update = (input: number) => {
    if (!Number.isFinite(input) || !values.length) return;
    const nearest = values.reduce((best, item) => Math.abs(item - input) < Math.abs(best - input) ? item : best, values[0]);
    onChange(nearest);
  };
  return <div className={styles.quantity} data-control="quantity" data-compact={compact || undefined}>
    <button type="button" aria-label={`减少${label}`} disabled={disabled || previous == null} onClick={() => previous != null && onChange(previous)}><Minus size={15} /></button>
    <label><span className={styles.srOnly}>{label}</span><input type="number" aria-label={label} min={values[0]} max={values.at(-1)} step={values.length > 1 ? values[1] - values[0] : 1} value={value} disabled={disabled || values.length < 2} onChange={event => update(event.currentTarget.valueAsNumber)} /><span>{unit}</span></label>
    <button type="button" aria-label={`增加${label}`} disabled={disabled || next == null} onClick={() => next != null && onChange(next)}><Plus size={15} /></button>
  </div>;
}

export function RatioControl({ value, onChange, options, name, disabled = false }: {
  value: string; onChange: (value: string) => void; options: readonly { value: string; label: string; note?: string }[]; name: string; disabled?: boolean;
}) {
  return <ChoiceControl label="画面比例" name={name} value={value} onChange={onChange} disabled={disabled} variant="ratios" options={options.map(option => ({
    ...option, description: option.note,
    visual: <i className={styles.ratioShape} style={{ aspectRatio: option.value.includes(":") ? option.value.replace(":", " / ") : "3 / 4" }} />,
  }))} />;
}
