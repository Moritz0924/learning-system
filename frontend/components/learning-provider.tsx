"use client";

import { createContext, FormEvent, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, deleteRequest, getRequest, postRequest, putRequest, streamPostRequest } from "@/lib/api";
import {
  cancelTutorRequest,
  consumeTutorEventStream,
  isTutorStreamCurrent,
  reduceTutorRunView,
  startTutorRunView,
  tutorRequestFailureCode,
  mergeTutorTranscript,
} from "@/lib/tutor-stream.mjs";
import type { TutorRunPhase, TutorRunView, TutorStreamRequest } from "@/lib/tutor-stream.mjs";
import { useAuth } from "@/components/providers/auth-provider";
import { useLocale } from "@/components/providers/locale-provider";
import { translate } from "@/lib/i18n.mjs";
import {
  getDocument,
  listDocuments,
  saveMarkdownNote,
  uploadDocumentFile,
} from "@/features/documents/document-api";
import type { DocumentRecord } from "@/features/documents/types";
import { initializeFromDraft } from "@/features/onboarding/onboarding-api";
import type { InitializeFromDraftRequest } from "@/features/onboarding/types";
import {
  memoryDeclarationFingerprint,
  memoryDeclarationRequest,
} from "@/features/memory/types";
import type { MemoryDeclarationDraft } from "@/features/memory/types";
import { listSkills, listToolApprovals } from "@/features/ai-config/ai-config-api";
import type { PromptSkill, ToolApproval } from "@/features/ai-config/types";
import { pollDocument } from "@/lib/document-poller";
import {
  AssessmentDraft,
  AssessmentResult,
  ChatResponse,
  fallbackState,
  GoalListItem,
  PlanAdjustment,
  ResourceRow,
  SourceResult,
  StatePayload,
  Task,
  TutorConversation,
  TutorTranscriptMessage,
  TutorTranscriptResponse,
} from "@/lib/learning-data";
import { localizeDemoTask } from "@/lib/learning-data";

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
  goalBootstrap: "bootstrapping" | "loaded" | "no_goal" | "failed";
  isDemoMode: boolean;
  retryGoalBootstrap: () => Promise<void>;
  state: StatePayload;
  currentTask: Task | null;
  masteryRows: StatePayload["mastery_summary"];
  message: string;
  setMessage: (value: string) => void;
  chat: ChatResponse;
  conversations: TutorConversation[];
  activeConversationId: string;
  activeRunId: string | null;
  transcript: TutorTranscriptMessage[];
  transcriptLoading: boolean;
  transcriptNextBefore: string | null;
  loadOlderTranscript: () => Promise<void>;
  tutorRunPhase: TutorRunPhase;
  currentTutorQuestion: string;
  tutorErrorCode: string;
  retryTutor: () => Promise<boolean>;
  skills: PromptSkill[];
  selectedSkillIds: string[];
  setSelectedSkillIds: (value: string[]) => void;
  toolApprovals: ToolApproval[];
  decideToolApproval: (approval: ToolApproval, decision: "approve" | "reject") => Promise<void>;
  submitTutorFeedback: (helpful: boolean) => Promise<void>;
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
  sourceSearchErrorCode: string;
  note: string;
  setNote: (value: string) => void;
  status: string;
  toast: string;
  dismissToast: () => void;
  busy: Record<string, boolean>;
  savedNodes: Set<string>;
  toggleSavedNode: (nodeId: string) => Promise<void>;
  resourceModal: ResourceRow | null;
  openResource: (resource: ResourceRow) => void;
  closeResource: () => void;
  copyResource: (resource: ResourceRow) => Promise<void>;
  refreshState: (nextGoalId?: string) => Promise<void>;
  initializeOnboarding: (request: InitializeFromDraftRequest) => Promise<boolean>;
  createLearningPath: () => Promise<void>;
  askTutor: (event?: FormEvent, memoryDraft?: MemoryDeclarationDraft | null, taskId?: string | null) => Promise<boolean>;
  createDailyAssessment: () => Promise<void>;
  submitAssessment: () => Promise<void>;
  requestPlanAdjustment: () => Promise<void>;
  applyPlanAdjustment: () => Promise<void>;
  uploadFile: (file: File) => Promise<boolean>;
  saveNote: () => Promise<void>;
  fetchDocuments: () => Promise<void>;
  refreshDocument: (documentId: string) => Promise<void>;
  searchOfficialSources: (query?: string) => Promise<void>;
  startTask: (task?: Task | null) => Promise<void>;
  completeTask: (task?: Task) => Promise<void>;
  notify: (message: string) => void;
};

const LearningContext = createContext<LearningContextValue | null>(null);
type Translate = (key: string, values?: Record<string, string | number>) => string;
type TutorAttemptSnapshot = {
  question: string;
  skillIds: string[];
  taskId?: string;
  memoryDeclaration?: ReturnType<typeof memoryDeclarationRequest>;
};

const EMPTY_TUTOR_RUN_VIEW: TutorRunView = {
  phase: "idle",
  currentQuestion: "",
  errorCode: "",
  draftAnswer: "",
};

function buildDemoChat(t: Translate): ChatResponse {
  return {
  final_answer: t("demo.chatAnswer"),
  runtime_metadata: {
    llm: { mode: "demo", is_remote: false, model: "frontend-demo" },
    rag: { mode: "demo", citation_count: 1, fallback_citations: true }
  },
  citations: [
    {
      citation_label: t("demo.chatCitation"),
      source_title: t("demo.chatSource"),
      source_url: "https://docs.langchain.com/oss/python/langchain/rag"
    }
  ]
  };
}

const isLocalizedDefault = (value: string, key: string) =>
  value === translate("zh-CN", key) || value === translate("en-US", key);

function translateApiError(t: Translate, error: unknown) {
  if (error instanceof ApiError && error.code === "document.unsupported_media_type") {
    return t("document.unsupported");
  }
  return t("provider.actionFailed");
}

export function LearningProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();

  return (
    <IdentityScopedLearningProvider key={user?.id ?? "anonymous"} userId={user?.id}>
      {children}
    </IdentityScopedLearningProvider>
  );
}

