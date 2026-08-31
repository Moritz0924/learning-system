import { postRequest } from "@/lib/api";

import type {
  DynamicDiagnosticDraftRequest,
  DynamicDiagnosticDraftResponse,
  DynamicReassessDraftRequest,
  InitializeFromDraftRequest,
  OnboardingInitializationResponse,
  ReassessFromDraftRequest,
} from "./types";


export function createDynamicDiagnosticDraft(request: DynamicDiagnosticDraftRequest) {
  return postRequest<DynamicDiagnosticDraftResponse>("/api/onboarding/dynamic-drafts", request);
}

export function initializeFromDraft(request: InitializeFromDraftRequest) {
  return postRequest<OnboardingInitializationResponse>("/api/onboarding/initialize-from-draft", request);
}

export function createReassessDraft(request: DynamicReassessDraftRequest) {
  return postRequest<DynamicDiagnosticDraftResponse>("/api/onboarding/reassess-drafts", request);
}

export function reassessFromDraft(request: ReassessFromDraftRequest) {
  return postRequest<OnboardingInitializationResponse>("/api/onboarding/reassess-from-draft", request);
}
