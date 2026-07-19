import { getRequest, postRequest } from "@/lib/api";

import type {
  DiagnosticTemplateResponse,
  OnboardingInitializationResponse,
  OnboardingInitializeRequest,
} from "./types";


export function loadDiagnosticTemplate(domain: string) {
  return getRequest<DiagnosticTemplateResponse>(`/api/onboarding/diagnostic-template?domain=${encodeURIComponent(domain)}`);
}

export function submitOnboarding(request: OnboardingInitializeRequest) {
  return postRequest<OnboardingInitializationResponse>("/api/onboarding/initialize", request);
}
