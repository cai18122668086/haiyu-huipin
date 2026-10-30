"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";

export function JobCreditSettlement({ workspaceId, jobId }: { workspaceId: string; jobId: string }) {
  const query = useQuery({ queryKey: ["job-credit-settlement", workspaceId, jobId], queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
    "/api/v1/workspaces/{workspace_id}/credits/jobs/{job_id}", { params: { path: { workspace_id: workspaceId, job_id: jobId } }, signal },
  )), retry: false, refetchInterval: current => !current.state.error && current.state.data?.settled_credits == null ? 5000 : false });
  return <section className="job-detail-section" aria-label="本次任务积分"><h3>本次任务积分</h3>
    {query.data ? <><p>预留 {query.data.reserved_credits} 积分</p>{query.data.settled_credits != null ? <p>已结算 {query.data.settled_credits} 积分 · 已释放 {query.data.released_credits} 积分</p> : <p>等待结算，按成功交付图片收取积分，未使用部分会自动释放。</p>}</> : <p>{query.isError ? "暂无可读取的积分预留记录，可在明细中核对。" : "正在同步本次积分…"}</p>}
    <Link href="/account#credits">查看积分明细</Link>
  </section>;
}
