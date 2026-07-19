import { getRequest, postRequest, putRequest } from "@/lib/api";

import type {
  MemoryListResponse,
  MemoryOrigin,
  MemoryPrivacySettings,
  MemoryRecordPublic,
  MemoryType,
} from "./types";


export function getMemoryPrivacy(): Promise<MemoryPrivacySettings> {
  return getRequest<MemoryPrivacySettings>("/api/memories/privacy");
}

export function updateMemoryPrivacy(settings: MemoryPrivacySettings): Promise<MemoryPrivacySettings> {
  return putRequest<MemoryPrivacySettings>("/api/memories/privacy", settings);
}

export function listMemories(input: {
  goalId?: string;
  memoryType?: MemoryType | "";
  sourceCategory?: MemoryOrigin | "";
  status: "active" | "inactive" | "all";
  includeUserScope?: boolean;
  limit: number;
  offset: number;
}): Promise<MemoryListResponse> {
  const params = new URLSearchParams({
    status: input.status,
    include_user_scope: String(input.includeUserScope ?? true),
    limit: String(input.limit),
    offset: String(input.offset),
  });
  if (input.goalId) params.set("goal_id", input.goalId);
  if (input.memoryType) params.set("memory_type", input.memoryType);
  if (input.sourceCategory) params.set("source_category", input.sourceCategory);
  return getRequest<MemoryListResponse>(`/api/memories?${params.toString()}`);
}

export function disableMemory(memoryId: string): Promise<MemoryRecordPublic> {
  return postRequest<MemoryRecordPublic>(`/api/memories/${encodeURIComponent(memoryId)}/disable`, {});
}
