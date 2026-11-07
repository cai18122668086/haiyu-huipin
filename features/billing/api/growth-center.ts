"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";

export const newcomerOfferKey = (workspaceId: string | undefined) => ["newcomer-offer", workspaceId] as const;

export function useNewcomerOffer(workspaceId: string | undefined) {
  return useQuery({queryKey:newcomerOfferKey(workspaceId),enabled:Boolean(workspaceId),retry:false,refetchInterval:30_000,
    queryFn:async({signal})=>unwrapApiResponse(await apiClient.GET("/api/v1/workspaces/{workspace_id}/promotions/newcomer-offer",{params:{path:{workspace_id:workspaceId!}},signal}))});
}

export type TrialInteraction = { session_id?: string; job_id?: string; event?: "viewed" | "downloaded" | "continue" };

export async function markTrialViewed(workspaceId:string, interaction:TrialInteraction) {
  return unwrapApiResponse(await apiClient.POST("/api/v1/workspaces/{workspace_id}/promotions/newcomer-offer/viewed",{params:{path:{workspace_id:workspaceId}},body:{event:"viewed", ...interaction}}));
}

export async function claimOfferImpression(workspaceId:string, manual = false) {
  return unwrapApiResponse(await apiClient.POST("/api/v1/workspaces/{workspace_id}/promotions/newcomer-offer/impression",{params:{path:{workspace_id:workspaceId}},body:{manual}}));
}

export type InvitationFilter = "all"|"paid"|"unpaid"|"refunded";
export function useInvitationCenter(workspaceId:string, status:InvitationFilter, offset:number) {
  return useQuery({queryKey:["invitation-center",workspaceId,status,offset],retry:false,refetchInterval:30_000,staleTime:15_000,placeholderData:keepPreviousData,
    queryFn:async({signal})=>unwrapApiResponse(await apiClient.GET("/api/v1/workspaces/{workspace_id}/invitations",{params:{path:{workspace_id:workspaceId},query:{status,offset,limit:20}},signal}))});
}
