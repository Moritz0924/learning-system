"use client";

import { createContext, FormEvent, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { deleteRequest, getRequest, postRequest, streamPostRequest } from "@/lib/api";
import {
  cancelTutorRequest,
  consumeTutorEventStream,
  isTutorStreamCurrent,
} from "@/lib/tutor-stream.mjs";
import type { TutorStreamRequest } from "@/lib/tutor-stream.mjs";
import { useAuth } from "@/components/providers/auth-provider";
import {
  getDocument,
  listDocuments,
  saveMarkdownNote,
  uploadDocumentFile,
} from "@/features/documents/document-api";
import type { DocumentRecord } from "@/features/documents/types";
import { submitOnboarding } from "@/features/onboarding/onboarding-api";
import type { OnboardingInitializeRequest } from "@/features/onboarding/types";
import {
  memoryDeclarationFingerprint,
  memoryDeclarationRequest,
} from "@/features/memory/types";
import type { MemoryDeclarationDraft } from "@/features/memory/types";
import { pollDocument } from "@/lib/document-poller";
import {
  AssessmentDraft,
  AssessmentResult,
  ChatResponse,
  fallbackState,
  formatMasteryName,
  GoalListItem,
  PlanAdjustment,
  ResourceRow,
  SourceResult,
  StatePayload,
  Task,
  TutorConversation
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
  | "fileUpload"
  | "sources"
  | "refresh";

type TaskSessionResponse = {
  task: Task;
  session?: Record<string, unknown>;
  observer_decision?: Record<string, unknown> | null;
  plan_adjustment?: PlanAdjustment | null;
};

type LearningContextValue = {
  goalId: string;
  isDemoMode: boolean;
  state: StatePayload;
  currentTask: Task | null;
  masteryRows: Array<[string, { score: number; confidence: number; knowledge_node_id?: string }]>;
  message: string;
  setMessage: (value: string) => void;
  chat: ChatResponse;
  conversations: TutorConversation[];
  activeConversationId: string;
  activeRunId: string | null;
  createConversation: () => Promise<void>;
  selectConversation: (threadId: string) => void;
  deleteConversation: (threadId: string) => Promise<void>;
  cancelTutor: () => Promise<void>;
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
  refreshState: (nextGoalId?: string) => Promise<void>;
  initializeOnboarding: (request: OnboardingInitializeRequest) => Promise<boolean>;
  createLearningPath: () => Promise<void>;
  askTutor: (event?: FormEvent, memoryDraft?: MemoryDeclarationDraft | null) => Promise<boolean>;
  createDailyAssessment: () => Promise<void>;
  submitAssessment: () => Promise<void>;
  requestPlanAdjustment: () => Promise<void>;
  applyPlanAdjustment: () => Promise<void>;
  uploadFile: (file: File) => Promise<boolean>;
  saveNote: () => Promise<void>;
  fetchDocuments: () => Promise<void>;
  refreshDocument: (documentId: string) => Promise<void>;
  searchOfficialSources: () => Promise<void>;
  startTask: (task?: Task | null) => Promise<void>;
  completeTask: (task?: Task) => Promise<void>;
  notify: (message: string) => void;
};

const LearningContext = createContext<LearningContextValue | null>(null);
const defaultTutorMessage = "在选择模型时，什么情况下优先考虑更强的推理模型？";
const defaultAdjustmentMessage = "本周降低负荷，并增加 RAG 与提示工程复习。";
const defaultSourceQuery = "FastAPI dependency injection";

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
  const { user } = useAuth();

  return (
    <IdentityScopedLearningProvider key={user?.id ?? "anonymous"} userId={user?.id}>
      {children}
    </IdentityScopedLearningProvider>
  );
}

function IdentityScopedLearningProvider({ children, userId }: { children: ReactNode; userId?: string }) {
  const router = useRouter();
  const [goalId, setGoalId] = useState("");
  const [goalBootstrap, setGoalBootstrap] = useState<"bootstrapping" | "loaded" | "no_goal" | "failed">("bootstrapping");
  const [state, setState] = useState<StatePayload>(fallbackState);
  const [message, setMessage] = useState(defaultTutorMessage);
  const [chat, setChat] = useState<ChatResponse>(demoChat);
  const [conversations, setConversations] = useState<TutorConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState("");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [assessmentMode, setAssessmentMode] = useState<"daily" | "weekly" | "phase">("daily");
  const [assessment, setAssessment] = useState<AssessmentDraft | null>(null);
  const [assessmentAnswers, setAssessmentAnswers] = useState<Record<string, string>>({});
  const [assessmentResult, setAssessmentResult] = useState<AssessmentResult | null>(null);
  const [adjustment, setAdjustment] = useState<PlanAdjustment | null>(null);
  const [adjustmentMessage, setAdjustmentMessage] = useState(defaultAdjustmentMessage);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [sourceQuery, setSourceQuery] = useState(defaultSourceQuery);
  const [sourceResults, setSourceResults] = useState<SourceResult[]>([]);
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("等待生成学习路径");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [savedNodes, setSavedNodes] = useState<Set<string>>(() => new Set());
  const [resourceModal, setResourceModal] = useState<ResourceRow | null>(null);
  const identityEpochRef = useRef(0);
  const documentPollersRef = useRef(new Map<string, () => void>());
  const busyKeysRef = useRef(new Set<BusyKey>());
  const busyActionsRef = useRef(new Map<BusyKey, Promise<unknown>>());
  const pendingMemoryRequestRef = useRef<{ fingerprint: string; requestId: string } | null>(null);
  const activeConversationIdRef = useRef("");
  const tutorRequestRef = useRef<TutorStreamRequest | null>(null);

  const currentTask = useMemo(
    () => state.today_tasks.find((task) => !["done", "completed"].includes(task.status)) || state.today_tasks[0] || null,
    [state.today_tasks]
  );
  const isDemoMode = goalBootstrap === "no_goal";
  const masteryRows = useMemo(() => Object.entries(state.mastery_summary).slice(0, 8), [state.mastery_summary]);

  const notify = useCallback((nextStatus: string) => {
    setStatus(nextStatus);
    setToast(nextStatus);
  }, []);

  useEffect(() => {
    identityEpochRef.current += 1;
    for (const cancel of documentPollersRef.current.values()) cancel();
    documentPollersRef.current.clear();
    if (!userId) return;
    let cancelled = false;
    const identityEpoch = identityEpochRef.current;
    void (async () => {
      try {
        const response = await getRequest<{ goals: GoalListItem[] }>("/api/goals");
        if (cancelled || identityEpochRef.current !== identityEpoch) return;
        const goal = response.goals[0];
        if (!goal) {
          setGoalBootstrap("no_goal");
          return;
        }
        const restoredState = await getRequest<StatePayload>(`/api/state/current?goal_id=${encodeURIComponent(goal.goal_id)}`);
        if (cancelled || identityEpochRef.current !== identityEpoch) return;
        setGoalId(goal.goal_id);
        setState(restoredState);
        setGoalBootstrap("loaded");
      } catch {
        if (!cancelled && identityEpochRef.current === identityEpoch) setGoalBootstrap("failed");
      }
    })();
    return () => { cancelled = true; };
  }, [userId]);

  useEffect(() => () => {
    identityEpochRef.current += 1;
    tutorRequestRef.current?.controller.abort();
    for (const cancel of documentPollersRef.current.values()) cancel();
    documentPollersRef.current.clear();
  }, []);

  useEffect(() => {
    tutorRequestRef.current?.controller.abort();
    if (!goalId) return;
    let cancelled = false;
    const identityEpoch = identityEpochRef.current;
    void (async () => {
      try {
        const listed = await getRequest<{ conversations: TutorConversation[] }>(
          `/api/tutor/conversations?goal_id=${encodeURIComponent(goalId)}`,
        );
        if (cancelled || identityEpochRef.current !== identityEpoch) return;
        let available = listed.conversations;
        if (available.length === 0) {
          const created = await postRequest<TutorConversation>(
            "/api/tutor/conversations",
            { goal_id: goalId, title: "Tutor session" },
          );
          if (cancelled || identityEpochRef.current !== identityEpoch) return;
          available = [created];
        }
        setConversations(available);
        activeConversationIdRef.current = available[0].thread_id;
        setActiveConversationId(available[0].thread_id);
      } catch (error) {
        if (!cancelled && identityEpochRef.current === identityEpoch) {
          notify(error instanceof Error ? error.message : "Unable to load tutor sessions.");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [goalId, notify]);

  const createConversation = useCallback(async () => {
    if (!goalId || activeRunId || busy.chat) return;
    const created = await postRequest<TutorConversation>(
      "/api/tutor/conversations",
      { goal_id: goalId, title: `Tutor session ${conversations.length + 1}` },
    );
    setConversations((current) => [created, ...current]);
    activeConversationIdRef.current = created.thread_id;
    setActiveConversationId(created.thread_id);
    setChat({ final_answer: "", citations: [] });
  }, [activeRunId, busy.chat, conversations.length, goalId]);

  const selectConversation = useCallback((threadId: string) => {
    if (activeRunId || busy.chat) return;
    activeConversationIdRef.current = threadId;
    setActiveConversationId(threadId);
    setChat({ final_answer: "", citations: [] });
  }, [activeRunId, busy.chat]);

  const deleteConversation = useCallback(async (threadId: string) => {
    if (!goalId || activeRunId || busy.chat) return;
    await deleteRequest<void>(
      `/api/tutor/conversations/${encodeURIComponent(threadId)}?goal_id=${encodeURIComponent(goalId)}`,
    );
    const remaining = conversations.filter((item) => item.thread_id !== threadId);
    if (remaining.length > 0) {
      setConversations(remaining);
      if (activeConversationId === threadId) {
        activeConversationIdRef.current = remaining[0].thread_id;
        setActiveConversationId(remaining[0].thread_id);
      }
      return;
    }
    const replacement = await postRequest<TutorConversation>(
      "/api/tutor/conversations",
      { goal_id: goalId, title: "Tutor session" },
    );
    setConversations([replacement]);
    activeConversationIdRef.current = replacement.thread_id;
    setActiveConversationId(replacement.thread_id);
  }, [activeConversationId, activeRunId, busy.chat, conversations, goalId]);

  const cancelTutor = useCallback(async () => {
    const request = tutorRequestRef.current;
    await cancelTutorRequest(request, (runId) =>
      postRequest<{ run_id: string; status: string }>(
        `/api/tutor/runs/${encodeURIComponent(runId)}/cancel`,
        {},
      ),
    );
  }, []);

  const runBusy = useCallback(
    async <T,>(
      key: BusyKey,
      action: (isCurrentIdentity: () => boolean) => Promise<T>,
      options: { queueIfBusy?: boolean } = {}
    ) => {
      const previousAction = busyActionsRef.current.get(key);
      if (previousAction && !options.queueIfBusy) return undefined;
      const identityEpoch = identityEpochRef.current;
      const isCurrentIdentity = () => identityEpochRef.current === identityEpoch;
      if (!busyKeysRef.current.has(key)) {
        busyKeysRef.current.add(key);
        setBusy((current) => ({ ...current, [key]: true }));
      }
      const execute = async (): Promise<T | undefined> => {
        if (!isCurrentIdentity()) return undefined;
        try {
          return await action(isCurrentIdentity);
        } catch (error) {
          if (isCurrentIdentity()) {
            notify(error instanceof Error ? error.message : "操作失败，请稍后再试。");
          }
          return undefined;
        }
      };
      const queuedAction = (previousAction?.catch(() => undefined) ?? Promise.resolve()).then(execute);
      busyActionsRef.current.set(key, queuedAction);
      try {
        return await queuedAction;
      } finally {
        if (busyActionsRef.current.get(key) === queuedAction) {
          busyActionsRef.current.delete(key);
          busyKeysRef.current.delete(key);
          setBusy((current) => ({ ...current, [key]: false }));
        }
      }
    },
    [notify]
  );

  const refreshState = useCallback(
    async (nextGoalId = goalId) => {
      if (!nextGoalId) {
        notify("还没有生成学习路径，先完成入学诊断。");
        return;
      }
      await runBusy("refresh", async (isCurrentIdentity) => {
        const payload = await getRequest<StatePayload>(`/api/state/current?goal_id=${encodeURIComponent(nextGoalId)}`);
        if (!isCurrentIdentity()) return;
        setState(payload);
        if (payload.latest_plan_adjustment) {
          setAdjustment(payload.latest_plan_adjustment);
        }
        notify("学习状态已刷新");
      }, { queueIfBusy: true });
    },
    [goalId, notify, runBusy]
  );

  const initializeOnboarding = useCallback(async (request: OnboardingInitializeRequest) => {
    const result = await runBusy("path", async (isCurrentIdentity) => {
      notify("正在提交诊断并生成学习路径");
      const initialized = await submitOnboarding(request);
      if (!isCurrentIdentity()) return false;
      setGoalId(initialized.goal.goal_id);
      setState(initialized.state);
      notify(
        `已生成路径：入口 ${initialized.diagnosis.entry_node_code}，计划版本 ${initialized.diagnosis.active_plan_version}`
      );
      router.push("/path");
      return true;
    });
    return result === true;
  }, [notify, router, runBusy]);

  const createLearningPath = useCallback(async () => {
    notify("请先完成真实入学诊断，再生成新的学习路径。");
    router.push("/diagnosis");
  }, [notify, router]);

  const askTutor = useCallback(
    async (event?: FormEvent, memoryDraft?: MemoryDeclarationDraft | null) => {
      event?.preventDefault();
      const trimmed = message.trim();
      if (!trimmed) {
        notify("请输入要追问讲师的问题。");
        return false;
      }
      const result = await runBusy("chat", async (isCurrentIdentity) => {
        notify("讲师正在检索资料并回答");
        if (!goalId) {
          setChat(demoChat);
          notify("已使用本地演示回答；生成学习路径后会调用后端讲师 API。");
          return true;
        }
        if (!activeConversationId) {
          notify("Tutor sessions are still loading. Please try again.");
          return false;
        }
        let memoryDeclaration: ReturnType<typeof memoryDeclarationRequest> | undefined;
        if (memoryDraft) {
          const fingerprint = memoryDeclarationFingerprint(memoryDraft);
          if (pendingMemoryRequestRef.current?.fingerprint !== fingerprint) {
            pendingMemoryRequestRef.current = {
              fingerprint,
              requestId: crypto.randomUUID(),
            };
          }
          memoryDeclaration = memoryDeclarationRequest(
            memoryDraft,
            pendingMemoryRequestRef.current.requestId,
          );
        }
        const controller = new AbortController();
        const requestContext: TutorStreamRequest = {
          requestId: crypto.randomUUID(),
          threadId: activeConversationId,
          runId: null,
          controller,
        };
        tutorRequestRef.current = requestContext;
        const isCurrentTutorRequest = () =>
          isCurrentIdentity()
          && isTutorStreamCurrent(
            tutorRequestRef.current,
            requestContext,
            activeConversationIdRef.current,
          );
        let completed = false;
        let cancelled = false;
        let terminalError = "";
        let canApplyTerminal = false;
        setChat({ final_answer: "", citations: [] });
        try {
          const response = await streamPostRequest(
            "/api/tutor/chat/stream",
            {
              goal_id: goalId,
              thread_id: activeConversationId,
              message: trimmed,
              ...(memoryDeclaration ? { memory_declaration: memoryDeclaration } : {})
            },
            controller.signal,
          );
          await consumeTutorEventStream(response, (streamEvent) => {
            if (!isCurrentTutorRequest()) return;
            if (streamEvent.type === "run.started") {
              const runId = streamEvent.data.run_id;
              if (typeof runId === "string") {
                requestContext.runId = runId;
                setActiveRunId(runId);
              }
            } else if (streamEvent.type === "teacher.delta") {
              const delta = streamEvent.data.delta;
              if (typeof delta === "string") {
                setChat((current) => ({ ...current, final_answer: current.final_answer + delta }));
              }
            } else if (streamEvent.type === "run.completed") {
              const resultPayload = streamEvent.data.result;
              if (resultPayload && typeof resultPayload === "object" && !Array.isArray(resultPayload)) {
                const value = resultPayload as Partial<ChatResponse>;
                setChat({
                  final_answer: typeof value.final_answer === "string" ? value.final_answer : "",
                  citations: Array.isArray(value.citations) ? value.citations : [],
                  runtime_metadata: value.runtime_metadata,
                });
                completed = true;
              }
            } else if (streamEvent.type === "run.failed") {
              terminalError = typeof streamEvent.data.message === "string"
                ? streamEvent.data.message
                : "The tutor run could not be completed.";
            } else if (streamEvent.type === "run.cancelled") {
              cancelled = true;
            }
          });
        } catch (error) {
          if (!controller.signal.aborted) throw error;
          cancelled = true;
        } finally {
          canApplyTerminal = isCurrentTutorRequest();
          if (tutorRequestRef.current === requestContext) {
            tutorRequestRef.current = null;
            setActiveRunId(null);
          }
        }
        if (!canApplyTerminal) return false;
        if (cancelled) {
          notify("Tutor response cancelled.");
          return false;
        }
        if (terminalError) throw new Error(terminalError);
        if (!completed) throw new Error("Tutor stream ended before completion.");
        if (memoryDeclaration) pendingMemoryRequestRef.current = null;
        notify("讲师回答已更新");
        return true;
      });
      return result === true;
    },
    [activeConversationId, goalId, message, notify, runBusy]
  );

  const createDailyAssessment = useCallback(async () => {
    if (!currentTask) {
      notify("当前没有可用于创建测验的学习任务。");
      return;
    }
    await runBusy("assessment", async (isCurrentIdentity) => {
      notify(`正在创建${assessmentMode === "daily" ? "日测" : assessmentMode === "weekly" ? "周测" : "阶段测"}`);
      const knowledgeNodeIds = [currentTask.knowledge_node_id];
      if (!goalId) {
        setAssessment({
          assessment_id: "demo-assessment",
          assessment_type: assessmentMode,
          status: "active",
          scope: { knowledge_node_ids: [currentTask.knowledge_node_id] },
          items: [
            {
              item_id: "demo-item-1",
              prompt: "解释模型选择时如何平衡成本、延迟和推理质量。",
              question_type: "explain",
              knowledge_node_id: currentTask.knowledge_node_id,
              options: [],
              difficulty: 2
            }
          ]
        });
        setAssessmentAnswers({});
        setAssessmentResult(null);
        notify("已创建本地演示测验");
        return;
      }
      if (!activeConversationId) {
        notify("Tutor sessions are still loading. Please try again.");
        return;
      }
      const payload =
        assessmentMode === "phase"
          ? await postRequest<AssessmentDraft>(
              "/api/assessments/phase",
              {
                goal_id: goalId,
                thread_id: activeConversationId,
                phase_code: "phase-ai-app-v1",
                knowledge_node_ids: knowledgeNodeIds
              }
            )
          : await postRequest<AssessmentDraft>(
              "/api/assessments",
              {
                goal_id: goalId,
                thread_id: activeConversationId,
                assessment_type: assessmentMode,
                knowledge_node_ids: knowledgeNodeIds
              }
            );
      if (!isCurrentIdentity()) return;
      setAssessment(payload);
      setAssessmentAnswers({});
      setAssessmentResult(null);
      notify("测验已创建");
    });
  }, [activeConversationId, assessmentMode, currentTask, goalId, notify, runBusy]);

  const submitAssessment = useCallback(async () => {
    if (!assessment) {
      notify("请先创建测验。");
      return;
    }
    const assessmentNodeId = currentTask?.knowledge_node_id || assessment.items[0]?.knowledge_node_id;
    if (!assessmentNodeId) {
      notify("测验缺少可关联的知识节点。");
      return;
    }
    await runBusy("submitAssessment", async (isCurrentIdentity) => {
      notify("正在提交测验");
      const answers = Object.fromEntries(
        assessment.items.map((item) => [
          item.item_id,
          assessmentAnswers[item.item_id]?.trim() ?? ""
        ])
      );
      if (!goalId) {
        setAssessmentResult({
          score: 60,
          feedback: "还需要补充模型降级策略和缓存策略。",
          mastery_updates: [{ knowledge_node_id: assessmentNodeId, previous_score: 42, new_score: 56 }],
          answers: [
            { item_id: assessment.items[0].item_id, score: 60, evidence_json: { wrong_reason_tags: ["missing_tradeoff"] } }
          ]
        });
        notify("已提交本地演示测验");
        return;
      }
      const assessmentRequestId = crypto.randomUUID();
      const payload = await postRequest<AssessmentResult>(
        `/api/assessments/${assessment.assessment_id}/submit`,
        { request_id: assessmentRequestId, answers }
      );
      if (!isCurrentIdentity()) return;
      setAssessmentResult(payload);
      await refreshState(goalId);
      if (!isCurrentIdentity()) return;
      notify("测验反馈已生成");
    });
  }, [assessment, assessmentAnswers, currentTask, goalId, notify, refreshState, runBusy]);

  const requestPlanAdjustment = useCallback(async () => {
    const trimmed = adjustmentMessage.trim();
    if (!trimmed) {
      notify("请输入计划调整原因。");
      return;
    }
    await runBusy("replan", async (isCurrentIdentity) => {
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
      if (!activeConversationId) {
        notify("Tutor sessions are still loading. Please try again.");
        return;
      }
      const payload = await postRequest<PlanAdjustment>(
        "/api/plans/replan",
        {
          goal_id: goalId,
          thread_id: activeConversationId,
          message: trimmed
        }
      );
      if (!isCurrentIdentity()) return;
      setAdjustment(payload);
      await refreshState(goalId);
      if (!isCurrentIdentity()) return;
      notify("计划调整已生成");
    });
  }, [activeConversationId, adjustmentMessage, goalId, notify, refreshState, runBusy]);

  const applyPlanAdjustment = useCallback(async () => {
    if (!adjustment) {
      notify("还没有可应用的计划调整。");
      return;
    }
    await runBusy("applyAdjustment", async (isCurrentIdentity) => {
      notify("正在应用计划调整");
      if (!goalId) {
        setAdjustment((current) => (current ? { ...current, status: "applied", new_plan_id: "demo-plan-v2" } : current));
        notify("已应用本地演示调整");
        return;
      }
      const payload = await postRequest<PlanAdjustment>(
        `/api/plans/adjustments/${adjustment.adjustment_id}/apply`,
        { goal_id: goalId }
      );
      if (!isCurrentIdentity()) return;
      setAdjustment(payload);
      await refreshState(goalId);
      if (!isCurrentIdentity()) return;
      notify("计划调整已应用");
    });
  }, [adjustment, goalId, notify, refreshState, runBusy]);

  const fetchDocuments = useCallback(async () => {
    await runBusy("document", async (isCurrentIdentity) => {
      const payload = await listDocuments();
      if (!isCurrentIdentity()) return;
      setDocuments(payload.documents);
      notify("资料列表已刷新");
    });
  }, [notify, runBusy]);

  const startDocumentPolling = useCallback((documentId: string, isCurrentIdentity: () => boolean) => {
    if (documentPollersRef.current.has(documentId)) return;
    const cancel = pollDocument(
      documentId,
      getDocument,
      (document) => {
        if (!isCurrentIdentity()) return;
        setDocuments((current) => current.map((item) => (item.id === document.id ? { ...item, ...document } : item)));
        if (document.parse_status === "success") notify("资料解析完成");
        if (document.parse_status === "failed") notify(document.parse_error || "资料解析失败");
        if (document.parse_status === "success" || document.parse_status === "failed") documentPollersRef.current.delete(documentId);
      },
      () => {
        if (isCurrentIdentity()) notify("资料处理尚未完成，请稍后刷新。");
        documentPollersRef.current.delete(documentId);
      }
    );
    documentPollersRef.current.set(documentId, cancel);
  }, [notify]);

  const refreshDocument = useCallback(async (documentId: string) => {
    await runBusy("document", async (isCurrentIdentity) => {
      const payload = await getDocument(documentId);
      if (!isCurrentIdentity()) return;
      setDocuments((current) => current.map((item) => (item.id === payload.id ? payload : item)));
      if (["pending", "processing"].includes(payload.parse_status)) {
        startDocumentPolling(documentId, isCurrentIdentity);
      }
    });
  }, [runBusy, startDocumentPolling]);

  const uploadFile = useCallback(async (file: File): Promise<boolean> => {
    const uploaded = await runBusy("fileUpload", async (isCurrentIdentity) => {
      notify("正在上传文件");
      const payload = await uploadDocumentFile(file);
      if (!isCurrentIdentity()) return false;
      setDocuments((current) => [payload, ...current.filter((item) => item.id !== payload.id)]);
      if (["pending", "processing"].includes(payload.parse_status)) {
        startDocumentPolling(payload.id, isCurrentIdentity);
      }
      notify("文件已上传，正在等待处理");
      return true;
    });
    return uploaded === true;
  }, [notify, runBusy, startDocumentPolling]);

  const saveNote = useCallback(async () => {
    const content = note.trim();
    if (!content) {
      notify("先写一点学习笔记，再保存为资料。");
      return;
    }
    await runBusy("document", async (isCurrentIdentity) => {
      notify("正在保存笔记并登记资料");
      const payload = await saveMarkdownNote(content);
      if (!isCurrentIdentity()) return;
      setDocuments((current) => [payload, ...current.filter((item) => item.id !== payload.id)]);
      if (["pending", "processing"].includes(payload.parse_status)) startDocumentPolling(payload.id, isCurrentIdentity);
      setNote((current) => (current.trim() === content ? "" : current));
      notify("学习笔记已保存为资料");
    });
  }, [note, notify, runBusy, startDocumentPolling]);

  const searchOfficialSources = useCallback(async () => {
    const query = sourceQuery.trim();
    if (!query) {
      notify("请输入要搜索的官方资料主题。");
      return;
    }
    await runBusy("sources", async (isCurrentIdentity) => {
      notify("正在检索官方来源");
      const payload = await postRequest<{ results: SourceResult[] }>(
        "/api/tools/search-official-learning-sources",
        {
          query,
          domains: ["fastapi.tiangolo.com", "docs.python.org", "platform.openai.com"]
        }
      );
      if (!isCurrentIdentity()) return;
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
    async (task?: Task | null) => {
      if (!task) {
        notify("当前没有可开始的任务。");
        return;
      }
      await runBusy("startTask", async (isCurrentIdentity) => {
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
        await postRequest<TaskSessionResponse>(`/api/tasks/${task.id}/start`, {});
        if (!isCurrentIdentity()) return;
        await refreshState(goalId);
        if (!isCurrentIdentity()) return;
        notify(`已进入任务：${task.title}`);
        router.push(`/tutor?task=${encodeURIComponent(task.id)}`);
      });
    },
    [goalId, notify, refreshState, router, runBusy]
  );

  const completeTask = useCallback(
    async (task?: Task) => {
      if (!task) {
        notify("当前没有可完成的任务。");
        return;
      }
      await runBusy("completeTask", async (isCurrentIdentity) => {
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
          }
        );
        if (!isCurrentIdentity()) return;
        if (payload.plan_adjustment) {
          setAdjustment(payload.plan_adjustment);
        }
        await refreshState(goalId);
        if (!isCurrentIdentity()) return;
        notify(payload.plan_adjustment ? "任务已完成，并生成待确认调整" : `已完成任务：${task.title}`);
      });
    },
    [goalId, notify, refreshState, runBusy]
  );

  const value = useMemo<LearningContextValue>(
    () => ({
      goalId,
      isDemoMode,
      state,
      currentTask,
      masteryRows: masteryRows.map(([name, item]) => [formatMasteryName(name), item]),
      message,
      setMessage,
      chat,
      conversations,
      activeConversationId,
      activeRunId,
      createConversation,
      selectConversation,
      deleteConversation,
      cancelTutor,
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
      initializeOnboarding,
      createLearningPath,
      askTutor,
      createDailyAssessment,
      submitAssessment,
      requestPlanAdjustment,
      applyPlanAdjustment,
      uploadFile,
      saveNote,
      fetchDocuments,
      refreshDocument,
      searchOfficialSources,
      startTask,
      completeTask,
      notify
    }),
    [
      goalId,
      isDemoMode,
      state,
      currentTask,
      masteryRows,
      message,
      chat,
      conversations,
      activeConversationId,
      activeRunId,
      createConversation,
      selectConversation,
      deleteConversation,
      cancelTutor,
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
      initializeOnboarding,
      createLearningPath,
      askTutor,
      createDailyAssessment,
      submitAssessment,
      requestPlanAdjustment,
      applyPlanAdjustment,
      uploadFile,
      saveNote,
      fetchDocuments,
      refreshDocument,
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
