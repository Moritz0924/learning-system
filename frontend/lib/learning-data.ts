import {
  MdOutlineAssignment,
  MdToday,
  MdSchool,
  MdQuiz,
  MdTimeline,
  MdTrendingUp,
  MdSettings,
  MdTune,
  MdInsertDriveFile,
  MdCode,
  MdSlowMotionVideo
} from "react-icons/md";
import type { IconType } from "react-icons";

export type Task = {
  id: string;
  title: string;
  objective: string;
  task_type: string;
  scheduled_date?: string;
  estimated_minutes: number;
  status: string;
  knowledge_node_id: string;
  knowledge_node_code: string;
  knowledge_node_title: string;
};

export type PlanAdjustment = {
  adjustment_id: string;
  new_plan_id?: string | null;
  decision: string;
  status?: string;
  policy_version?: string;
  automation_allowed?: boolean;
  requires_confirmation?: boolean;
  evidence_json?: Record<string, unknown>;
  change_summary: Record<string, unknown>;
  rationale_json: Record<string, unknown>;
  plan_patch?: Record<string, unknown>;
  before_snapshot?: Record<string, unknown>;
  after_snapshot?: Record<string, unknown>;
  active_plan?: { id: string; version: number };
  created_tasks?: Task[];
};

export type LearningEvent = {
  id: string;
  event_type: string;
  source: string;
  task_id?: string | null;
  session_id?: string | null;
  occurred_at?: string | null;
  event_payload?: Record<string, unknown>;
};

export type RoadmapNode = {
  node_id: string;
  knowledge_node_id: string;
  task_id: string | null;
  title: string;
  objective: string;
  order: number;
  status: "completed" | "current" | "locked";
  progress: number;
};

export type RoadmapStage = {
  stage_id: string;
  title: string;
  objective: string;
  order: number;
  status: "completed" | "current" | "locked";
  progress: number;
  nodes: RoadmapNode[];
};

export type Roadmap = {
  title: string;
  locale: "zh-CN" | "en-US";
  plan_version: number;
  stages: RoadmapStage[];
};

export type StatePayload = {
  user_id: string;
  goal: { id: string; title: string | null };
  active_plan: { id: string; version: number };
  baseline_diagnostic?: Record<string, unknown>;
  mastery_summary: Array<{ label: string; score: number; confidence: number; evidence_count: number }>;
  current_state: {
    review_queue?: Array<Record<string, string>>;
    next_action?: string;
    recent_learning_events?: LearningEvent[];
    completion_rate_7d?: number | null;
    latest_plan_adjustment?: PlanAdjustment | null;
  };
  generated_from?: Record<string, unknown>;
  latest_plan_adjustment?: PlanAdjustment | null;
  today_tasks: Task[];
  updated_at?: string;
  roadmap?: Roadmap | null;
};

export type GoalResponse = {
  user_id: string;
  goal_id: string;
  status: string;
};

export type GoalListItem = {
  goal_id: string;
  title: string;
  target_outcome: string;
  deadline: string | null;
  weekly_hours_target: number;
  status: string;
  created_at: string;
};

export type DiagnosisResponse = {
  entry_node_code: string;
  active_plan_version: number;
};

export type Citation = {
  citation_label?: string;
  citation_id?: string;
  title?: string | null;
  source_type?: string;
  excerpt?: string | null;
  source_title?: string | null;
  source_url?: string | null;
};

export type ChatResponse = {
  final_answer: string;
  citations: Citation[];
  grounding_status?: string | null;
  insufficient_evidence?: boolean;
  missing_information?: string[];
  runtime_metadata?: {
    llm?: {
      mode?: string;
      is_remote?: boolean;
      model?: string;
    };
    rag?: {
      mode?: string;
      citation_count?: number;
      fallback_citations?: boolean;
      embedding_provider?: string;
    };
    memory_write?: {
      candidate_count: number;
      approved_count: number;
      saved_count: number;
      rejected_count: number;
      conflict_count: number;
      policy_version: "memory-gate-v1";
    };
  };
};

