"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowRight, LoaderCircle } from "lucide-react";
import { useContinueDetailBrand, useResolveDetailIntake } from "@/features/jobs/api/jobs";
import { errorMessage } from "@/lib/api/request";
import type { JobDetail } from "@/lib/api/types";
import { needsAutomaticBrandHandling } from "@/lib/detail-brand";

const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const objects = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(object) : [];

export function DomesticDetailIntakeQuestion({ workspaceId, job }: { workspaceId?: string; job: JobDetail }) {
  const resolve = useResolveDetailIntake(workspaceId);
  const automaticBrand = useContinueDetailBrand(workspaceId);
  const attempted = useRef<string>("");
  const [choices, setChoices] = useState<Record<string, string>>({});
  const intake = object(job.plan?.payload.product_intake);
  const conflicts = objects(intake.source_conflicts);
  const facts = [...objects(intake.user_facts), ...objects(intake.observed_facts)];
  const factById = new Map(facts.map(fact => [String(fact.id), fact]));
  const brandOnly = needsAutomaticBrandHandling(intake);
  const allSelected = conflicts.every(conflict => Array.isArray(conflict.alternative_fact_ids) && conflict.alternative_fact_ids.includes(choices[String(conflict.id)]));
  const revision = job.plan?.revision;
  const continueBrand = automaticBrand.mutate;
  useEffect(() => {
    const key = `${job.id}:${revision}`;
    if (!brandOnly || !revision || !workspaceId || attempted.current === key) return;
    attempted.current = key;
    continueBrand({ jobId: job.id, allowedActions: job.allowed_actions, revision });
  }, [brandOnly, revision, workspaceId, job.id, job.allowed_actions, continueBrand]);

  if (brandOnly) return <section className="detail-form-card" aria-label="正在处理商品信息">
    <p><LoaderCircle className="spin" size={17} /> 正在按默认规则处理商品信息，完成后自动继续。</p>
    {automaticBrand.error && <div className="api-state api-state--error" role="alert"><p>{errorMessage(automaticBrand.error)}</p>
      <button className="primary-button" disabled={automaticBrand.isPending} onClick={() => revision && continueBrand({ jobId: job.id, allowedActions: job.allowed_actions, revision })}>重新连接并继续</button>
    </div>}
  </section>;

  function submit() {
    if (!allSelected || !job.plan) return;
    resolve.mutate({ jobId: job.id, allowedActions: job.allowed_actions, revision: job.plan.revision,
      confirmationText: "我确认以上商品事实取舍，按选定的商品规格继续分析。",
      resolutions: conflicts.map(conflict => {
        const id = String(conflict.id); const alternatives = conflict.alternative_fact_ids as string[];
        return { conflict_id: id, retained_fact_ids: [choices[id]], rejected_fact_ids: alternatives.filter(value => value !== choices[id]) };
      }) });
  }

  return <section className="detail-form-card" aria-label="商品事实确认">
    <div className="form-section-head"><AlertCircle size={22} /><div><h2>先确认商品信息</h2><p>确认以下规格信息后，将继续处理任务。</p></div></div>
    {conflicts.map(conflict => <fieldset key={String(conflict.id)} className="detail-form-card">
      <legend>{String(conflict.topic)}</legend><p>{String(conflict.why_material)}</p>
      {(conflict.alternative_fact_ids as string[]).map(id => {
        const fact = factById.get(id);
        return <label key={id} style={{ display: "block", marginBlock: 10 }}><input type="radio" name={`source-${String(conflict.id)}`} checked={choices[String(conflict.id)] === id}
          onChange={() => setChoices(current => ({ ...current, [String(conflict.id)]: id }))} /><strong>{String(fact?.text ?? id)}</strong>
          <small style={{ display: "block", marginLeft: 24 }}>{fact?.source_class === "USER_FACT" ? "您填写的商品事实" : `商品图观察 · ${String(fact?.observed_region ?? "可见区域")}`}</small></label>;
      })}
    </fieldset>)}
    {resolve.error && <div className="api-state api-state--error" role="alert"><p>{errorMessage(resolve.error)}</p></div>}
    <button className="primary-button" onClick={submit} disabled={!allSelected || resolve.isPending}>{resolve.isPending ? <LoaderCircle className="spin" size={17} /> : <ArrowRight size={17} />}确认商品信息并继续</button>
  </section>;
}