function IdentityScopedLearningProvider({ children, userId }: { children: ReactNode; userId?: string }) {
  const { locale, t } = useLocale();
  const router = useRouter();
  const [goalId, setGoalId] = useState("");
  const [goalBootstrap, setGoalBootstrap] = useState<"bootstrapping" | "loaded" | "no_goal" | "failed">("bootstrapping");
  const [goalBootstrapAttempt, setGoalBootstrapAttempt] = useState(0);
  const [state, setState] = useState<StatePayload>(fallbackState);
  const [message, setMessage] = useState(() => t("demo.defaultTutorQuestion"));
  const [chat, setChat] = useState<ChatResponse>(() => buildDemoChat(t));
  const [conversations, setConversations] = useState<TutorConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState("");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [currentTutorRunId, setCurrentTutorRunId] = useState<string | null>(null);
  const [tutorRunView, setTutorRunView] = useState<TutorRunView>(EMPTY_TUTOR_RUN_VIEW);
  const [transcript, setTranscript] = useState<TutorTranscriptMessage[]>([]);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptNextBefore, setTranscriptNextBefore] = useState<string | null>(null);
  const [skills, setSkills] = useState<PromptSkill[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [toolApprovals, setToolApprovals] = useState<ToolApproval[]>([]);
  const [lastCompletedRunId, setLastCompletedRunId] = useState<string | null>(null);
  const [assessmentMode, setAssessmentMode] = useState<"daily" | "weekly" | "phase">("daily");
  const [assessment, setAssessment] = useState<AssessmentDraft | null>(null);
  const [assessmentAnswers, setAssessmentAnswers] = useState<Record<string, string>>({});
  const [assessmentResult, setAssessmentResult] = useState<AssessmentResult | null>(null);
  const [adjustment, setAdjustment] = useState<PlanAdjustment | null>(null);
  const [adjustmentMessage, setAdjustmentMessage] = useState(() => t("demo.defaultAdjustment"));
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [sourceQuery, setSourceQuery] = useState(() => t("demo.defaultSourceQuery"));
  const [sourceResults, setSourceResults] = useState<SourceResult[]>([]);
  const [sourceSearchErrorCode, setSourceSearchErrorCode] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState(() => t("provider.waitingPath"));
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [savedNodes, setSavedNodes] = useState<Set<string>>(() => new Set());
  const [resourceModal, setResourceModal] = useState<ResourceRow | null>(null);
  const identityEpochRef = useRef(0);
  const documentPollersRef = useRef(new Map<string, () => void>());
  const busyKeysRef = useRef(new Set<BusyKey>());
  const busyActionsRef = useRef(new Map<BusyKey, Promise<unknown>>());
  const pendingMemoryRequestRef = useRef<{ fingerprint: string; requestId: string } | null>(null);
  const pendingAssessmentCreationRef = useRef<{ fingerprint: string; requestId: string } | null>(null);
  const pendingAssessmentSubmissionRef = useRef<{ fingerprint: string; requestId: string } | null>(null);
  const activeConversationIdRef = useRef("");
  const tutorRequestRef = useRef<TutorStreamRequest | null>(null);
  const lastTutorAttemptRef = useRef<TutorAttemptSnapshot | null>(null);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setMessage((current) => isLocalizedDefault(current, "demo.defaultTutorQuestion") ? t("demo.defaultTutorQuestion") : current);
      setAdjustmentMessage((current) => isLocalizedDefault(current, "demo.defaultAdjustment") ? t("demo.defaultAdjustment") : current);
      setSourceQuery((current) => isLocalizedDefault(current, "demo.defaultSourceQuery") ? t("demo.defaultSourceQuery") : current);
      setStatus((current) => isLocalizedDefault(current, "provider.waitingPath") ? t("provider.waitingPath") : current);
      setChat((current) => current.runtime_metadata?.llm?.mode === "demo" ? buildDemoChat(t) : current);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [t]);

  const currentTask = useMemo(
    () => state.today_tasks.find((task) => !["done", "completed"].includes(task.status)) || state.today_tasks[0] || null,
    [state.today_tasks]
  );
  const assessmentStage = useMemo(() => {
    if (!currentTask || !state.roadmap) return null;
    return state.roadmap.stages.find((stage) =>
      stage.nodes.some((node) =>
        node.task_id === currentTask.id || node.knowledge_node_id === currentTask.knowledge_node_id
      )
    ) ?? state.roadmap.stages.find((stage) => stage.status === "current") ?? null;
  }, [currentTask, state.roadmap]);
  const isDemoMode = goalBootstrap === "no_goal";
  const masteryRows = useMemo(() => state.mastery_summary.slice(0, 8), [state.mastery_summary]);

  const notify = useCallback((nextStatus: string) => {
    setStatus(nextStatus);
    setToast(nextStatus);
  }, []);

  const retryGoalBootstrap = useCallback(async () => {
    setGoalBootstrap("bootstrapping");
    setGoalBootstrapAttempt((attempt) => attempt + 1);
  }, []);

  useEffect(() => {
    identityEpochRef.current += 1;
    setSavedNodes(new Set());
    pendingAssessmentCreationRef.current = null;
    pendingAssessmentSubmissionRef.current = null;
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
        setAdjustment(restoredState.latest_plan_adjustment ?? null);
        setChat({ final_answer: "", citations: [] });
        setTutorRunView(EMPTY_TUTOR_RUN_VIEW);
        setGoalBootstrap("loaded");
      } catch {
        if (!cancelled && identityEpochRef.current === identityEpoch) setGoalBootstrap("failed");
      }
    })();
    return () => { cancelled = true; };
  }, [goalBootstrapAttempt, userId]);

  useEffect(() => {
    setSavedNodes(new Set());
    if (!goalId) return;
    let cancelled = false;
    const identityEpoch = identityEpochRef.current;
    void getRequest<{ knowledge_node_ids: string[] }>(
      `/api/saved-learning-nodes?goal_id=${encodeURIComponent(goalId)}`,
    )
      .then((payload) => {
        if (cancelled || identityEpochRef.current !== identityEpoch) return;
        setSavedNodes(new Set(payload.knowledge_node_ids));
      })
      .catch(() => {
        if (!cancelled && identityEpochRef.current === identityEpoch) {
          notify(t("provider.savedLoadFailed"));
        }
      });
    return () => { cancelled = true; };
  }, [goalId, notify, t]);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    void listSkills()
      .then(({ skills: configured }) => {
        if (cancelled) return;
        const enabled = configured.filter((skill) => skill.enabled);
        setSkills(enabled);
        setSelectedSkillIds(enabled.filter((skill) => skill.default_enabled).map((skill) => skill.id));
      })
      .catch(() => {
        if (!cancelled) {
          setSkills([]);
          setSelectedSkillIds([]);
        }
      });
    return () => { cancelled = true; };
  }, [userId]);

  useEffect(() => () => {
    identityEpochRef.current += 1;
    tutorRequestRef.current?.controller.abort();
    pendingAssessmentCreationRef.current = null;
    pendingAssessmentSubmissionRef.current = null;
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
            { goal_id: goalId, title: t("tutor.session") },
          );
          if (cancelled || identityEpochRef.current !== identityEpoch) return;
          available = [created];
        }
        setConversations(available);
        activeConversationIdRef.current = available[0].thread_id;
        setActiveConversationId(available[0].thread_id);
      } catch (error) {
        if (!cancelled && identityEpochRef.current === identityEpoch) {
          notify(t("provider.sessionsLoadFailed"));
        }
      }
    })();
    return () => { cancelled = true; };
  }, [goalId, notify, t]);

  useEffect(() => {
    if (!activeConversationId) return;
    let cancelled = false;
    void listToolApprovals(activeConversationId)
      .then(({ approvals }) => {
        if (cancelled) return;
        setToolApprovals(approvals);
        const pending = approvals.find((approval) => approval.status === "pending" || approval.status === "executing");
        if (pending) {
          setActiveRunId(pending.run_id);
          setTutorRunView((current) => ({ ...current, phase: "awaiting_approval", errorCode: "" }));
        }
      })
      .catch((error) => {
        if (!cancelled) notify(t("provider.approvalsRestoreFailed"));
      });
    return () => { cancelled = true; };
  }, [activeConversationId, notify, t]);

  useEffect(() => {
    if (!goalId || !activeConversationId) return;
    const threadId = activeConversationId;
    let cancelled = false;
    setTranscript([]);
    setTranscriptNextBefore(null);
    setTranscriptLoading(true);
    void getRequest<TutorTranscriptResponse>(
      `/api/tutor/conversations/${encodeURIComponent(threadId)}/messages?goal_id=${encodeURIComponent(goalId)}`,
    )
      .then((payload) => {
        if (cancelled || activeConversationIdRef.current !== threadId) return;
        setTranscript(payload.messages);
        setTranscriptNextBefore(payload.next_before);
      })
      .catch(() => {
        if (!cancelled && activeConversationIdRef.current === threadId) {
          notify(t("provider.transcriptLoadFailed"));
        }
      })
      .finally(() => {
        if (!cancelled && activeConversationIdRef.current === threadId) {
          setTranscriptLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [activeConversationId, goalId, notify, t]);

  const loadOlderTranscript = useCallback(async () => {
    const threadId = activeConversationId;
    const before = transcriptNextBefore;
    const identityEpoch = identityEpochRef.current;
    if (!goalId || !threadId || !before || transcriptLoading) return;
    setTranscriptLoading(true);
    try {
      const payload = await getRequest<TutorTranscriptResponse>(
        `/api/tutor/conversations/${encodeURIComponent(threadId)}/messages?goal_id=${encodeURIComponent(goalId)}&before=${encodeURIComponent(before)}`,
      );
      if (
        identityEpochRef.current !== identityEpoch
        || activeConversationIdRef.current !== threadId
      ) return;
      setTranscript((current) => mergeTutorTranscript(current, payload.messages));
      setTranscriptNextBefore(payload.next_before);
    } catch {
      if (
        identityEpochRef.current === identityEpoch
        && activeConversationIdRef.current === threadId
      ) notify(t("provider.transcriptLoadFailed"));
    } finally {
      if (
        identityEpochRef.current === identityEpoch
        && activeConversationIdRef.current === threadId
      ) setTranscriptLoading(false);
    }
  }, [activeConversationId, goalId, notify, t, transcriptLoading, transcriptNextBefore]);

  const resetTutorConversationView = useCallback(() => {
    setChat({ final_answer: "", citations: [] });
    setTutorRunView(EMPTY_TUTOR_RUN_VIEW);
    setToolApprovals([]);
    setActiveRunId(null);
    setCurrentTutorRunId(null);
    setLastCompletedRunId(null);
    setTranscript([]);
    setTranscriptLoading(false);
    setTranscriptNextBefore(null);
    lastTutorAttemptRef.current = null;
  }, []);

  const createConversation = useCallback(async () => {
    if (!goalId || activeRunId || busy.chat) return;
    const created = await postRequest<TutorConversation>(
      "/api/tutor/conversations",
      { goal_id: goalId, title: `${t("tutor.session")} ${conversations.length + 1}` },
    );
    setConversations((current) => [created, ...current]);
    resetTutorConversationView();
    activeConversationIdRef.current = created.thread_id;
    setActiveConversationId(created.thread_id);
  }, [activeRunId, busy.chat, conversations.length, goalId, resetTutorConversationView, t]);

  const selectConversation = useCallback((threadId: string) => {
    if (activeRunId || busy.chat) return;
    resetTutorConversationView();
    activeConversationIdRef.current = threadId;
    setActiveConversationId(threadId);
  }, [activeRunId, busy.chat, resetTutorConversationView]);

  const deleteConversation = useCallback(async (threadId: string) => {
    if (!goalId || activeRunId || busy.chat) return;
    await deleteRequest<void>(
      `/api/tutor/conversations/${encodeURIComponent(threadId)}?goal_id=${encodeURIComponent(goalId)}`,
    );
    const remaining = conversations.filter((item) => item.thread_id !== threadId);
    if (remaining.length > 0) {
      setConversations(remaining);
      if (activeConversationId === threadId) {
        resetTutorConversationView();
        activeConversationIdRef.current = remaining[0].thread_id;
        setActiveConversationId(remaining[0].thread_id);
      }
      return;
    }
    resetTutorConversationView();
    const replacement = await postRequest<TutorConversation>(
      "/api/tutor/conversations",
      { goal_id: goalId, title: t("tutor.session") },
    );
    setConversations([replacement]);
    activeConversationIdRef.current = replacement.thread_id;
    setActiveConversationId(replacement.thread_id);
  }, [activeConversationId, activeRunId, busy.chat, conversations, goalId, resetTutorConversationView, t]);

  const cancelTutor = useCallback(async () => {
    const request = tutorRequestRef.current;
    const cancel = (runId: string) => postRequest<{ run_id: string; status: string }>(
      `/api/tutor/runs/${encodeURIComponent(runId)}/cancel`,
      {},
    );
    if (request) await cancelTutorRequest(request, cancel);
    else if (activeRunId) await cancel(activeRunId);
    setActiveRunId(null);
    setTutorRunView((current) => ({ ...current, phase: "cancelled", errorCode: "" }));
    setToolApprovals((current) => current.filter((approval) => approval.run_id !== activeRunId));
  }, [activeRunId]);

  const runBusy = useCallback(
    async <T,>(
      key: BusyKey,
      action: (isCurrentIdentity: () => boolean) => Promise<T>,
      options: { queueIfBusy?: boolean; rethrow?: boolean } = {}
    ) => {
      if (
        goalBootstrap === "failed" &&
        ["startTask", "completeTask", "chat", "assessment", "submitAssessment", "replan", "applyAdjustment"].includes(key)
      ) {
        notify(t("provider.runFailed"));
        return undefined;
      }
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
          if (options.rethrow) throw error;
          if (isCurrentIdentity()) {
            notify(translateApiError(t, error));
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
    [goalBootstrap, notify, t]
  );

  const refreshState = useCallback(
    async (nextGoalId = goalId) => {
      if (!nextGoalId) {
        notify(t("provider.noLearningPath"));
        return;
      }
      await runBusy("refresh", async (isCurrentIdentity) => {
        const payload = await getRequest<StatePayload>(`/api/state/current?goal_id=${encodeURIComponent(nextGoalId)}`);
        if (!isCurrentIdentity()) return;
        setState(payload);
        if (payload.latest_plan_adjustment) {
          setAdjustment(payload.latest_plan_adjustment);
        }
        notify(t("provider.stateRefreshed"));
      }, { queueIfBusy: true });
    },
    [goalId, notify, runBusy, t]
  );

  const initializeOnboarding = useCallback(async (request: InitializeFromDraftRequest) => {
    const result = await runBusy("path", async (isCurrentIdentity) => {
      notify(t("provider.onboardingSubmitting"));
      const initialized = await initializeFromDraft(request);
      if (!isCurrentIdentity()) return false;
      setGoalId(initialized.goal.goal_id);
      setGoalBootstrap("loaded");
      setState(initialized.state);
      setAdjustment(initialized.state.latest_plan_adjustment ?? null);
      setChat({ final_answer: "", citations: [] });
      setTutorRunView(EMPTY_TUTOR_RUN_VIEW);
      notify(
        t("provider.pathGenerated", { entry: initialized.diagnosis.entry_node_code, version: initialized.diagnosis.active_plan_version })
      );
      router.push("/path");
      return true;
    }, { rethrow: true });
    return result === true;
  }, [notify, router, runBusy, t]);

  const createLearningPath = useCallback(async () => {
    notify(t("provider.completeDiagnosticFirst"));
    router.push("/diagnosis");
  }, [notify, router, t]);

  const askTutor = useCallback(
    async (
      event?: FormEvent,
      memoryDraft?: MemoryDeclarationDraft | null,
      taskId?: string | null,
      retryAttempt?: TutorAttemptSnapshot,
    ) => {
      event?.preventDefault();
      const trimmed = (retryAttempt?.question ?? message).trim();
      if (!trimmed) {
        notify(t("provider.questionRequired"));
        return false;
      }
      if (activeRunId) {
        notify(t("provider.finishActiveRun"));
        return false;
      }

      let memoryDeclaration = retryAttempt?.memoryDeclaration;
      if (!retryAttempt && memoryDraft) {
        const fingerprint = memoryDeclarationFingerprint(memoryDraft);
        if (pendingMemoryRequestRef.current?.fingerprint !== fingerprint) {
          pendingMemoryRequestRef.current = { fingerprint, requestId: crypto.randomUUID() };
        }
        memoryDeclaration = memoryDeclarationRequest(memoryDraft, pendingMemoryRequestRef.current.requestId);
      }
      const attempt: TutorAttemptSnapshot = retryAttempt ?? {
        question: trimmed,
        skillIds: [...selectedSkillIds],
        ...(taskId ? { taskId } : {}),
        ...(memoryDeclaration ? { memoryDeclaration } : {}),
      };
      lastTutorAttemptRef.current = attempt;
      setTutorRunView(startTutorRunView(trimmed));
      setChat({ final_answer: "", citations: [] });

      const result = await runBusy("chat", async (isCurrentIdentity) => {
        notify(t("provider.tutorAnswering"));
        if (!goalId) {
          const demo = buildDemoChat(t);
          setChat(demo);
          setTutorRunView((current) => ({ ...current, phase: "completed", draftAnswer: demo.final_answer }));
          notify(t("provider.demoAnswer"));
          return true;
        }
        if (!activeConversationId) {
          setTutorRunView((current) => ({ ...current, phase: "failed", errorCode: "tutor.session_unavailable" }));
          notify(t("provider.sessionsLoading"));
          return false;
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
          && isTutorStreamCurrent(tutorRequestRef.current, requestContext, activeConversationIdRef.current);
        let completed = false;
        let cancelled = false;
        let awaitingApproval = false;
        let terminalError = false;
        let canApplyTerminal = false;
        try {
          const response = await streamPostRequest(
            "/api/tutor/chat/stream",
            {
              goal_id: goalId,
              thread_id: activeConversationId,
              ...(attempt.taskId ? { task_id: attempt.taskId } : {}),
              message: attempt.question,
              locale,
              skill_ids: attempt.skillIds,
              ...(attempt.memoryDeclaration ? { memory_declaration: attempt.memoryDeclaration } : {}),
            },
            controller.signal,
          );
          await consumeTutorEventStream(response, (streamEvent) => {
            if (!isCurrentTutorRequest()) return;
            setTutorRunView((current) => reduceTutorRunView(current, streamEvent));
            if (streamEvent.type === "run.started") {
              const runId = streamEvent.data.run_id;
              if (typeof runId === "string") {
                requestContext.runId = runId;
                setCurrentTutorRunId(runId);
                setActiveRunId(runId);
                setTranscript((current) => mergeTutorTranscript(current, [{
                  id: `${runId}:user`,
                  run_id: runId,
                  role: "user",
                  content: attempt.question,
                  created_at: new Date().toISOString(),
                }]));
              }
            } else if (streamEvent.type === "teacher.delta") {
              const delta = streamEvent.data.delta;
              if (typeof delta === "string") {
                setChat((current) => ({ ...current, final_answer: current.final_answer + delta }));
              }
            } else if (streamEvent.type === "run.completed") {
              if (typeof requestContext.runId === "string") setLastCompletedRunId(requestContext.runId);
              const resultPayload = streamEvent.data.result;
              if (resultPayload && typeof resultPayload === "object" && !Array.isArray(resultPayload)) {
                const value = resultPayload as Partial<ChatResponse>;
                setChat({
                  final_answer: typeof value.final_answer === "string" ? value.final_answer : "",
                  citations: Array.isArray(value.citations) ? value.citations : [],
                  grounding_status: typeof value.grounding_status === "string" ? value.grounding_status : null,
                  insufficient_evidence: value.insufficient_evidence === true,
                  missing_information: Array.isArray(value.missing_information) ? value.missing_information : [],
                  runtime_metadata: value.runtime_metadata,
                });
                if (typeof requestContext.runId === "string") {
                  const runId = requestContext.runId;
                  setTranscript((current) => mergeTutorTranscript(current, [{
                    id: `${runId}:assistant`,
                    run_id: runId,
                    role: "assistant",
                    content: typeof value.final_answer === "string" ? value.final_answer : "",
                    created_at: new Date().toISOString(),
                    citations: Array.isArray(value.citations) ? value.citations : [],
                    grounding_status: typeof value.grounding_status === "string" ? value.grounding_status : null,
                  }]));
                }
                completed = true;
              }
            } else if (streamEvent.type === "run.failed") {
              setChat({ final_answer: "", citations: [] });
              terminalError = true;
            } else if (streamEvent.type === "run.cancelled") {
              cancelled = true;
            } else if (streamEvent.type === "tool.approval_required") {
              const approval = streamEvent.data as unknown as ToolApproval;
              if (typeof approval.approval_id === "string" && typeof approval.run_id === "string") {
                awaitingApproval = true;
                requestContext.runId = approval.run_id;
                setCurrentTutorRunId(approval.run_id);
                setActiveRunId(approval.run_id);
                setToolApprovals((current) => [
                  ...current.filter((item) => item.approval_id !== approval.approval_id),
                  approval,
                ]);
              }
            } else if (streamEvent.type === "run.awaiting_approval") {
              awaitingApproval = true;
            }
          });
        } catch (error) {
          if (!controller.signal.aborted) {
            setTutorRunView((current) => reduceTutorRunView(current, {
              type: "run.failed",
              data: {
                code: tutorRequestFailureCode(error instanceof ApiError ? error.code : undefined),
              },
            }));
            throw error;
          }
          cancelled = true;
          setTutorRunView((current) => ({ ...current, phase: "cancelled", errorCode: "" }));
        } finally {
          canApplyTerminal = isCurrentTutorRequest();
          if (tutorRequestRef.current === requestContext) {
            tutorRequestRef.current = null;
            if (!awaitingApproval) setActiveRunId(null);
          }
        }
        if (!canApplyTerminal) return false;
        if (cancelled) {
          notify(t("provider.tutorCancelled"));
          return false;
        }
        if (terminalError) {
          notify(t("provider.runFailed"));
          return false;
        }
        if (awaitingApproval) {
          notify(t("provider.approvalRequired"));
          return false;
        }
        if (!completed) {
          setTutorRunView((current) => reduceTutorRunView(current, {
            type: "run.failed",
            data: { code: "tutor.stream_incomplete" },
          }));
          notify(t("provider.streamIncomplete"));
          return false;
        }
        if (attempt.memoryDeclaration) pendingMemoryRequestRef.current = null;
        notify(t("provider.answerUpdated"));
        return true;
      });
      return result === true;
    },
    [activeConversationId, activeRunId, goalId, locale, message, notify, runBusy, selectedSkillIds, t]
  );

  const retryTutor = useCallback(async () => {
    if (!lastTutorAttemptRef.current) return false;
    return askTutor(
      undefined,
      undefined,
      lastTutorAttemptRef.current.taskId,
      lastTutorAttemptRef.current,
    );
  }, [askTutor]);

  const decideToolApproval = useCallback(async (approval: ToolApproval, decision: "approve" | "reject") => {
    await runBusy("chat", async (isCurrentIdentity) => {
      const controller = new AbortController();
      const requestContext: TutorStreamRequest = {
        requestId: crypto.randomUUID(),
        threadId: activeConversationId,
        runId: approval.run_id,
        controller,
      };
      tutorRequestRef.current = requestContext;
      setActiveRunId(approval.run_id);
      setTutorRunView((current) => ({ ...current, phase: "preparing", errorCode: "" }));
      setToolApprovals((current) => current.map((item) => item.approval_id === approval.approval_id ? { ...item, status: decision === "approve" ? "executing" : "rejected" } : item));
      let completed = false;
      let cancelled = false;
      let terminalError = false;
      try {
        const response = await streamPostRequest(
          `/api/tutor/runs/${encodeURIComponent(approval.run_id)}/tool-approvals/${encodeURIComponent(approval.approval_id)}/decision`,
          { decision },
          controller.signal,
        );
        await consumeTutorEventStream(response, (streamEvent) => {
          if (!isCurrentIdentity() || activeConversationIdRef.current !== requestContext.threadId) return;
          setTutorRunView((current) => reduceTutorRunView(current, streamEvent));
          if (streamEvent.type === "teacher.delta") {
            const delta = streamEvent.data.delta;
            if (typeof delta === "string") setChat((current) => ({ ...current, final_answer: current.final_answer + delta }));
          } else if (streamEvent.type === "tool.started") {
            setToolApprovals((current) => current.map((item) => item.approval_id === approval.approval_id ? { ...item, status: "executing" } : item));
          } else if (streamEvent.type === "tool.completed") {
            const status = streamEvent.data.status;
            setToolApprovals((current) => current.map((item) => item.approval_id === approval.approval_id ? { ...item, status: status === "rejected" ? "rejected" : "completed" } : item));
          } else if (streamEvent.type === "run.completed") {
            const resultPayload = streamEvent.data.result;
            if (resultPayload && typeof resultPayload === "object" && !Array.isArray(resultPayload)) {
              const value = resultPayload as Partial<ChatResponse>;
              setChat({
                final_answer: typeof value.final_answer === "string" ? value.final_answer : "",
                citations: Array.isArray(value.citations) ? value.citations : [],
                grounding_status: typeof value.grounding_status === "string" ? value.grounding_status : null,
                insufficient_evidence: value.insufficient_evidence === true,
                missing_information: Array.isArray(value.missing_information) ? value.missing_information : [],
                runtime_metadata: value.runtime_metadata,
              });
              setTranscript((current) => mergeTutorTranscript(current, [{
                id: `${approval.run_id}:assistant`,
                run_id: approval.run_id,
                role: "assistant",
                content: typeof value.final_answer === "string" ? value.final_answer : "",
                created_at: new Date().toISOString(),
                citations: Array.isArray(value.citations) ? value.citations : [],
                grounding_status: typeof value.grounding_status === "string" ? value.grounding_status : null,
              }]));
              setLastCompletedRunId(approval.run_id);
              completed = true;
            }
          } else if (streamEvent.type === "run.failed") {
            setChat({ final_answer: "", citations: [] });
            terminalError = true;
          } else if (streamEvent.type === "run.cancelled") cancelled = true;
        });
      } catch (error) {
        if (!controller.signal.aborted) {
          setTutorRunView((current) => reduceTutorRunView(current, {
            type: "run.failed",
            data: { code: "tutor.network_failed" },
          }));
          throw error;
        }
        cancelled = true;
        setTutorRunView((current) => ({ ...current, phase: "cancelled", errorCode: "" }));
      } finally {
        if (tutorRequestRef.current === requestContext) tutorRequestRef.current = null;
        setActiveRunId(null);
      }
      if (activeConversationId) {
        const restored = await listToolApprovals(activeConversationId);
        if (isCurrentIdentity()) setToolApprovals(restored.approvals);
      }
      if (cancelled) { notify(t("provider.tutorCancelled")); return; }
      if (terminalError) { notify(t("provider.resumeFailed")); return; }
      if (!completed) {
        setTutorRunView((current) => reduceTutorRunView(current, {
          type: "run.failed",
          data: { code: "tutor.approval_stream_incomplete" },
        }));
        notify(t("provider.approvalStreamIncomplete"));
        return;
      }
      notify(decision === "approve" ? t("provider.toolCompleted") : t("provider.toolRejected"));
    });
  }, [activeConversationId, notify, runBusy, t]);

  const submitTutorFeedback = useCallback(async (helpful: boolean) => {
    if (!lastCompletedRunId) {
      notify(t("provider.noAnswer"));
      return;
    }
    await postRequest(`/api/tutor/runs/${lastCompletedRunId}/feedback`, {
      helpful,
      reason_code: helpful ? "helpful" : "needs_review",
    });
    notify(t("provider.feedbackThanks"));
  }, [lastCompletedRunId, notify, t]);

  const createDailyAssessment = useCallback(async () => {
    if (!currentTask) {
      notify(t("provider.noAssessmentTask"));
      return;
    }
    await runBusy("assessment", async (isCurrentIdentity) => {
      notify(t("provider.creatingAssessment", { type: t(assessmentMode === "daily" ? "shell.daily" : assessmentMode === "weekly" ? "shell.weekly" : "page.phaseAssessment") }));
      const knowledgeNodeIds = assessmentMode === "phase"
        ? [...new Set(assessmentStage?.nodes.map((node) => node.knowledge_node_id) ?? [])]
        : [currentTask.knowledge_node_id];
      if (assessmentMode === "phase" && (!assessmentStage || knowledgeNodeIds.length === 0)) {
        notify(t("provider.noAssessmentTask"));
        return;
      }
      const phaseCode = assessmentMode === "phase" ? assessmentStage?.stage_id ?? null : null;
      if (!goalId) {
        setAssessment({
          assessment_id: "demo-assessment",
          assessment_type: assessmentMode,
          status: "active",
          scope: { item_count: 1 },
          items: [
            {
              item_id: "demo-item-1",
              prompt: t("demo.assessmentPrompt"),
              question_type: "explain",
              options: [],
              difficulty: 2
            }
          ]
        });
        setAssessmentAnswers({});
        setAssessmentResult(null);
        notify(t("provider.demoAssessment"));
        return;
      }
      if (!activeConversationId) {
        notify(t("provider.sessionsLoading"));
        return;
      }
      const creationFingerprint = JSON.stringify({
        goalId,
        assessmentMode,
        knowledgeNodeIds,
        locale,
        phaseCode,
      });
      if (pendingAssessmentCreationRef.current?.fingerprint !== creationFingerprint) {
        pendingAssessmentCreationRef.current = { fingerprint: creationFingerprint, requestId: crypto.randomUUID() };
      }
      const assessmentRequestId = pendingAssessmentCreationRef.current.requestId;
      const payload =
        assessmentMode === "phase"
          ? await postRequest<AssessmentDraft>(
              "/api/assessments/phase",
              {
                request_id: assessmentRequestId,
                goal_id: goalId,
                thread_id: activeConversationId,
                phase_code: phaseCode,
                locale,
                knowledge_node_ids: knowledgeNodeIds
              }
            )
          : await postRequest<AssessmentDraft>(
              "/api/assessments",
              {
                request_id: assessmentRequestId,
                goal_id: goalId,
                thread_id: activeConversationId,
                assessment_type: assessmentMode,
                locale,
                knowledge_node_ids: knowledgeNodeIds
              }
            );
      if (!isCurrentIdentity()) return;
      pendingAssessmentCreationRef.current = null;
      setAssessment(payload);
      setAssessmentAnswers({});
      setAssessmentResult(null);
      notify(t("provider.assessmentCreated"));
    });
  }, [activeConversationId, assessmentMode, assessmentStage, currentTask, goalId, locale, notify, runBusy, t]);

  const submitAssessment = useCallback(async () => {
    if (!assessment) {
      notify(t("provider.createAssessmentFirst"));
      return;
    }
    const assessmentNodeId = currentTask?.knowledge_node_id;
    if (!assessmentNodeId) {
      notify(t("provider.assessmentNoNode"));
      return;
    }
    await runBusy("submitAssessment", async (isCurrentIdentity) => {
      notify(t("provider.submittingAssessment"));
      const answers = Object.fromEntries(
        assessment.items.map((item) => [
          item.item_id,
          assessmentAnswers[item.item_id]?.trim() ?? ""
        ])
      );
      if (!goalId) {
        const feedback = t("demo.assessmentFeedback");
        setAssessmentResult({
          assessment_id: assessment.assessment_id,
          attempt_id: "demo-attempt",
          status: "graded",
          score: 60,
          feedback,
          grading: {
            mode: "deterministic_fallback",
            grader_version: "frontend-demo-v2",
            confidence: 0.6,
            needs_review: false,
            automatic_mastery_eligible: true
          },
          mastery_updates: [{
            label: currentTask?.knowledge_node_title ?? "Knowledge progress",
            previous_score: 42,
            new_score: 56,
            new_confidence: 0.6,
            automatic_adjustment_eligible: true,
            reason_codes: ["demo_assessment"]
          }],
          answers: [
            {
              item_id: assessment.items[0].item_id,
              score: 60,
              feedback,
              wrong_reason_tags: ["missing_tradeoff"],
              confidence: 0.6,
              needs_review: false
            }
          ],
          observer_decision: {
            policy_version: "frontend-demo-v2",
            decision: "keep",
            automation_allowed: false,
            confidence: 0.6,
            reason_codes: ["demo_assessment"],
            user_facing_rationale: t("demo.adjustmentRationale")
          },
          plan_adjustment: null
        });
        notify(t("provider.demoAssessmentSubmitted"));
        return;
      }
      const submissionFingerprint = JSON.stringify({ assessmentId: assessment.assessment_id, answers });
      if (pendingAssessmentSubmissionRef.current?.fingerprint !== submissionFingerprint) {
        pendingAssessmentSubmissionRef.current = { fingerprint: submissionFingerprint, requestId: crypto.randomUUID() };
      }
      const assessmentRequestId = pendingAssessmentSubmissionRef.current.requestId;
      const payload = await postRequest<AssessmentResult>(
        `/api/assessments/${assessment.assessment_id}/submit`,
        { request_id: assessmentRequestId, answers }
      );
      if (!isCurrentIdentity()) return;
      pendingAssessmentSubmissionRef.current = null;
      setAssessmentResult(payload);
      await refreshState(goalId);
      if (!isCurrentIdentity()) return;
      notify(t("provider.feedbackGenerated"));
    });
  }, [assessment, assessmentAnswers, currentTask, goalId, notify, refreshState, runBusy, t]);

  const requestPlanAdjustment = useCallback(async () => {
    const trimmed = adjustmentMessage.trim();
    if (!trimmed) {
      notify(t("provider.adjustmentReasonRequired"));
      return;
    }
    await runBusy("replan", async (isCurrentIdentity) => {
      notify(t("provider.adjustmentRequesting"));
      if (!goalId) {
        const demo = {
          adjustment_id: "demo-adjustment",
          decision: "reduce",
          status: "proposed",
          change_summary: { reduced_daily_load: "20%", added: ["review_tasks"] },
          plan_patch: { load_multiplier: 0.8 },
          rationale_json: { rationale: t("demo.adjustmentRationale") }
        };
        setAdjustment(demo);
        notify(t("provider.demoAdjustment"));
        return;
      }
      if (!activeConversationId) {
        notify(t("provider.sessionsLoading"));
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
      notify(t("provider.adjustmentCreated"));
    });
  }, [activeConversationId, adjustmentMessage, goalId, notify, refreshState, runBusy, t]);

  const applyPlanAdjustment = useCallback(async () => {
    if (!adjustment) {
      notify(t("provider.noAdjustment"));
      return;
    }
    await runBusy("applyAdjustment", async (isCurrentIdentity) => {
      notify(t("provider.adjustmentApplying"));
      if (!goalId) {
        setAdjustment((current) => (current ? { ...current, status: "applied", new_plan_id: "demo-plan-v2" } : current));
        notify(t("provider.demoAdjustmentApplied"));
        return;
      }
      const payload = await postRequest<PlanAdjustment>(
        `/api/plans/adjustments/${adjustment.adjustment_id}/apply`,
        { goal_id: goalId, locale }
      );
      if (!isCurrentIdentity()) return;
      setAdjustment(payload);
      await refreshState(goalId);
      if (!isCurrentIdentity()) return;
      notify(t("provider.adjustmentApplied"));
    });
  }, [adjustment, goalId, locale, notify, refreshState, runBusy, t]);

  const fetchDocuments = useCallback(async () => {
    await runBusy("document", async (isCurrentIdentity) => {
      const payload = await listDocuments();
      if (!isCurrentIdentity()) return;
      setDocuments(payload.documents);
      notify(t("provider.documentsRefreshed"));
    });
  }, [notify, runBusy, t]);

  const startDocumentPolling = useCallback((documentId: string, isCurrentIdentity: () => boolean) => {
    if (documentPollersRef.current.has(documentId)) return;
    const cancel = pollDocument(
      documentId,
      getDocument,
      (document) => {
        if (!isCurrentIdentity()) return;
        setDocuments((current) => current.map((item) => (item.id === document.id ? { ...item, ...document } : item)));
        if (document.parse_status === "success") notify(t("provider.documentParsed"));
        if (document.parse_status === "failed") notify(document.parse_error || t("provider.documentFailed"));
        if (document.parse_status === "success" || document.parse_status === "failed") documentPollersRef.current.delete(documentId);
      },
      () => {
        if (isCurrentIdentity()) notify(t("provider.documentProcessing"));
        documentPollersRef.current.delete(documentId);
      }
    );
    documentPollersRef.current.set(documentId, cancel);
  }, [notify, t]);

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
    if (!goalId) {
      notify(t("provider.documentGoalRequired"));
      return false;
    }
    const uploaded = await runBusy("fileUpload", async (isCurrentIdentity) => {
      notify(t("provider.uploadStarting"));
      const payload = await uploadDocumentFile(file, goalId);
      if (!isCurrentIdentity()) return false;
      setDocuments((current) => [payload, ...current.filter((item) => item.id !== payload.id)]);
      if (["pending", "processing"].includes(payload.parse_status)) {
        startDocumentPolling(payload.id, isCurrentIdentity);
      }
      notify(t("provider.fileUploaded"));
      return true;
    });
    return uploaded === true;
  }, [goalId, notify, runBusy, startDocumentPolling, t]);

  const saveNote = useCallback(async () => {
    const content = note.trim();
    if (!content) {
      notify(t("provider.noteRequired"));
      return;
    }
    if (!goalId) {
      notify(t("provider.documentGoalRequired"));
      return;
    }
    await runBusy("document", async (isCurrentIdentity) => {
      notify(t("provider.noteSaving"));
      const payload = await saveMarkdownNote(content, goalId);
      if (!isCurrentIdentity()) return;
      setDocuments((current) => [payload, ...current.filter((item) => item.id !== payload.id)]);
      if (["pending", "processing"].includes(payload.parse_status)) startDocumentPolling(payload.id, isCurrentIdentity);
      setNote((current) => (current.trim() === content ? "" : current));
      notify(t("provider.noteSaved"));
    });
  }, [goalId, note, notify, runBusy, startDocumentPolling, t]);

  const searchOfficialSources = useCallback(async (requestedQuery?: string) => {
    const query = (requestedQuery || sourceQuery).trim();
    if (!query) {
      notify(t("provider.sourceRequired"));
      return;
    }
    await runBusy("sources", async (isCurrentIdentity) => {
      notify(t("provider.sourcesSearching"));
      setSourceSearchErrorCode("");
      let payload: { results: SourceResult[] };
      try {
        payload = await postRequest<{ results: SourceResult[] }>(
          "/api/tools/search-learning-sources",
          { query },
        );
      } catch (error) {
        if (error instanceof ApiError && error.code === "source_search.unavailable") {
          if (isCurrentIdentity()) {
            setSourceResults([]);
            setSourceSearchErrorCode(error.code);
            notify(t("provider.sourcesUnavailable"));
          }
          return;
        }
        throw error;
      }
      if (!isCurrentIdentity()) return;
      setSourceResults(payload.results);
      notify(t("provider.sourcesReturned"));
    });
  }, [notify, runBusy, sourceQuery, t]);

  const searchedTaskIdRef = useRef("");
  useEffect(() => {
    if (isDemoMode || !currentTask || searchedTaskIdRef.current === currentTask.id) return;
    searchedTaskIdRef.current = currentTask.id;
    const query = `${currentTask.knowledge_node_code} ${currentTask.title}`;
    setSourceQuery(query);
    void searchOfficialSources(query);
  }, [currentTask, isDemoMode, searchOfficialSources]);

  const setAssessmentAnswer = useCallback((itemId: string, value: string) => {
    setAssessmentAnswers((current) => ({ ...current, [itemId]: value }));
  }, []);

  const toggleSavedNode = useCallback(
    async (nodeId: string) => {
      const previous = new Set(savedNodes);
      const wasSaved = previous.has(nodeId);
      const next = new Set(previous);
      if (wasSaved) next.delete(nodeId);
      else next.add(nodeId);
      setSavedNodes(next);
      notify(t(wasSaved ? "provider.savedRemoved" : "provider.saved"));
      if (!goalId) return;
      try {
        if (wasSaved) {
          await deleteRequest<void>(
            `/api/saved-learning-nodes/${encodeURIComponent(nodeId)}?goal_id=${encodeURIComponent(goalId)}`,
          );
        } else {
          await putRequest<void>(
            `/api/saved-learning-nodes/${encodeURIComponent(nodeId)}`,
            { goal_id: goalId },
          );
        }
      } catch {
        setSavedNodes(previous);
        notify(t("provider.savedUpdateFailed"));
      }
    },
    [goalId, notify, savedNodes, t]
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
        await navigator.clipboard.writeText(t(resource.detailKey));
        notify(t("provider.templateCopied"));
      } else {
        setResourceModal(resource);
        notify(t("provider.clipboardUnsupported"));
      }
    },
    [notify, t]
  );

  const startTask = useCallback(
    async (task?: Task | null) => {
      if (!task) {
        notify(t("provider.noTaskStart"));
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
          notify(t("provider.enteredTask", { title: localizeDemoTask(task, t).title }));
          router.push(`/tutor?task=${encodeURIComponent(task.id)}`);
          return;
        }
        await postRequest<TaskSessionResponse>(`/api/tasks/${task.id}/start`, {});
        if (!isCurrentIdentity()) return;
        await refreshState(goalId);
        if (!isCurrentIdentity()) return;
        notify(t("provider.enteredTask", { title: localizeDemoTask(task, t).title }));
        router.push(`/tutor?task=${encodeURIComponent(task.id)}`);
      });
    },
    [goalId, notify, refreshState, router, runBusy, t]
  );

  const completeTask = useCallback(
    async (task?: Task) => {
      if (!task) {
        notify(t("provider.noTaskComplete"));
        return;
      }
      await runBusy("completeTask", async (isCurrentIdentity) => {
        if (!goalId) {
          setState((current) => ({
            ...current,
            today_tasks: current.today_tasks.map((item) => (item.id === task.id ? { ...item, status: "completed" } : item))
          }));
          notify(t("provider.completedTask", { title: localizeDemoTask(task, t).title }));
          return;
        }
        const payload = await postRequest<TaskSessionResponse>(
          `/api/tasks/${task.id}/complete`,
          {
            evidence: {
              source: "frontend",
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
        notify(payload.plan_adjustment ? t("provider.completedWithAdjustment") : t("provider.completedTask", { title: localizeDemoTask(task, t).title }));
      });
    },
    [goalId, notify, refreshState, runBusy, t]
  );

  const value = useMemo<LearningContextValue>(
    () => ({
      goalId,
      goalBootstrap,
      isDemoMode,
      retryGoalBootstrap,
      state,
      currentTask,
      masteryRows,
      message,
      setMessage,
      chat,
      conversations,
      activeConversationId,
      activeRunId,
      transcript: tutorRunView.currentQuestion && currentTutorRunId
        ? transcript.filter((item) => item.run_id !== currentTutorRunId)
        : transcript,
      transcriptLoading,
      transcriptNextBefore,
      loadOlderTranscript,
      tutorRunPhase: tutorRunView.phase,
      currentTutorQuestion: tutorRunView.currentQuestion,
      tutorErrorCode: tutorRunView.errorCode,
      retryTutor,
      skills,
      selectedSkillIds,
      setSelectedSkillIds,
      toolApprovals,
      decideToolApproval,
      submitTutorFeedback,
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
      sourceSearchErrorCode,
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
      goalBootstrap,
      isDemoMode,
      retryGoalBootstrap,
      state,
      currentTask,
      masteryRows,
      message,
      chat,
      conversations,
      activeConversationId,
      activeRunId,
      currentTutorRunId,
      transcript,
      transcriptLoading,
      transcriptNextBefore,
      loadOlderTranscript,
      tutorRunView,
      retryTutor,
      skills,
      selectedSkillIds,
      toolApprovals,
      decideToolApproval,
      submitTutorFeedback,
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
      sourceSearchErrorCode,
      note,
      status,
    toast,
    t,
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
