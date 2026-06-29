import {
  MdOutlineAssignment,
  MdToday,
  MdSchool,
  MdQuiz,
  MdTimeline,
  MdTrendingUp,
  MdSettings,
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

export type StatePayload = {
  user_id: string;
  goal: { id: string; title: string | null };
  active_plan: { id: string; version: number };
  baseline_diagnostic?: Record<string, unknown>;
  mastery_summary: Record<string, { score: number; confidence: number; knowledge_node_id?: string }>;
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
};

export type GoalResponse = {
  user_id: string;
  goal_id: string;
  status: string;
};

export type DiagnosisResponse = {
  entry_node_code: string;
  active_plan_version: number;
};

export type Citation = {
  citation_label: string;
  source_title?: string | null;
  source_url?: string | null;
};

export type ChatResponse = {
  final_answer: string;
  citations: Citation[];
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
  };
};

export type AssessmentItem = {
  item_id: string;
  prompt: string;
  question_type: string;
  knowledge_node_id: string;
};

export type AssessmentDraft = {
  assessment_id: string;
  assessment_type: string;
  items: AssessmentItem[];
};

export type AssessmentResult = {
  score: number;
  feedback: string;
  mastery_updates: Array<{ knowledge_node_id: string; previous_score: number; new_score: number }>;
  answers: Array<{ item_id: string; score: number; evidence_json: { wrong_reason_tags?: string[] } }>;
};

export type DocumentRecord = {
  id: string;
  filename: string;
  mime_type: string;
  parse_status: string;
  trusted_level: number;
  source_url?: string | null;
};

export type SourceResult = {
  title: string;
  url: string;
  snippet: string;
  retrieved_at: string;
  source_level: string;
};

export type NavItem = {
  id: string;
  label: string;
  href: string;
  icon: IconType;
};

export type PathNode = {
  phase: string;
  title: string;
  progress: number;
  state: "done" | "active" | "queued";
};

export type ResourceRow = {
  icon: IconType;
  title: string;
  type: string;
  size: string;
  action: "查看" | "复制" | "观看";
  detail: string;
};

export const navItems: NavItem[] = [
  { id: "diagnosis", label: "入学诊断", href: "/diagnosis", icon: MdOutlineAssignment },
  { id: "today", label: "今日学习", href: "/today", icon: MdToday },
  { id: "tutor", label: "讲师", href: "/tutor", icon: MdSchool },
  { id: "assessment", label: "测验", href: "/assessment", icon: MdQuiz },
  { id: "path", label: "学习路径", href: "/path", icon: MdTimeline },
  { id: "progress", label: "进度", href: "/progress", icon: MdTrendingUp },
  { id: "settings", label: "设置", href: "/settings", icon: MdSettings }
];

export const pathNodes: PathNode[] = [
  { phase: "阶段 1", title: "基础准备", progress: 100, state: "done" },
  { phase: "阶段 2", title: "核心知识", progress: 100, state: "done" },
  { phase: "阶段 3", title: "AI 应用构建", progress: 35, state: "active" },
  { phase: "3.1", title: "需求分析与场景设计", progress: 100, state: "done" },
  { phase: "3.2", title: "数据获取与处理", progress: 100, state: "done" },
  { phase: "3.3", title: "模型选择与提示工程", progress: 42, state: "active" },
  { phase: "3.4", title: "RAG 与知识增强", progress: 0, state: "queued" },
  { phase: "3.5", title: "智能体工具调用", progress: 0, state: "queued" },
  { phase: "3.6", title: "AI 应用部署上线", progress: 0, state: "queued" }
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
  mastery_summary: {
    python_foundations: { score: 78, confidence: 0.9 },
    fastapi_basics: { score: 78, confidence: 0.82 },
    llm_api_basics: { score: 55, confidence: 0.68 },
    rag_foundations: { score: 45, confidence: 0.55 },
    langgraph_basics: { score: 35, confidence: 0.48 }
  },
  current_state: { review_queue: [], next_action: "study" },
  latest_plan_adjustment: null,
  today_tasks: fallbackTasks
};

export const resourceRows: ResourceRow[] = [
  {
    icon: MdInsertDriveFile,
    title: "模型选型决策指南（含场景清单）",
    type: "文档",
    size: "3.2 MB",
    action: "查看",
    detail: "用于比较推理强度、上下文长度、成本、延迟和稳定性。当前为本地资料详情，真实文件下载能力尚未接入。"
  },
  {
    icon: MdInsertDriveFile,
    title: "提示工程最佳实践卡片",
    type: "文档",
    size: "1.8 MB",
    action: "查看",
    detail: "包含任务拆解、约束表达、输出格式和评估清单。"
  },
  {
    icon: MdCode,
    title: "提示词模板库（可复制）",
    type: "代码片段",
    size: "12.4 KB",
    action: "复制",
    detail: "你是一个严谨的 AI 应用教练。先复述任务目标，再列出约束、风险和可验证输出。"
  },
  {
    icon: MdSlowMotionVideo,
    title: "提示工程案例演示",
    type: "视频",
    size: "48.6 MB",
    action: "观看",
    detail: "视频播放服务尚未接入；这里先展示课程概要和预计观看时长。"
  }
];

export function statusText(status: string) {
  if (status === "done" || status === "completed") return "已完成";
  if (status === "active") return "进行中";
  return "待开始";
}

export function formatMasteryName(name: string) {
  const labels: Record<string, string> = {
    python_foundations: "Python 基础",
    fastapi_basics: "FastAPI 基础",
    llm_api_basics: "LLM API 基础",
    rag_foundations: "RAG 基础",
    langgraph_basics: "LangGraph 基础"
  };
  return labels[name] || name;
}
