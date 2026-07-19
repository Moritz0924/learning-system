export type MemoryType =
  | "learning_preference"
  | "long_term_goal"
  | "mastery_summary"
  | "learning_milestone";

export type MemoryOrigin =
  | "explicit_user_statement"
  | "system_inference"
  | "learning_result";

export type MemoryPrivacySettings = {
  enabled: boolean;
  allow_explicit_user: boolean;
  allow_system_inference: boolean;
  allow_learning_results: boolean;
};

export const defaultMemoryPrivacy: MemoryPrivacySettings = {
  enabled: true,
  allow_explicit_user: true,
  allow_system_inference: false,
  allow_learning_results: true,
};

export type MemoryRecordPublic = {
  memory_id: string;
  goal_id: string | null;
  scope: "user" | "goal";
  memory_type: MemoryType;
  content: Record<string, unknown>;
  origin: MemoryOrigin;
  source_kind: "explicit_user" | "learning_event" | "assessment" | "mastery_record" | "system_derived";
  importance: number;
  confidence: number;
  is_enabled: boolean;
  expires_at: string | null;
  disabled_at: string | null;
  disabled_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type MemoryListResponse = {
  items: MemoryRecordPublic[];
  limit: number;
  offset: number;
  returned_count: number;
};

export type MemoryDeclarationDraft =
  | {
      memory_type: "learning_preference";
      preference_key: string;
      preference_value: string;
    }
  | {
      memory_type: "long_term_goal";
      title: string;
      target_outcome: string;
      deadline: string | null;
    };

export type MemoryDeclarationRequest = MemoryDeclarationDraft & { request_id: string };

export function memoryDeclarationFingerprint(draft: MemoryDeclarationDraft): string {
  return JSON.stringify(draft);
}

export function memoryDeclarationRequest(
  draft: MemoryDeclarationDraft,
  requestId: string,
): MemoryDeclarationRequest {
  return { ...draft, request_id: requestId };
}
