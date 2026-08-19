import type { DiagnosisResponse, GoalResponse, StatePayload } from "@/lib/learning-data";


export type DiagnosticOption = {
  option_id: string;
  label: string;
};

export type SelfAssessmentDimension = {
  code: string;
  title: string;
  description: string;
  minimum: number;
  maximum: number;
};

export type DiagnosticQuestion = {
  question_id: string;
  node_code: string;
  question_type: "single_choice";
  prompt: string;
  options: DiagnosticOption[];
};

export type DiagnosticTemplateResponse = {
  template_version: string;
  domain: string;
  title: string;
  self_assessment_dimensions: SelfAssessmentDimension[];
  questions: DiagnosticQuestion[];
};

export type ExplanationMode = "analogy" | "definition" | "principle" | "engineering";

export type OnboardingInitializeRequest = {
  request_id: string;
  template_version: string;
  locale: "zh-CN" | "en-US";
  goal: {
    title: string;
    target_outcome: string;
    deadline: string | null;
    weekly_hours_target: number;
    learning_preferences: {
      explanation_order: ExplanationMode[];
      preferred_session_minutes: number;
      code_first: boolean;
    };
  };
  self_assessment_answers: Array<{ dimension_code: string; level: number }>;
  knowledge_answers: Array<{ question_id: string; selected_option_id: string }>;
};

export type OnboardingInitializationResponse = {
  goal: GoalResponse;
  diagnosis: DiagnosisResponse;
  state: StatePayload;
  replayed: boolean;
};