export type TutorTranscriptMessage = {
  id: string;
  run_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations?: Citation[];
  grounding_status?: string | null;
};

export type TutorTranscriptResponse = {
  messages: TutorTranscriptMessage[];
  next_before: string | null;
};

export type TutorConversation = {
  thread_id: string;
  goal_id: string;
  title: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AssessmentOption = {
  option_id: string;
  label: string;
};

export type AssessmentItem = {
  item_id: string;
  prompt: string;
  question_type: "choice" | "explain" | "code_reading" | "scenario";
  options: AssessmentOption[];
  difficulty: number;
};

export type AssessmentDraft = {
  assessment_id: string;
  assessment_type: "daily" | "weekly" | "phase";
  status: "active";
  scope: { item_count: number };
  items: AssessmentItem[];
  phase_assessment_state_id?: string;
  phase_code?: string;
};

export type AssessmentResult = {
  assessment_id: string;
  attempt_id: string;
  status: "graded" | "review_required";
  score: number | null;
  feedback: string;
  grading: {
    mode: "deterministic_exact" | "remote_structured" | "deterministic_fallback" | "manual_review_required";
    grader_version: string;
    confidence: number | null;
    needs_review: boolean;
    automatic_mastery_eligible: boolean;
  };
  mastery_updates: Array<{
    label: string;
    previous_score: number;
    new_score: number;
    new_confidence: number;
    automatic_adjustment_eligible: boolean;
    reason_codes: string[];
  }>;
  answers: Array<{
    item_id: string;
    score: number | null;
    feedback: string;
    wrong_reason_tags: string[];
    confidence: number | null;
    needs_review: boolean;
  }>;
  observer_decision: {
    policy_version: string;
    decision: "keep" | "reduce" | "remediate" | "advance" | "manual_review";
    automation_allowed: boolean;
    confidence: number;
    reason_codes: string[];
    user_facing_rationale: string;
  };
  plan_adjustment?: {
    adjustment_id: string;
    decision: string;
    status: "proposed";
    automation_allowed: boolean;
    change_summary: Record<string, unknown>;
    rationale: string;
  } | null;
};

export type SourceResult = {
  title: string;
  url: string;
  snippet: string;
  retrieved_at: string;
  source_level: string;
  retrieval_mode?: string;
  is_live_search?: boolean;
  trust_label?: string;
};

export type NavItem = {
  id: string;
  labelKey: string;
  href: string;
  icon: IconType;
};

export type ResourceRow = {
  icon: IconType;
  titleKey: string;
  typeKey: string;
  size: string;
  action: "view" | "copy" | "watch";
  detailKey: string;
};

export const navItems: NavItem[] = [
  { id: "diagnosis", labelKey: "nav.diagnosis", href: "/diagnosis", icon: MdOutlineAssignment },
  { id: "today", labelKey: "nav.today", href: "/today", icon: MdToday },
  { id: "tutor", labelKey: "nav.tutor", href: "/tutor", icon: MdSchool },
  { id: "assessment", labelKey: "nav.assessment", href: "/assessment", icon: MdQuiz },
  { id: "path", labelKey: "nav.path", href: "/path", icon: MdTimeline },
  { id: "progress", labelKey: "nav.progress", href: "/progress", icon: MdTrendingUp },
  { id: "settings", labelKey: "nav.settings", href: "/settings", icon: MdSettings },
  { id: "ai-config", labelKey: "nav.aiConfig", href: "/ai-config", icon: MdTune }
];

export const fallbackTasks: Task[] = [
  {
    id: "task-demo-1",
    title: "模型能力对比与选择策略",
    objective: "完成模型选择笔记",
    task_type: "阅读",
    estimated_minutes: 20,
    status: "done",
    knowledge_node_id: "node-llm_api_basics",
    knowledge_node_code: "llm_api_basics",
    knowledge_node_title: "LLM API Basics"
  },
  {
    id: "task-demo-2",
    title: "提示词设计原则与技巧",
    objective: "写出一个稳定提示模板",
    task_type: "视频",
    estimated_minutes: 25,
    status: "active",
    knowledge_node_id: "node-rag_foundations",
    knowledge_node_code: "rag_foundations",
    knowledge_node_title: "RAG Foundations"
  },
  {
    id: "task-demo-3",
    title: "提示词实战：优化输出质量",
    objective: "提交优化前后对比",
    task_type: "实操",
    estimated_minutes: 25,
    status: "pending",
    knowledge_node_id: "node-rag_foundations",
    knowledge_node_code: "rag_foundations",
    knowledge_node_title: "RAG Foundations"
  }
];

export const fallbackState: StatePayload = {
  user_id: "demo-user",
  goal: { id: "demo-goal", title: "学习 AI 应用开发" },
  active_plan: { id: "demo-plan", version: 1 },
  mastery_summary: [
    { label: "Python 基础", score: 78, confidence: 0.9, evidence_count: 2 },
    { label: "FastAPI 基础", score: 78, confidence: 0.82, evidence_count: 2 },
    { label: "LLM API 基础", score: 55, confidence: 0.68, evidence_count: 1 },
    { label: "RAG 基础", score: 45, confidence: 0.55, evidence_count: 1 },
    { label: "LangGraph 基础", score: 35, confidence: 0.48, evidence_count: 1 }
  ],
  current_state: { review_queue: [], next_action: "study" },
  latest_plan_adjustment: null,
  today_tasks: fallbackTasks,
  roadmap: null,
};

export const resourceRows: ResourceRow[] = [
  {
    icon: MdInsertDriveFile,
    titleKey: "resource.modelGuide",
    typeKey: "resource.document",
    size: "3.2 MB",
    action: "view",
    detailKey: "resource.modelGuideDetail"
  },
  {
    icon: MdInsertDriveFile,
    titleKey: "resource.promptCard",
    typeKey: "resource.document",
    size: "1.8 MB",
    action: "view",
    detailKey: "resource.promptCardDetail"
  },
  {
    icon: MdCode,
    titleKey: "resource.promptLibrary",
    typeKey: "resource.code",
    size: "12.4 KB",
    action: "copy",
    detailKey: "resource.promptLibraryDetail"
  },
  {
    icon: MdSlowMotionVideo,
    titleKey: "resource.promptDemo",
    typeKey: "resource.video",
    size: "48.6 MB",
    action: "watch",
    detailKey: "resource.promptDemoDetail"
  }
];

export function statusText(status: string, t: (key: string) => string) {
  if (status === "done" || status === "completed") return t("status.completed");
  if (status === "active") return t("status.active");
  return t("status.pending");
}

export function formatMasteryName(name: string, t: (key: string) => string) {
  const keys: Record<string, string> = {
    python_foundations: "mastery.python",
    fastapi_basics: "mastery.fastapi",
    llm_api_basics: "mastery.llmApi",
    rag_foundations: "mastery.rag",
    langgraph_basics: "mastery.langgraph"
  };
  return keys[name] ? t(keys[name]) : name;
}

export function localizeDemoTask(task: Task, t: (key: string) => string) {
  const keys: Record<string, [string, string, string]> = {
    "task-demo-1": ["demo.task1Title", "demo.task1Objective", "demo.task1Type"],
    "task-demo-2": ["demo.task2Title", "demo.task2Objective", "demo.task2Type"],
    "task-demo-3": ["demo.task3Title", "demo.task3Objective", "demo.task3Type"],
  };
  const copy = keys[task.id];
  return copy ? { ...task, title: t(copy[0]), objective: t(copy[1]), task_type: t(copy[2]) } : task;
}
