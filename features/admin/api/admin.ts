import { isLocalDevelopmentAuthEnabled, LOCAL_DEV_AUTH_USER_ID } from "@/lib/auth/local-dev";
import { mapApiError } from "@/lib/api/error-mapper";

export type AdminAccess = { can_manage: boolean };

export type AdminAccount = {
  id: string;
  workspace_id: string;
  label: string;
  status: "active" | "paused" | "disabled";
  login_state: "unknown" | "ready" | "needs_login";
  last_login_at: string | null;
  last_used_at: string | null;
  profile_busy: boolean;
  active_lease_task_id: string | null;
  needs_relogin: boolean;
  worker_id?: string | null;
};

export type AdminOperationalAccount = AdminAccount & {
  lease_state: "active" | "expired" | "none";
  last_lease: {
    task_id: string;
    fencing_token: number;
    acquired_at: string | null;
    heartbeat_at: string | null;
    expires_at: string | null;
  } | null;
  platform: string;
  workspace_name: string | null;
  task_count: number;
  failed_task_count: number;
  risk_control_count: number;
  recent_tasks: Array<{
    id: string;
    platform: string;
    status: string;
    stage: string | null;
    progress: number;
    error_code: string | null;
    error_message: string | null;
    created_at: string | null;
    finished_at: string | null;
  }>;
  active_lease: {
    task_id: string;
    fencing_token: number;
    acquired_at: string | null;
    heartbeat_at: string | null;
    expires_at: string | null;
  } | null;
};

export type AdminPlatformOverview = {
  account_count: number;
  active_count: number;
  ready_count: number;
  needs_relogin_count: number;
  busy_count: number;
  accounts: AdminAccount[];
};

export type AdminOverview = {
  users: { total: number; active: number; disabled: number; recent_signups: number };
  workspaces: { total: number; active: number };
  usage: {
    jobs_total: number;
    jobs_running: number;
    jobs_failed: number;
    jobs_completed: number;
    crawl_tasks_total: number;
    crawl_tasks_running: number;
    crawl_tasks_failed: number;
    crawl_tasks_succeeded: number;
  };
  crawler_sessions: Record<string, AdminPlatformOverview>;
  generated_at: string;
};

export type AdminPagination = {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type AdminUser = {
  id: string;
  display_name: string;
  email: string | null;
  status: string;
  last_login_at: string | null;
  created_at: string | null;
  last_activity_at: string | null;
  workspace_count: number;
  active_workspace_count: number;
  workspace_jobs_total: number;
  workspace_jobs_running: number;
  workspace_jobs_failed: number;
  crawl_tasks_total: number;
  crawl_tasks_running: number;
  crawl_tasks_failed: number;
};

export type AdminLog = {
  kind: "generation" | "crawl";
  id: string;
  workspace_id: string;
  workspace_name: string | null;
  type: string;
  platform: string | null;
  status: string;
  stage: string | null;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  attempt_count: number;
  created_at: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  event_count: number;
};

export type AdminLogEvent = {
  id: string;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  message: string;
  occurred_at: string | null;
};

export type AdminLogItem = {
  id: string;
  item_key: string;
  ordinal: number;
  status: string;
  stage: string | null;
  quality: string | null;
  result_source: string | null;
  attempt_count: number;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  provider_stages: Array<{
    id: string;
    attempt_no: number;
    provider: string;
    operation: string;
    status: string;
    error_code: string | null;
    error_message: string | null;
    started_at: string | null;
    finished_at: string | null;
  }>;
};

export type AdminLogAsset = {
  id: string;
  relation_id: number;
  job_item_id: string | null;
  direction: "input" | "intermediate" | "output";
  role: string;
  ordinal: number;
  is_deliverable: boolean;
  validation: Record<string, unknown>;
  original_filename: string;
  content_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  origin_kind: string;
  lifecycle_status: string;
  created_at: string | null;
  access_url: string | null;
  download_url: string | null;
  access_expires_at: string | null;
};

export type AdminLogDetail = AdminLog & {
  events: AdminLogEvent[];
  items: AdminLogItem[];
  assets: AdminLogAsset[];
  input_assets: AdminLogAsset[];
  output_assets: AdminLogAsset[];
  intermediate_assets: AdminLogAsset[];
};

export type AdminAdministrator = {
  user_id: string;
  display_name: string;
  email: string | null;
  status: "active" | "disabled";
  role: "admin" | "super_admin";
  permissions: string[];
  source: "managed" | "bootstrap";
  user_status: string;
  created_at: string | null;
  updated_at: string | null;
  last_login_at: string | null;
};

export type AdminWorkspace = {
  id: string;
  name: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  last_activity_at: string | null;
  member_count: number;
  active_member_count: number;
  jobs_total: number;
  jobs_running: number;
  jobs_failed: number;
  crawl_tasks_total: number;
  crawl_tasks_running: number;
  crawl_tasks_failed: number;
  account_count: number;
  active_account_count: number;
  needs_relogin_count: number;
};

export type UserWorkspace = {
  id: string;
  name: string;
  role: "owner" | "admin" | "member" | "viewer" | string;
};

type AdminListResponse<T> = {
  items: T[];
  pagination: AdminPagination;
};

function requestHeaders() {
  const headers = new Headers({ Accept: "application/json" });
  if (isLocalDevelopmentAuthEnabled()) {
    headers.set("X-User-Id", LOCAL_DEV_AUTH_USER_ID);
  }
  return headers;
}

async function parseResponse<T>(response: Response): Promise<T> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw mapApiError(undefined, response);
  }
  if (!response.ok) {
    throw mapApiError(payload, response);
  }
  const typedPayload = payload as { data: T } | T;
  return typeof typedPayload === "object" && typedPayload !== null && "data" in typedPayload
    ? typedPayload.data as T
    : typedPayload as T;
}

