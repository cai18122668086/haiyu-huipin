"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useRef } from "react";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";
import type { components } from "@/lib/api/generated/openapi";

export type Purchase = components["schemas"]["CreditPurchaseResponse"];
export type Product = components["schemas"]["CreditPackage"];

export function useCreditProducts(workspaceId: string) {
  return useQuery({
    queryKey: ["credit-products", workspaceId],
    queryFn: async ({ signal }) =>
      unwrapApiResponse(
        await apiClient.GET(
          "/api/v1/workspaces/{workspace_id}/credits/packages",
          {
            params: {
              path: { workspace_id: workspaceId },
              query: { include_memberships: true },
            },
            signal,
          },
        ),
      ),
    retry: false,
  });
}
export function useMembership(workspaceId: string) {
  return useQuery({
    queryKey: ["credit-membership", workspaceId],
    queryFn: async ({ signal }) =>
      unwrapApiResponse(
        await apiClient.GET(
          "/api/v1/workspaces/{workspace_id}/credits/membership",
          { params: { path: { workspace_id: workspaceId } }, signal },
        ),
      ),
    refetchInterval: 5000,
    retry: false,
  });
}
export function usePurchaseHistory(workspaceId: string) {
  return useInfiniteQuery({
    queryKey: ["credit-orders", workspaceId],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrapApiResponse(
        await apiClient.GET(
          "/api/v1/workspaces/{workspace_id}/credits/purchases",
          {
            params: {
              path: { workspace_id: workspaceId },
              query: { cursor: pageParam, limit: 30 },
            },
            signal,
          },
        ),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 5000,
    retry: false,
  });
}
export function useLedgerHistory(workspaceId: string) {
  return useInfiniteQuery({
    queryKey: ["credit-ledger-history", workspaceId],
    initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrapApiResponse(
        await apiClient.GET(
          "/api/v1/workspaces/{workspace_id}/credits/ledger",
          {
            params: {
              path: { workspace_id: workspaceId },
              query: { before_id: pageParam, limit: 50 },
            },
            signal,
          },
        ),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 5000,
    retry: false,
  });
}
export function usePurchaseOrder(workspaceId: string, orderId: string | null) {
  return useQuery({
    queryKey: ["credit-purchase", workspaceId, orderId],
    enabled: Boolean(orderId),
    queryFn: async ({ signal }) =>
      unwrapApiResponse(
        await apiClient.GET(
          "/api/v1/workspaces/{workspace_id}/credits/purchases/{purchase_id}",
          {
            params: {
              path: { workspace_id: workspaceId, purchase_id: orderId! },
            },
            signal,
          },
        ),
      ),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 2500 : 10000,
    retry: false,
  });
}
export function useReconcilePurchase(workspaceId: string, orderId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrapApiResponse(await apiClient.POST(
      "/api/v1/workspaces/{workspace_id}/credits/purchases/{purchase_id}/reconcile",
      { params: { path: { workspace_id: workspaceId, purchase_id: orderId } } },
    )),
    onSuccess: (order) => {
      client.setQueryData(["credit-purchase", workspaceId, orderId], order);
      for (const queryKey of [["credits", workspaceId], ["credit-orders", workspaceId],
        ["credit-membership", workspaceId], ["credit-ledger-history", workspaceId]])
        void client.invalidateQueries({ queryKey });
    },
  });
}

export function useCreatePurchase(workspaceId: string) {
  const queryClient = useQueryClient();
  const intent = useRef<{ productId: string; key: string } | null>(null);
  const mutation = useMutation({
    mutationFn: async ({ productId, channel }: { productId: string; channel: "alipay" | "wechat" }) => {
      const intentId = `${productId}:${channel}`;
      if (!intent.current || intent.current.productId !== intentId)
        intent.current = { productId: intentId, key: crypto.randomUUID() };
      return unwrapApiResponse(
        await apiClient.POST(
          "/api/v1/workspaces/{workspace_id}/credits/purchases",
          {
            params: {
              path: { workspace_id: workspaceId },
              header: { "Idempotency-Key": intent.current.key },
            },
            body: { package_id: productId, channel },
          },
        ),
      );
    },
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["credit-orders", workspaceId],
      }),
  });
  return {
    ...mutation,
    newIntent: () => {
      intent.current = null;
      mutation.reset();
    },
  };
}

export type RefundHistoryItem = { id: string; status: string; amount_fen: number; reason: string; created_at: string; completed_at: string | null };
export function useRefundHistory(workspaceId: string, purchaseId: string) {
  return useQuery({
    queryKey: ["credit-refunds", workspaceId, purchaseId],
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/credits/purchases/{purchase_id}/refunds",
      { params: { path: { workspace_id: workspaceId, purchase_id: purchaseId } }, signal },
    )) as { items: RefundHistoryItem[] },
    refetchInterval: 10000,
    retry: false,
  });
}
