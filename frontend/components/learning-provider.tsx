"use client";

import { createContext, FormEvent, ReactNode, useCallback, useContext, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { getRequest, postRequest } from "@/lib/api";
import {
  AssessmentDraft,
  AssessmentResult,
  ChatResponse,
  DiagnosisResponse,
  DocumentRecord,
  fallbackState,
  formatMasteryName,
  GoalResponse,
  PlanAdjustment,
  ResourceRow,
  SourceResult,
  StatePayload,
  Task
} from "@/lib/learning-data";

type BusyKey =
  | "path"
  | "chat"
  | "assessment"
  | "submitAssessment"
  | "replan"
  | "applyAdjustment"
  | "startTask"
  | "completeTask"
  | "document"
  | "sources"
  | "refresh";

type TaskSessionResponse = {
  task: Task;
  session?: Record<string, unknown>;
  observer_decision?: Record<string, unknown> | null;
  plan_adjustment?: PlanAdjustment | null;
};

type LearningContextValue = {
  userId: string;
  setUserId: (value: string) => void;
  goalId: string;
  state: StatePayload;
  currentTask: Task;
  masteryRows: Array<[string, { score: number; confidence: number; knowledge_node_id?: string }]>;
  goalTitle: string;
  setGoalTitle: (value: string) => void;
  targetOutcome: string;
  setTargetOutcome: (value: string) => void;
  weeklyHours: number;
  setWeeklyHours: (value: number) => void;
  message: string;
  setMessage: (value: string) => void;
  chat: ChatResponse;
  assessmentMode: "daily" | "weekly" | "phase";
  setAssessmentMode: (value: "daily" | "weekly" | "phase") => void;
  assessment: AssessmentDraft | null;
  assessmentAnswers: Record<string, string>;
  setAssessmentAnswer: (itemId: string, value: string) => void;
  assessmentResult: AssessmentResult | null;
  adjustment: PlanAdjustment | null;
  adjustmentMessage: string;
  setAdjustmentMessage: (value: string) => void;
  documents: DocumentRecord[];
  sourceQuery: string;
  setSourceQuery: (value: string) => void;
  sourceResults: SourceResult[];
  note: string;
  setNote: (value: string) => void;
  status: string;
  toast: string;
  dismissToast: () => void;
  busy: Record<string, boolean>;
  savedNodes: Set<string>;
  toggleSavedNode: (nodeId: string) => void;
  resourceModal: ResourceRow | null;
  openResource: (resource: ResourceRow) => void;
  closeResource: () => void;
  copyResource: (resource: ResourceRow) => Promise<void>;
  refreshState: (nextGoalId?: string, nextUserId?: string) => Promise<void>;
  createLearningPath: () => Promise<void>;
  askTutor: (event?: FormEvent) => Promise<void>;
  createDailyAssessment: () => Promise<void>;
  submitAssessment: () => Promise<void>;
  requestPlanAdjustment: () => Promise<void>;
  applyPlanAdjustment: () => Promise<void>;
  uploadDocument: () => Promise<void>;
  fetchDocuments: () => Promise<void>;
  searchOfficialSources: () => Promise<void>;
  startTask: (task?: Task) => Promise<void>;
  completeTask: (task?: Task) => Promise<void>;
  notify: (message: string) => void;
};

const LearningContext = createContext<LearningContextValue | null>(null);

const demoChat: ChatResponse = {
  final_answer:
    "在选择模型时，优先看任务风险：高推理难度、高错误成本、需要长链路规划时使用更强模型；格式化、分类、轻量摘要可以交给低成本模型承接。",
  runtime_metadata: {
    llm: { mode: "demo", is_remote: false, model: "frontend-demo" },
    rag: { mode: "demo", citation_count: 1, fallback_citations: true }
  },
  citations: [
    {
      citation_label: "AI App Dev V1 - 模型选择",
      source_title: "课程内置资料",
      source_url: "https://docs.langchain.com/oss/python/langchain/rag"
    }
  ]
};

export function LearningProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [userId, setUserId] = useState("stage3-demo-user");
  const [goalId, setGoalId] = useState("");
  const [state, setState] = useState<StatePayload>(fallbackState);
  const [goalTitle, setGoalTitle] = useState("学习 AI 应用开发");
  const [targetOutcome, setTargetOutcome] = useState("独立构建并部署 RAG 应用");
  const [weeklyHours, setWeeklyHours] = useState(10);
  const [message, setMessage] = useState("在选择模型时，什么情况下优先考虑更强的推理模型？");
  const [chat, setChat] = useState<ChatResponse>(demoChat);
  const [assessmentMode, setAssessmentMode] = useState<"daily" | "weekly" | "phase">("daily");
  const [assessment, setAssessment] = useState<AssessmentDraft | null>(null);
  const [assessmentAnswers, setAssessmentAnswers] = useState<Record<string, string>>({});
  const [assessmentResult, setAssessmentResult] = useState<AssessmentResult | null>(null);
  const [adjustment, setAdjustment] = useState<PlanAdjustment | null>(null);
  const [adjustmentMessage, setAdjustmentMessage] = useState("本周降低负荷，并增加 RAG 与提示工程复习。");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [sourceQuery, setSourceQuery] = useState("FastAPI dependency injection");
  const [sourceResults, setSourceResults] = useState<SourceResult[]>([]);
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("等待生成学习路径");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [savedNodes, setSavedNodes] = useState<Set<string>>(() => new Set());
  const [resourceModal, setResourceModal] = useState<ResourceRow | null>(null);

  const currentTask = useMemo(
    () => state.today_tasks.find((task) => !["done", "completed"].includes(task.status)) || state.today_tasks[0],
    [state.today_tasks]
  );
  const masteryRows = useMemo(() => Object.entries(state.mastery_summary).slice(0, 8), [state.mastery_summary]);

  const notify = useCallback((nextStatus: string) => {
    setStatus(nextStatus);
    setToast(nextStatus);
  }, []);

  const runBusy = useCallback(
    async <T,>(key: BusyKey, action: () => Promise<T>) => {
      setBusy((current) => ({ ...current, [key]: true }));
      try {
        return await action();
      } catch (error) {
        notify(error instanceof Error ? error.message : "操作失败，请稍后再试。");
        return undefined;
      } finally {
        setBusy((current) => ({ ...current, [key]: false }));
      }
    },
    [notify]
  );

  const refreshState = useCallback(
    async (nextGoalId = goalId, nextUserId = userId) => {
      if (!nextGoalId) {
        notify("还没有生成学习路径，先完成入学诊断。");
        return;
      }
      await runBusy("refresh", async () => {
        const payload = await getRequest<StatePayload>(
          `/api/state/current?goal_id=${encodeURIComponent(nextGoalId)}`,
          nextUserId
        );
        setState(payload);
        if (payload.latest_plan_adjustment) {
          setAdjustment(payload.latest_plan_adjustment);
        }
        notify("学习状态已刷新");
      });
    },
    [goalId, notify, runBusy, userId]
  );

  const createLearningPath = useCallback(async () => {
    const nextUserId = userId.trim() || "stage3-demo-user";
    await runBusy("path", async () => {
      notify("正在提交诊断并生成学习路径");
      const goal = await postRequest<GoalResponse>("/api/goals", {
        user_id: nextUserId,
        email: `${nextUserId}@example.com`,
        display_name: "学习者",
        title: goalTitle,
        target_outcome: targetOutcome,
        deadline: "2026-08-15",
        weekly_hours_target: weeklyHours,
        learning_preferences: { style: "coach_then_code" }
      });
      setUserId(goal.user_id);
      setGoalId(goal.goal_id);
      const diagnosis = await postRequest<DiagnosisResponse>("/api/onboarding/diagnosis", {
        goal_id: goal.goal_id,
        self_assessment: {
          python_level: 4,
          api_level: 3,
          llm_level: 2,
          rag_level: 1,
          langgraph_level: 0
        },
        submitted_answers: {
          questions: [
            { node_code: "python_foundations", is_correct: true },
            { node_code: "fastapi_basics", is_correct: true },
            { node_code: "llm_api_basics", is_correct: false },
            { node_code: "rag_foundations", is_correct: false }
          ]
        }
      }, goal.user_id);
      const payload = await getRequest<StatePayload>(
        `/api/state/current?goal_id=${encodeURIComponent(goal.goal_id)}`,
        goal.user_id
      );
      setState(payload);
      notify(`已生成路径：入口 ${diagnosis.entry_node_code}，计划版本 ${diagnosis.active_plan_version}`);
      router.push("/path");
    });
  }, [goalTitle, notify, router, runBusy, targetOutcome, userId, weeklyHours]);

  const askTutor = useCallback(
    async (event?: FormEvent) => {
      event?.preventDefault();
      const trimmed = message.trim();
      if (!trimmed) {
        notify("请输入要追问讲师的问题。");
        return;
      }
      await runBusy("chat", async () => {
        notify("讲师正在检索资料并回答");
        if (!goalId) {
          setChat(demoChat);
          notify("已使用本地演示回答；生成学习路径后会调用后端讲师 API。");
          return;
        }
        const payload = await postRequest<ChatResponse>(
          "/api/tutor/chat",
          {
            goal_id: goalId,
            thread_id: "frontend-thread",
            message: trimmed
          },
          userId
        );
        setChat(payload);
        notify("讲师回答已更新");
      });
    },
    [goalId, message, notify, runBusy, userId]
  );

  const createDailyAssessment = useCallback(async () => {
    await runBusy("assessment", async () => {
      notify(`正在创建${assessmentMode === "daily" ? "日测" : assessmentMode === "weekly" ? "周测" : "阶段测"}`);
      const knowledgeNodeIds = [currentTask.knowledge_node_id];
      if (!goalId) {
        setAssessment({
          assessment_id: "demo-assessment",
          assessment_type: assessmentMode,
          items: [
            {
              item_id: "demo-item-1",
              prompt: "解释模型选择时如何平衡成本、延迟和推理质量。",
              question_type: "explain",
              knowledge_node_id: currentTask.knowledge_node_id
            }
          ]
        });
        setAssessmentAnswers({});
        setAssessmentResult(null);
        notify("已创建本地演示测验");
        return;
      }
      const payload =
        assessmentMode === "phase"
          ? await postRequest<AssessmentDraft>(
              "/api/assessments/phase",
              {
                goal_id: goalId,
                thread_id: "frontend-thread",
                phase_code: "phase-ai-app-v1",
                knowledge_node_ids: knowledgeNodeIds
              },
              userId
            )
          : await postRequest<AssessmentDraft>(
              "/api/assessments",
              {
                goal_id: goalId,
                thread_id: "frontend-thread",
                assessment_type: assessmentMode,
                knowledge_node_ids: knowledgeNodeIds
              },
              userId
            );
      setAssessment(payload);
      setAssessmentAnswers({});
      setAssessmentResult(null);
      notify("测验已创建");
    });
  }, [assessmentMode, currentTask.knowledge_node_id, goalId, notify, runBusy, userId]);

  const submitAssessment = useCallback(async () => {
    if (!assessment) {
      notify("请先创建测验。");
      return;
    }
    await runBusy("submitAssessment", async () => {
      notify("正在提交测验");
      const answers = Object.fromEntries(
        assessment.items.map((item) => [
          item.item_id,
          assessmentAnswers[item.item_id]?.trim() ||
            "需要根据任务难度、成本、延迟和可靠性分层选择模型，并为简单任务设置降级路径。"
        ])
      );
      if (!goalId) {
        setAssessmentResult({
          score: 60,
          feedback: "还需要补充模型降级策略和缓存策略。",
          mastery_updates: [{ knowledge_node_id: currentTask.knowledge_node_id, previous_score: 42, new_score: 56 }],
          answers: [
            { item_id: assessment.items[0].item_id, score: 60, evidence_json: { wrong_reason_tags: ["missing_tradeoff"] } }
          ]
        });
        notify("已提交本地演示测验");
        return;
      }
      const payload = await postRequest<AssessmentResult>(
        `/api/assessments/${assessment.assessment_id}/submit`,
        { answers },
        userId
      );
      setAssessmentResult(payload);
      await refreshState(goalId, userId);
      notify("测验反馈已生成");
    });
  }, [assessment, assessmentAnswers, currentTask.knowledge_node_id, goalId, notify, refreshState, runBusy, userId]);

  const requestPlanAdjustment = useCallback(async () => {
    const trimmed = adjustmentMessage.trim();
    if (!trimmed) {
      notify("请输入计划调整原因。");
      return;
    }
    await runBusy("replan", async () => {
      notify("正在请求计划调整");
      if (!goalId) {
        const demo = {
          adjustment_id: "demo-adjustment",
          decision: "reduce",
          status: "proposed",
          change_summary: { reduced_daily_load: "20%", added: ["review_tasks"] },
          plan_patch: { load_multiplier: 0.8 },
          rationale_json: { rationale: "当前模型与提示工程掌握度偏低，降低负荷并加入复习。" }
        };
        setAdjustment(demo);
        notify("已生成本地演示调整");
        return;
      }
      const payload = await postRequest<PlanAdjustment>(
        "/api/plans/replan",
        {
          goal_id: goalId,
          thread_id: "frontend-thread",
          message: trimmed
        },
        userId
      );
      setAdjustment(payload);
      await refreshState(goalId, userId);
      notify("计划调整已生成");
    });
  }, [adjustmentMessage, goalId, notify, refreshState, runBusy, userId]);

  const applyPlanAdjustment = useCallback(async () => {
    if (!adjustment) {
      notify("还没有可应用的计划调整。");
      return;
    }
    await runBusy("applyAdjustment", async () => {
      notify("正在应用计划调整");
      if (!goalId) {
        setAdjustment((current) => (current ? { ...current, status: "applied", new_plan_id: "demo-plan-v2" } : current));
        notify("已应用本地演示调整");
        return;
      }
      const payload = await postRequest<PlanAdjustment>(
        `/api/plans/adjustments/${adjustment.adjustment_id}/apply`,
        { goal_id: goalId },
        userId
      );
      setAdjustment(payload);
      await refreshState(goalId, userId);
      notify("计划调整已应用");
    });
  }, [adjustment, goalId, notify, refreshState, runBusy, userId]);

  const fetchDocuments = useCallback(async () => {
    await runBusy("document", async () => {
      const payload = await getRequest<{ documents: DocumentRecord[] }>(
        "/api/documents",
        userId
      );
      setDocuments(payload.documents);
      notify("资料列表已刷新");
    });
  }, [notify, runBusy, userId]);

  const uploadDocument = useCallback(async () => {
    const content = note.trim();
    if (!content) {
      notify("先写一点学习笔记，再保存为资料。");
      return;
    }
    await runBusy("document", async () => {
      notify("正在保存笔记并登记资料");
      const payload = await postRequest<DocumentRecord>(
        "/api/documents/upload",
        {
          filename: `learning-note-${Date.now()}.md`,
          mime_type: "text/markdown",
          content
        },
        userId
      );
      setDocuments((current) => [payload, ...current]);
      setNote("");
      notify("学习笔记已保存为资料");
    });
  }, [note, notify, runBusy, userId]);

  const searchOfficialSources = useCallback(async () => {
    const query = sourceQuery.trim();
    if (!query) {
      notify("请输入要搜索的官方资料主题。");
      return;
    }
    await runBusy("sources", async () => {
      notify("正在检索官方来源");
      const payload = await postRequest<{ results: SourceResult[] }>("/api/tools/search-official-learning-sources", {
        query,
        domains: ["fastapi.tiangolo.com", "docs.python.org", "platform.openai.com"]
      });
      setSourceResults(payload.results);
      notify("官方来源已返回");
    });
  }, [notify, runBusy, sourceQuery]);

  const setAssessmentAnswer = useCallback((itemId: string, value: string) => {
    setAssessmentAnswers((current) => ({ ...current, [itemId]: value }));
  }, []);

  const toggleSavedNode = useCallback(
    (nodeId: string) => {
      setSavedNodes((current) => {
        const next = new Set(current);
        if (next.has(nodeId)) {
          next.delete(nodeId);
          notify("已取消收藏当前节点");
        } else {
          next.add(nodeId);
          notify("已收藏当前节点");
        }
        return next;
      });
    },
    [notify]
  );

  const openResource = useCallback((resource: ResourceRow) => {
    setResourceModal(resource);
  }, []);

  const closeResource = useCallback(() => {
    setResourceModal(null);
  }, []);

  const copyResource = useCallback(
    async (resource: ResourceRow) => {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(resource.detail);
        notify("提示词模板已复制到剪贴板");
      } else {
        setResourceModal(resource);
        notify("当前浏览器不支持剪贴板，已打开模板详情。");
      }
    },
    [notify]
  );

  const startTask = useCallback(
    async (task?: Task) => {
      if (!task) {
        notify("当前没有可开始的任务。");
        return;
      }
      await runBusy("startTask", async () => {
        if (!goalId) {
          setState((current) => ({
            ...current,
            today_tasks: current.today_tasks.map((item) =>
              item.id === task.id ? { ...item, status: "active" } : item.status === "active" ? { ...item, status: "pending" } : item
            )
          }));
          notify(`已进入任务：${task.title}`);
          router.push(`/tutor?task=${encodeURIComponent(task.id)}`);
          return;
        }
        await postRequest<TaskSessionResponse>(`/api/tasks/${task.id}/start`, {}, userId);
        await refreshState(goalId, userId);
        notify(`已进入任务：${task.title}`);
        router.push(`/tutor?task=${encodeURIComponent(task.id)}`);
      });
    },
    [goalId, notify, refreshState, router, runBusy, userId]
  );

  const completeTask = useCallback(
    async (task?: Task) => {
      if (!task) {
        notify("当前没有可完成的任务。");
        return;
      }
      await runBusy("completeTask", async () => {
        if (!goalId) {
          setState((current) => ({
            ...current,
            today_tasks: current.today_tasks.map((item) => (item.id === task.id ? { ...item, status: "completed" } : item))
          }));
          notify(`已完成任务：${task.title}`);
          return;
        }
        const payload = await postRequest<TaskSessionResponse>(
          `/api/tasks/${task.id}/complete`,
          {
            duration_minutes: task.estimated_minutes,
            evidence: {
              source: "frontend",
              completed_at: new Date().toISOString(),
              task_title: task.title
            }
          },
          userId
        );
        if (payload.plan_adjustment) {
          setAdjustment(payload.plan_adjustment);
        }
        await refreshState(goalId, userId);
        notify(payload.plan_adjustment ? "任务已完成，并生成待确认调整" : `已完成任务：${task.title}`);
      });
    },
    [goalId, notify, refreshState, runBusy, userId]
  );

  const value = useMemo<LearningContextValue>(
    () => ({
      userId,
      setUserId,
      goalId,
      state,
      currentTask,
      masteryRows: masteryRows.map(([name, item]) => [formatMasteryName(name), item]),
      goalTitle,
      setGoalTitle,
      targetOutcome,
      setTargetOutcome,
      weeklyHours,
      setWeeklyHours,
      message,
      setMessage,
      chat,
      assessmentMode,
      setAssessmentMode,
      assessment,
      assessmentAnswers,
      setAssessmentAnswer,
      assessmentResult,
      adjustment,
      adjustmentMessage,
      setAdjustmentMessage,
      documents,
      sourceQuery,
      setSourceQuery,
      sourceResults,
      note,
      setNote,
      status,
      toast,
      dismissToast: () => setToast(""),
      busy,
      savedNodes,
      toggleSavedNode,
      resourceModal,
      openResource,
      closeResource,
      copyResource,
      refreshState,
      createLearningPath,
      askTutor,
      createDailyAssessment,
      submitAssessment,
      requestPlanAdjustment,
      applyPlanAdjustment,
      uploadDocument,
      fetchDocuments,
      searchOfficialSources,
      startTask,
      completeTask,
      notify
    }),
    [
      userId,
      goalId,
      state,
      currentTask,
      masteryRows,
      goalTitle,
      targetOutcome,
      weeklyHours,
      message,
      chat,
      assessmentMode,
      assessment,
      assessmentAnswers,
      setAssessmentAnswer,
      assessmentResult,
      adjustment,
      adjustmentMessage,
      documents,
      sourceQuery,
      sourceResults,
      note,
      status,
      toast,
      busy,
      savedNodes,
      toggleSavedNode,
      resourceModal,
      openResource,
      closeResource,
      copyResource,
      refreshState,
      createLearningPath,
      askTutor,
      createDailyAssessment,
      submitAssessment,
      requestPlanAdjustment,
      applyPlanAdjustment,
      uploadDocument,
      fetchDocuments,
      searchOfficialSources,
      startTask,
      completeTask,
      notify
    ]
  );

  return <LearningContext.Provider value={value}>{children}</LearningContext.Provider>;
}

export function useLearning() {
  const context = useContext(LearningContext);
  if (!context) {
    throw new Error("useLearning must be used inside LearningProvider");
  }
  return context;
}
