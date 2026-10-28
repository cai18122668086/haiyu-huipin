"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";

export const announcementKeys = {
  all: ["announcements"] as const,
  current: () => [...announcementKeys.all, "current"] as const,
  publicList: (limit = 20) => [...announcementKeys.all, "public", limit] as const,
  adminAccess: () => [...announcementKeys.all, "admin-access"] as const,
  adminList: (status: "all" | "draft" | "published" | "archived") =>
    [...announcementKeys.all, "admin", status] as const,
};

export function useCurrentAnnouncement() {
  return useQuery({
    queryKey: announcementKeys.current(),
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET("/api/v1/announcements/current", { signal });
      return unwrapApiResponse(result);
    },
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });
}

export function usePublicAnnouncements(limit = 20) {
  return useQuery({
    queryKey: announcementKeys.publicList(limit),
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET("/api/v1/announcements", {
        params: { query: { limit } },
        signal,
      });
      return unwrapApiResponse(result);
    },
    staleTime: 60_000,
  });
}

export function useAnnouncementAdminAccess() {
  return useQuery({
    queryKey: announcementKeys.adminAccess(),
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET("/api/v1/admin/announcements/access", { signal });
      return unwrapApiResponse(result);
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useAdminAnnouncements(
  status: "all" | "draft" | "published" | "archived" = "all",
  enabled = true,
) {
  return useQuery({
    queryKey: announcementKeys.adminList(status),
    enabled,
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET("/api/v1/admin/announcements", {
        params: { query: { status } },
        signal,
      });
      return unwrapApiResponse(result);
    },
  });
}

type AnnouncementMutationInput = {
  title: string;
  summary?: string | null;
  content: string;
  image_asset_id?: string | null;
  image_alt?: string | null;
  kind: "info" | "success" | "warning" | "maintenance";
  link_url?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  is_pinned: boolean;
};

export function useCreateAnnouncement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: AnnouncementMutationInput) => {
      const result = await apiClient.POST("/api/v1/admin/announcements", { body });
      return unwrapApiResponse(result);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: announcementKeys.all });
    },
  });
}

export function useUpdateAnnouncement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: AnnouncementMutationInput & { id: string }) => {
      const result = await apiClient.PATCH("/api/v1/admin/announcements/{announcement_id}", {
        params: { path: { announcement_id: id } },
        body,
      });
      return unwrapApiResponse(result);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: announcementKeys.all });
    },
  });
}

export function usePublishAnnouncement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const result = await apiClient.POST(
        "/api/v1/admin/announcements/{announcement_id}/publish",
        { params: { path: { announcement_id: id } } },
      );
      return unwrapApiResponse(result);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: announcementKeys.all });
    },
  });
}

export function useArchiveAnnouncement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const result = await apiClient.POST(
        "/api/v1/admin/announcements/{announcement_id}/archive",
        { params: { path: { announcement_id: id } } },
      );
      return unwrapApiResponse(result);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: announcementKeys.all });
    },
  });
}
