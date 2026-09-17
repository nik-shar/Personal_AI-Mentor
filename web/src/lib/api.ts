/**
 * Typed client for the FastAPI backend.
 *
 * All requests are relative ("/api/...") and proxied to the Python backend by
 * the Next.js rewrite in next.config.ts — so there is a single origin for the
 * browser (no CORS) and the backend host is configured in one place.
 */

import type {
  ApplicationHistoryResponse,
  ApplicationsResponse,
  ArchitectureDescriptor,
  AvailabilityResponse,
  ChatResponse,
  GraphResponse,
  HealthResponse,
  MemoriesResponse,
  NodeDetail,
  ProfileFactsResponse,
  ProfileResponse,
  RoadmapsResponse,
  ScheduleResponse,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError(
      `Cannot reach the mentor backend. Is FastAPI running on :8000? (${(err as Error).message})`,
      0,
    );
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* keep default */
    }
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/* ----------------------------- health ----------------------------- */

export const getHealth = () => request<HealthResponse>("/api/health");

/* -------------------------- architecture -------------------------- */

export const getArchitecture = () =>
  request<ArchitectureDescriptor>("/api/architecture");

/* ------------------------------ chat ------------------------------ */

export const sendChat = (message: string, sessionId: string | null) =>
  request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId }),
  });

export const endSession = (sessionId: string) =>
  request<{ status: string; session_id: string; turns_saved: number }>(
    "/api/session/end",
    { method: "POST", body: JSON.stringify({ session_id: sessionId }) },
  );

/* ---------------------------- roadmaps ---------------------------- */

export const getRoadmaps = () => request<RoadmapsResponse>("/api/roadmaps");

export const getGraph = (graphId: string) =>
  request<GraphResponse>(`/api/roadmaps/${encodeURIComponent(graphId)}`);

export const getNodeDetail = (filename: string) =>
  request<NodeDetail>(`/api/nodes/detail?filename=${encodeURIComponent(filename)}`);

/* ----------------------------- profile ---------------------------- */

export const getProfile = () => request<ProfileResponse>("/api/profile");

export const getProfileFacts = () => request<ProfileFactsResponse>("/api/profile/facts");

export const updateProfileFact = (key: string, value: unknown) =>
  request<{ status: string; fact: unknown }>(`/api/profile/${encodeURIComponent(key)}`, {
    method: "PATCH",
    body: JSON.stringify({ value }),
  });

export const deleteProfileFact = (key: string) =>
  request<{ status: string }>(`/api/profile/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });

/* ------------------------------ memory ---------------------------- */

export const getMemories = (params?: {
  type?: string;
  source?: string;
  includeArchived?: boolean;
}) => {
  const qs = new URLSearchParams();
  if (params?.type) qs.set("type", params.type);
  if (params?.source) qs.set("source", params.source);
  if (params?.includeArchived) qs.set("include_archived", "true");
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<MemoriesResponse>(`/api/memories${suffix}`);
};

export const getPendingMemories = () =>
  request<MemoriesResponse>("/api/memories/pending");

export const confirmMemory = (id: string) =>
  request<{ status: string; memory: unknown }>(
    `/api/memories/${encodeURIComponent(id)}/confirm`,
    { method: "POST" },
  );

export const correctMemory = (id: string, content: string, reason: string) =>
  request<{ status: string; memory: unknown }>(
    `/api/memories/${encodeURIComponent(id)}/correct`,
    { method: "POST", body: JSON.stringify({ content, reason }) },
  );

export const deleteMemory = (id: string) =>
  request<{ status: string }>(`/api/memories/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });

/* ----------------------------- schedule --------------------------- */

export const getSchedule = (params?: {
  date?: string;
  status?: string;
  category?: string;
}) => {
  const qs = new URLSearchParams();
  if (params?.date) qs.set("date", params.date);
  if (params?.status) qs.set("status", params.status);
  if (params?.category) qs.set("category", params.category);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<ScheduleResponse>(`/api/schedule${suffix}`);
};

export const getAvailability = (date?: string) =>
  request<AvailabilityResponse>(
    `/api/schedule/availability${date ? `?date=${encodeURIComponent(date)}` : ""}`,
  );

export const updateScheduleEvent = (eventId: string, updates: Record<string, unknown>) =>
  request<{ status: string; event: unknown }>(
    `/api/schedule/${encodeURIComponent(eventId)}`,
    { method: "PATCH", body: JSON.stringify(updates) },
  );

export const deleteScheduleEvent = (eventId: string) =>
  request<{ status: string }>(`/api/schedule/${encodeURIComponent(eventId)}`, {
    method: "DELETE",
  });

/* --------------------------- applications ------------------------- */

export const getApplications = () => request<ApplicationsResponse>("/api/applications");

export const getApplicationHistory = (limit = 30) =>
  request<ApplicationHistoryResponse>(`/api/applications/history?limit=${limit}`);

export const createApplication = (payload: Record<string, unknown>) =>
  request<{ status: string; application: unknown }>("/api/applications", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateApplicationStage = (
  company: string,
  roleTitle: string | null,
  stage: string,
) =>
  request<{ status: string }>("/api/applications/stage", {
    method: "POST",
    body: JSON.stringify({ company, role_title: roleTitle, stage }),
  });

export const addApplicationNote = (company: string, roleTitle: string | null, note: string) =>
  request<{ status: string }>("/api/applications/note", {
    method: "POST",
    body: JSON.stringify({ company, role_title: roleTitle, note }),
  });

export const attachJobDescription = (
  company: string,
  roleTitle: string | null,
  jdText: string,
) =>
  request<{ status: string }>("/api/applications/jd", {
    method: "POST",
    body: JSON.stringify({ company, role_title: roleTitle, jd_text: jdText }),
  });

export const deleteApplication = (company: string, role?: string) =>
  request<{ status: string }>(
    `/api/applications/${encodeURIComponent(company)}${
      role ? `?role=${encodeURIComponent(role)}` : ""
    }`,
    { method: "DELETE" },
  );

export const getArtifact = (company: string, role: string, kind: "jd" | "resume" | "fit") =>
  request<{ filename: string; path: string; body: string }>(
    `/api/applications/artifact?company=${encodeURIComponent(
      company,
    )}&role=${encodeURIComponent(role)}&kind=${kind}`,
  );

/* ------------------------------- jobs ----------------------------- */

export const getJobs = () =>
  request<{ count: number; jobs?: unknown[]; listings?: unknown[]; stats?: unknown }>(
    "/api/jobs",
  );

export const deleteJobListing = (listingId: string) =>
  request<{ status: string }>(`/api/jobs/${encodeURIComponent(listingId)}`, {
    method: "DELETE",
  });