function listQuery(params: { limit?: number; offset?: number; status?: string; search?: string }) {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.status) query.set("status", params.status);
  if (params.search) query.set("search", params.search);
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

export async function getAdminAccess(signal?: AbortSignal): Promise<AdminAccess> {
  const response = await fetch("/api/v1/admin/overview/access", {
    credentials: "include",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<AdminAccess>(response);
}

export async function getAdminOverview(signal?: AbortSignal): Promise<AdminOverview> {
  const response = await fetch("/api/v1/admin/overview", {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<AdminOverview>(response);
}

export async function getAdminUsers(
  params: { limit?: number; offset?: number; status?: string; search?: string } = {},
  signal?: AbortSignal,
): Promise<AdminListResponse<AdminUser>> {
  const response = await fetch(`/api/v1/admin/users${listQuery(params)}`, {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<AdminListResponse<AdminUser>>(response);
}

export async function getAdminWorkspaces(
  params: { limit?: number; offset?: number; status?: string; search?: string } = {},
  signal?: AbortSignal,
): Promise<AdminListResponse<AdminWorkspace>> {
  const response = await fetch(`/api/v1/admin/workspaces${listQuery(params)}`, {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<AdminListResponse<AdminWorkspace>>(response);
}

/** Workspaces visible to the signed-in user (already membership-filtered). */
export async function getUserWorkspaces(signal?: AbortSignal): Promise<UserWorkspace[]> {
  const response = await fetch("/api/v1/workspaces", {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<UserWorkspace[]>(response);
}
export async function getAdminLogs(
  params: {
    kind?: "all" | "generation" | "crawl";
    user_id?: string;
    workspace_id?: string;
    platform?: string;
    status?: string;
    created_from?: string;
    created_to?: string;
    query?: string;
    job_id?: string;
    crawl_task_id?: string;
    account_id?: string;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<AdminListResponse<AdminLog>> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const encoded = query.toString();
  const response = await fetch(`/api/v1/admin/logs${encoded ? `?${encoded}` : ""}`, {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<AdminListResponse<AdminLog>>(response);
}


export async function getAdminLogDetail(
  kind: "generation" | "crawl",
  id: string,
  signal?: AbortSignal,
): Promise<AdminLogDetail> {
  const response = await fetch(`/api/v1/admin/logs/${kind}/${encodeURIComponent(id)}`, {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<AdminLogDetail>(response);
}

export async function getAdminAccounts(
  params: {
    workspace_id?: string;
    platform?: string;
    status?: string;
    login_state?: string;
    needs_relogin?: boolean;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<AdminListResponse<AdminOperationalAccount>> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const encoded = query.toString();
  const response = await fetch(`/api/v1/admin/accounts${encoded ? `?${encoded}` : ""}`, {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  return parseResponse<AdminListResponse<AdminOperationalAccount>>(response);
}


export async function getAdminAdministrators(
  params: { role?: string; status?: string; search?: string; limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<AdminListResponse<AdminAdministrator> & { can_manage: boolean }> {
  const response = await fetch(`/api/v1/admin/administrators${listQuery(params)}`, {
    credentials: "include",
    cache: "no-store",
    headers: requestHeaders(),
    signal,
  });
  const payload = await parseResponse<AdminListResponse<AdminAdministrator> & { can_manage: boolean }>(response);
  return payload;
}

export async function createAdminAdministrator(
  email: string,
): Promise<AdminAdministrator> {
  const response = await fetch("/api/v1/admin/administrators", {
    method: "POST",
    credentials: "include",
    headers: { ...Object.fromEntries(requestHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const payload = await parseResponse<AdminAdministrator>(response);
  return payload;
}

export async function updateAdminAdministrator(
  userId: string,
  changes: {
    status?: "active" | "disabled";
    role?: "admin" | "super_admin";
    permissions?: string[];
  },
): Promise<AdminAdministrator> {
  const response = await fetch(`/api/v1/admin/administrators/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    credentials: "include",
    headers: { ...Object.fromEntries(requestHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  const payload = await parseResponse<AdminAdministrator>(response);
  return payload;
}
