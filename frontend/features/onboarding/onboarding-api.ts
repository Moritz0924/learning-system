import { postRequest } from "@/lib/api";

import type {
  DynamicDiagnosticDraftRequest,
  DynamicDiagnosticDraftResponse,
  InitializeFromDraftRequest,
  OnboardingInitializationResponse,
} from "./types";


export function createDynamicDiagnosticDraft(request: DynamicDiagnosticDraftRequest) {
  return postRequest<DynamicDiagnosticDraftResponse>("/api/onboarding/dynamic-drafts", request);
}

export function initializeFromDraft(request: InitializeFromDraftRequest) {
  return postRequest<OnboardingInitializationResponse>("/api/onboarding/initialize-from-draft", request);
}
