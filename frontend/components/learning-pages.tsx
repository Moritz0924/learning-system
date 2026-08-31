"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  MdArrowForward,
  MdRefresh,
  MdSave,
  MdSearch
} from "react-icons/md";

import { HeaderActions, ResourceList, TaskTable } from "@/components/learning-shell";
import { useLearning } from "@/components/learning-provider";
import { useLocale } from "@/components/providers/locale-provider";
import { DocumentList } from "@/features/documents/document-list";
import { DocumentUploadPanel } from "@/features/documents/document-upload-panel";
import { DiagnosisForm, ReassessForm } from "@/features/onboarding/diagnosis-form";
import { getMemoryPrivacy } from "@/features/memory/memory-api";
import { MemorySettingsPanel } from "@/features/memory/memory-settings-panel";
import type { MemoryDeclarationDraft, MemoryPrivacySettings } from "@/features/memory/types";
import { localizeDemoTask } from "@/lib/learning-data";
import { translateEnum } from "@/lib/i18n.mjs";
import { tutorFailureBodyKey } from "@/lib/tutor-stream.mjs";

function PageHeader({
  eyebrow,
  title,
  description,
  actions
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-5 flex min-h-11 items-start justify-between gap-4">
      <div>
        <div className="text-xs text-muted">{eyebrow}</div>
        <h1 className="mt-1 text-2xl font-semibold tracking-[0]">{title}</h1>
        <p className="mt-3 max-w-3xl text-sm leading-7 text-muted">{description}</p>
      </div>
      {actions}
    </header>
  );
}

function Metric({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="border-r border-line pr-4 last:border-r-0">
      <div className="text-xs text-muted">{label}</div>
      <div className={`mt-1 text-sm font-semibold ${accent ? "text-teal" : "text-ink"}`}>{value}</div>
      {accent && (
        <div className="mt-2 h-2 rounded-full bg-[#e2ebec]">
          <div className="h-2 rounded-full bg-teal" style={{ width: value }} />
        </div>
      )}
    </div>
  );
}

export function DiagnosisPage() {
  const { t } = useLocale();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { busy, initializeOnboarding, refreshState } = useLearning();
  const isReassess = searchParams.get("mode") === "reassess";
  const reassessGoalId = searchParams.get("goal_id");

  return (
    <>
      <PageHeader
        eyebrow={t("page.diagnosisEyebrow")}
        title={t("page.diagnosisTitle")}
        description={t("page.diagnosisDescription")}
      />
      {isReassess ? (
        reassessGoalId ? (
          <ReassessForm
            busy={Boolean(busy.path)}
            goalId={reassessGoalId}
            onComplete={async () => {
              await refreshState(reassessGoalId);
              router.push("/path");
            }}
          />
        ) : (
          <p role="alert" className="border-t border-line pt-5 text-sm text-coral">
            {t("provider.noLearningPath")}
          </p>
        )
      ) : (
        <DiagnosisForm busy={Boolean(busy.path)} onInitialize={initializeOnboarding} />
      )}
    </>
  );
}

export function PathPage() {
  const { t } = useLocale();
  const searchParams = useSearchParams();
  const { busy, createLearningPath, currentTask, goalId, state, note, saveNote, setNote } = useLearning();
  const requestedNodeId = searchParams.get("node");
  const selectedStage = requestedNodeId
    ? state.roadmap?.stages.find((stage) => stage.nodes.some((node) => node.node_id === requestedNodeId))
    : undefined;
  const currentStage = selectedStage ?? state.roadmap?.stages.find((stage) => stage.status === "current") ?? state.roadmap?.stages[0];
  const currentNode = requestedNodeId
    ? currentStage?.nodes.find((node) => node.node_id === requestedNodeId)
    : currentStage?.nodes.find((node) => node.status === "current") ?? currentStage?.nodes[0];
  const stageTask = currentNode?.task_id
    ? state.today_tasks.find((task) => task.id === currentNode.task_id) ?? null
    : null;
  const displayTask = stageTask ? localizeDemoTask(stageTask, t) : null;

  return (
    <>
      <PageHeader
        eyebrow={t("page.currentNode")}
        title={currentNode?.title || state.roadmap?.title || state.goal.title || t("page.currentNodeFallback")}
        description={currentNode?.objective || currentStage?.objective || t("roadmap.empty")}
        actions={<HeaderActions task={stageTask} />}
      />

      <section className="border-b border-line pb-6">
        <div className="grid grid-cols-4 gap-4 text-sm max-[940px]:grid-cols-2">
          <Metric label={t("page.estimated")} value={t("shell.minutes", { count: stageTask?.estimated_minutes ?? 0 })} />
          <Metric label={t("roadmap.current")} value={currentStage ? t(`roadmap.${currentStage.status}`) : t("roadmap.empty")} />
          <Metric label={t("page.mastery")} value={`${Math.round((currentNode?.progress ?? 0) * 100)}%`} accent />
          <Metric label={t("page.planStatus")} value={t("page.version", { version: state.active_plan.version })} />
        </div>
      </section>

      {state.roadmap ? (
        <section className="border-b border-line py-5" aria-label={state.roadmap.title}>
          <h2 className="font-semibold">{state.roadmap.title}</h2>
          <div className="mt-4 space-y-4">
            {state.roadmap.stages.map((stage) => (
              <article key={stage.stage_id} className={`border-l-2 pl-4 ${stage.status === "current" ? "border-teal" : "border-line"}`}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-teal">{t(`roadmap.${stage.status}`)}</div>
                    <h3 className="mt-1 text-sm font-semibold">{stage.title}</h3>
                    <p className="mt-1 text-xs leading-5 text-muted">{stage.objective}</p>
                  </div>
                  <span className="text-xs text-muted">{t("roadmap.progress", { progress: Math.round(stage.progress * 100) })}</span>
                </div>
                <ol className="mt-3 space-y-2">
                  {stage.nodes.map((node) => (
                    <li key={node.node_id} className={node.status === "current" ? "rounded-lg bg-tealSoft px-3 py-2" : "px-3 py-2"}>
                      <div className="flex items-center justify-between gap-3 text-sm">
                        <span className="font-medium">{node.title}</span>
                        <span className="text-xs text-muted">{t("roadmap.progress", { progress: Math.round(node.progress * 100) })}</span>
                      </div>
                    </li>
                  ))}
                </ol>
              </article>
            ))}
          </div>
        </section>
      ) : (
        <section className="border-b border-line py-6 text-sm text-muted">
          <p>{t("roadmap.empty")}</p>
          <Link href="/diagnosis" className="mt-3 inline-flex font-semibold text-teal underline underline-offset-4">
            {t("roadmap.reassess")}
          </Link>
        </section>
      )}

      <section className="py-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="font-semibold">{t("page.diagnosisToday")}</h2>
          <div className="flex items-center gap-2">
            {goalId ? (
              <Link
                className="inline-flex h-9 items-center rounded-lg border border-teal px-3 text-xs font-semibold text-teal"
                href={`/diagnosis?mode=reassess&goal_id=${encodeURIComponent(goalId)}`}
              >
                {t("roadmap.reassess")}
              </Link>
            ) : (
              <button
                className="h-9 rounded-lg border border-teal px-3 text-xs font-semibold text-teal disabled:opacity-60"
                onClick={createLearningPath}
                disabled={Boolean(busy.path)}
                type="button"
              >
                {busy.path ? t("shell.creating") : t("page.generatePath")}
              </button>
            )}
          </div>
        </div>
        <TaskTable />
      </section>

      <section className="py-4">
        <h2 className="mb-3 font-semibold">{t("page.learningResources")}</h2>
        <ResourceList />
      </section>

      <section className="py-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">{t("page.learningNotes")}</h2>
          <button
            className="flex h-9 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
            onClick={saveNote}
            disabled={!note.trim() || Boolean(busy.document)}
            type="button"
          >
            <MdSave /> {busy.document ? t("page.savingNote") : t("page.saveNote")}
          </button>
        </div>
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder={t("page.notePlaceholder", { title: displayTask?.title || t("page.currentNodeFallback") })}
          className="min-h-36 w-full resize-none rounded-lg border border-line bg-white p-4 text-sm leading-6 outline-none focus:border-teal"
        />
      </section>
    </>
  );
}

export function TodayPage() {
  const { t } = useLocale();
  const { busy, currentTask, refreshState, goalId } = useLearning();
  const displayTask = currentTask ? localizeDemoTask(currentTask, t) : null;
  return (
    <>
      <PageHeader
        eyebrow={t("page.todayEyebrow")}
        title={t("page.todayTitle")}
        description={t("page.todayDescription")}
        actions={
          <button
            data-testid="refresh-today-state"
            className="flex h-10 items-center gap-2 rounded-lg border border-line bg-white px-4 text-sm font-semibold text-teal"
            onClick={() => refreshState(goalId)}
            type="button"
          >
            <MdRefresh /> {busy.refresh ? t("page.refreshing") : t("page.refreshState")}
          </button>
        }
      />
      <section className="grid gap-4 lg:grid-cols-[1fr_280px]">
        <TaskTable />
        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">{t("page.currentRecommendation")}</h2>
          {currentTask ? (
            <>
              <p className="mt-3 text-sm leading-7 text-muted">{displayTask?.objective}</p>
              <Link data-testid="primary-start-task" href={`/tutor?task=${encodeURIComponent(currentTask.id)}`} className="mt-5 flex h-10 items-center justify-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white">
                {t("page.enterTutor")} <MdArrowForward />
              </Link>
            </>
          ) : (
            <>
              <p className="mt-3 text-sm leading-7 text-muted">{t("page.noPendingTask")}</p>
              <button
                data-testid="primary-start-task"
                className="mt-5 flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
                disabled
                type="button"
              >
                {t("page.enterTutor")} <MdArrowForward />
              </button>
            </>
          )}
        </div>
      </section>
    </>
  );
}

export function TutorPage() {
  const { locale, t } = useLocale();
  const searchParams = useSearchParams();
  const {
    activeConversationId,
    activeRunId,
    askTutor,
    skills,
    selectedSkillIds,
    setSelectedSkillIds,
    toolApprovals,
    decideToolApproval,
    submitTutorFeedback,
    busy,
    cancelTutor,
    chat,
    conversations,
    createConversation,
    currentTutorQuestion,
    currentTask,
    deleteConversation,
    isDemoMode,
    message,
    retryTutor,
    selectConversation,
    setMessage,
    tutorErrorCode,
    tutorRunPhase,
    transcript,
    transcriptLoading,
    state,
  } = useLearning();
  const requestedTaskId = searchParams.get("task");
  const selectedTask = requestedTaskId
    ? state.today_tasks.find((task) => task.id === requestedTaskId) ?? null
    : currentTask;
  const displayTask = selectedTask ? localizeDemoTask(selectedTask, t) : null;
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [memoryType, setMemoryType] = useState<"learning_preference" | "long_term_goal">("learning_preference");
  const [preferenceKey, setPreferenceKey] = useState("explanation_style");
  const [preferenceValue, setPreferenceValue] = useState("");
  const [goalTitle, setGoalTitle] = useState("");
  const [targetOutcome, setTargetOutcome] = useState("");
  const [deadline, setDeadline] = useState("");
  const [memoryPrivacy, setMemoryPrivacy] = useState<MemoryPrivacySettings | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getMemoryPrivacy()
      .then((settings) => { if (!cancelled) setMemoryPrivacy(settings); })
      .catch(() => { if (!cancelled) setMemoryPrivacy(null); });
    return () => { cancelled = true; };
  }, []);

  const explicitMemoryAllowed = Boolean(memoryPrivacy?.enabled && memoryPrivacy.allow_explicit_user);
  const effectiveMemoryEnabled = memoryEnabled && explicitMemoryAllowed;

  const submitTutorQuestion = async (event: FormEvent) => {
    event.preventDefault();
    let draft: MemoryDeclarationDraft | null = null;
    if (effectiveMemoryEnabled) {
      draft = memoryType === "learning_preference"
        ? {
            memory_type: "learning_preference",
            preference_key: preferenceKey.trim(),
            preference_value: preferenceValue.trim(),
          }
        : {
            memory_type: "long_term_goal",
            title: goalTitle.trim(),
            target_outcome: targetOutcome.trim(),
            deadline: deadline || null,
          };
    }
    const succeeded = await askTutor(undefined, draft, requestedTaskId);
    if (succeeded && draft) setMemoryEnabled(false);
  };

  const memoryDraftInvalid = effectiveMemoryEnabled && (
    memoryType === "learning_preference"
      ? !preferenceKey.trim() || !preferenceValue.trim()
      : !goalTitle.trim() || !targetOutcome.trim()
  );
  return (
    <>
      <PageHeader
        eyebrow={t("page.tutorEyebrow")}
        title={t("page.tutorTitle")}
        description={t("page.tutorDescription", { task: displayTask?.title || t("page.noTask") })}
      />
      <section className="rounded-lg border border-line bg-white p-5">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <select
            aria-label={t("tutor.session")}
            className="h-9 min-w-48 rounded-lg border border-line bg-white px-3 text-sm"
            value={activeConversationId}
            disabled={Boolean(activeRunId) || Boolean(busy.chat)}
            onChange={(event) => selectConversation(event.target.value)}
          >
            {conversations.map((conversation, index) => (
              <option key={conversation.thread_id} value={conversation.thread_id}>
                {conversation.title || `${t("tutor.session")} ${index + 1}`}
              </option>
            ))}
          </select>
          <button
            className="h-9 rounded-lg border border-line px-3 text-xs font-semibold text-teal disabled:opacity-60"
            disabled={Boolean(activeRunId) || Boolean(busy.chat)}
            onClick={() => void createConversation()}
            type="button"
          >
            {t("tutor.newSession")}
          </button>
          <button
            className="h-9 rounded-lg border border-line px-3 text-xs font-semibold text-muted disabled:opacity-60"
            disabled={!activeConversationId || Boolean(activeRunId) || Boolean(busy.chat)}
            onClick={() => void deleteConversation(activeConversationId)}
            type="button"
          >
            {t("tutor.deleteSession")}
          </button>
        </div>
        <section data-testid="tutor-transcript" className="space-y-3 border-t border-line py-4">
          {transcriptLoading && <p className="text-sm text-muted">{t("tutor.transcriptLoading")}</p>}
          {transcript.map((item) => (
            <article
              data-testid="tutor-transcript-message"
              key={item.id}
              className={`rounded-lg px-4 py-3 text-sm leading-6 ${item.role === "assistant" ? "bg-tealSoft" : "bg-[#fbfdfc]"}`}
            >
              <div className="mb-1 text-xs font-semibold text-muted">
                {t(item.role === "assistant" ? "tutor.assistant" : "tutor.userQuestion")}
              </div>
              <p>{item.content}</p>
            </article>
          ))}
        </section>
        <form onSubmit={(event) => void submitTutorQuestion(event)} className="space-y-4">
          <textarea
            data-testid="tutor-question"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            className="min-h-28 w-full resize-none rounded-lg border border-line p-4 text-sm leading-6 outline-none focus:border-teal"
          />
          {skills.length > 0 && (
            <fieldset className="rounded-lg border border-line bg-[#fbfdfc] p-3">
              <legend className="px-1 text-xs font-semibold text-muted">{t("tutor.skills")}</legend>
              <div className="flex flex-wrap gap-2">
                {skills.map((skill) => (
                  <label key={skill.id} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${selectedSkillIds.includes(skill.id) ? "border-teal bg-tealSoft text-teal" : "border-line bg-white text-muted"}`}>
                    <input
                      type="checkbox"
                      checked={selectedSkillIds.includes(skill.id)}
                      onChange={(event) => setSelectedSkillIds(event.target.checked ? [...selectedSkillIds, skill.id] : selectedSkillIds.filter((id) => id !== skill.id))}
                    />
                    {skill.name}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          <label className="flex items-center gap-2 rounded-lg border border-line bg-[#fbfdfc] p-3 text-sm">
            <input
              data-testid="memory-declaration-toggle"
              type="checkbox"
              checked={effectiveMemoryEnabled}
              disabled={!explicitMemoryAllowed}
              onChange={(event) => setMemoryEnabled(event.target.checked)}
            />
            {t("tutor.saveMemory")}
          </label>
          {!explicitMemoryAllowed && (
            <p className="text-xs text-muted">{t("tutor.memoryUnavailable")}</p>
          )}
          {effectiveMemoryEnabled && (
            <div data-testid="memory-declaration-form" className="space-y-3 rounded-lg border border-line p-4">
              <select className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={memoryType} onChange={(event) => setMemoryType(event.target.value as "learning_preference" | "long_term_goal")}>
                <option value="learning_preference">{t("tutor.learningPreference")}</option>
                <option value="long_term_goal">{t("tutor.longTermGoal")}</option>
              </select>
              {memoryType === "learning_preference" ? (
                <>
                  <input data-testid="memory-preference-key" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={preferenceKey} onChange={(event) => setPreferenceKey(event.target.value)} placeholder={t("tutor.preferenceKey")} />
                  <input data-testid="memory-preference-value" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={preferenceValue} onChange={(event) => setPreferenceValue(event.target.value)} placeholder={t("tutor.preferenceValue")} />
                </>
              ) : (
                <>
                  <input data-testid="memory-goal-title" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={goalTitle} onChange={(event) => setGoalTitle(event.target.value)} placeholder={t("tutor.goalTitle")} />
                  <textarea data-testid="memory-goal-outcome" className="min-h-20 w-full rounded-lg border border-line p-3 text-sm" value={targetOutcome} onChange={(event) => setTargetOutcome(event.target.value)} placeholder={t("tutor.targetOutcome")} />
                  <input data-testid="memory-goal-deadline" type="date" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={deadline} onChange={(event) => setDeadline(event.target.value)} />
                </>
              )}
            </div>
          )}
          <button data-testid="tutor-submit" className="flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60" disabled={Boolean(busy.chat) || Boolean(activeRunId) || memoryDraftInvalid} type="submit">
            {busy.chat ? t("shell.sending") : t("tutor.sendToTutor")} <MdArrowForward />
          </button>
          {activeRunId && (
            <button
              className="h-10 rounded-lg border border-line px-4 text-sm font-semibold text-muted"
              onClick={() => void cancelTutor()}
              type="button"
            >
              {t("tutor.cancelResponse")}
            </button>
          )}
        </form>
        {currentTutorQuestion && (
          <section className="mt-6 border-t border-line pt-5">
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">{t("tutor.userQuestion")}</div>
            <p data-testid="tutor-user-turn" className="mt-2 text-sm font-medium leading-7 text-ink">{currentTutorQuestion}</p>
          </section>
        )}
        {["preparing", "retrieving", "writing", "awaiting_approval"].includes(tutorRunPhase) && (
          <div data-testid="tutor-thinking" aria-live="polite" className="mt-4 flex items-center gap-3 border-l-2 border-teal bg-tealSoft/60 px-3 py-2 text-sm text-teal">
            <span className="h-2 w-2 animate-pulse rounded-full bg-teal" aria-hidden="true" />
            {t(`tutor.phase.${tutorRunPhase}`)}
          </div>
        )}
        {tutorRunPhase !== "idle" && (
          <div data-testid="tutor-run-status" className="sr-only" aria-live="polite">
            {t(`tutor.phase.${tutorRunPhase}`)}
          </div>
        )}
        {tutorRunPhase === "failed" && (
          <div data-testid="tutor-failure" role="alert" className="mt-4 border-l-2 border-coral bg-[#fff6f3] px-4 py-3 text-sm text-coral">
            <h2 className="font-semibold">{t("tutor.failureTitle")}</h2>
            <p className="mt-1 leading-6">{t(tutorFailureBodyKey(tutorErrorCode))}</p>
            {tutorErrorCode && <p className="mt-2 text-xs">{t("tutor.errorCode", { code: tutorErrorCode })}</p>}
            <div className="mt-3 flex flex-wrap gap-3">
              <button type="button" disabled={Boolean(busy.chat)} onClick={() => void retryTutor()} className="rounded-lg border border-coral px-3 py-2 text-xs font-semibold disabled:opacity-50">
                {t("tutor.retry")}
              </button>
              <Link href="/ai-config" className="rounded-lg bg-coral px-3 py-2 text-xs font-semibold text-white">
                {t("tutor.openAiConfig")}
              </Link>
            </div>
          </div>
        )}
        {toolApprovals.length > 0 && (
          <section className="mt-6 space-y-3 border-t border-line pt-5" aria-label={t("tutor.toolApprovals")}>
            <h2 className="font-semibold">{t("tutor.toolApprovals")}</h2>
            {toolApprovals.map((approval) => (
              <article key={approval.approval_id} className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div><div className="text-xs font-semibold uppercase tracking-wide text-amber-700">{approval.server.name}</div><div className="mt-1 text-sm font-semibold">{approval.tool_name}</div></div>
                  <span className="rounded-full bg-white px-2 py-1 text-xs text-muted">{translateEnum(locale, "approval", approval.status)}</span>
                </div>
                <div className="mt-3 text-xs font-semibold text-muted">{t("tutor.arguments")}</div>
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-white p-3 text-xs leading-5 text-ink">{JSON.stringify(approval.arguments, null, 2)}</pre>
                {approval.status === "pending" && (
                  <div className="mt-3 flex gap-2">
                    <button disabled={Boolean(busy.chat)} type="button" onClick={() => void decideToolApproval(approval, "approve")} className="rounded-lg bg-teal px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">{t("tutor.approveOnce")}</button>
                    <button disabled={Boolean(busy.chat)} type="button" onClick={() => void decideToolApproval(approval, "reject")} className="rounded-lg border border-line bg-white px-3 py-2 text-xs font-semibold text-coral disabled:opacity-50">{t("tutor.reject")}</button>
                  </div>
                )}
              </article>
            ))}
          </section>
        )}
        <div className="mt-6 border-t border-line pt-5">
          <h2 className="font-semibold">{t("tutor.answer")}</h2>
          <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold">
            <span className="rounded-lg border border-line bg-tealSoft px-2 py-1 text-teal">
              LLM {isDemoMode ? t("tutor.demoLabel") : chat.runtime_metadata?.llm?.mode || t("tutor.runtimeUnknown")}
            </span>
            <span className="rounded-lg border border-line bg-amber-50 px-2 py-1 text-amber-700">
              RAG {t("shell.citations", { count: chat.runtime_metadata?.rag?.citation_count ?? chat.citations.length })}
            </span>
          </div>
          <p data-testid="tutor-answer" className="mt-3 text-sm leading-7 text-muted">
            {chat.final_answer}
            {tutorRunPhase === "writing" && chat.final_answer && (
              <span data-testid="tutor-streaming-cursor" className="ml-0.5 animate-pulse text-teal" aria-hidden="true">▍</span>
            )}
          </p>
          {chat.grounding_status && (
            <div className="mt-3 rounded-lg border border-line bg-slate-50 px-3 py-2 text-xs text-muted">
              {t("tutor.grounding", { status: translateEnum(locale, "grounding", chat.grounding_status) })}
              {chat.missing_information?.length ? t("tutor.needMore", { items: chat.missing_information.join(", ") }) : ""}
            </div>
          )}
          {tutorRunPhase === "completed" && !isDemoMode && (
            <div className="mt-3 flex gap-2 text-xs">
              <button className="rounded-lg border border-line px-3 py-2" type="button" onClick={() => void submitTutorFeedback(true)}>{t("tutor.helpful")}</button>
              <button className="rounded-lg border border-line px-3 py-2" type="button" onClick={() => void submitTutorFeedback(false)}>{t("tutor.needsImprovement")}</button>
            </div>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {chat.citations.map((citation) => (
              <a key={citation.citation_label} href={citation.source_url || "#"} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-line bg-tealSoft px-3 py-2 text-xs font-semibold text-teal">
                {citation.citation_label}
              </a>
            ))}
            {chat.citations.length === 0 && <span className="rounded-lg border border-line px-3 py-2 text-xs font-semibold text-muted">{t("shell.noCitations")}</span>}
          </div>
        </div>
      </section>
    </>
  );
}

export function AssessmentPage() {
  const { locale, t } = useLocale();
  const {
    assessment,
    assessmentAnswers,
    assessmentMode,
    assessmentResult,
    busy,
    createDailyAssessment,
    setAssessmentAnswer,
    setAssessmentMode,
    submitAssessment
  } = useLearning();

  return (
    <>
      <PageHeader
        eyebrow={t("page.assessmentEyebrow")}
        title={t("page.assessmentTitle")}
        description={t("page.assessmentDescription")}
        actions={
          <button
            data-testid="assessment-create"
            className="h-10 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60"
            onClick={createDailyAssessment}
            disabled={Boolean(busy.assessment)}
            type="button"
          >
            {busy.assessment ? t("shell.creating") : t("shell.createAssessment")}
          </button>
        }
      />
      <section className="rounded-lg border border-line bg-white p-5">
        <div className="mb-5 grid w-fit grid-cols-3 rounded-lg border border-line text-sm">
          {[
            ["daily", t("shell.daily")],
            ["weekly", t("shell.weekly")],
            ["phase", t("page.phaseAssessment")]
          ].map(([value, label]) => (
            <button
              key={value}
              className={`px-4 py-2 ${assessmentMode === value ? "bg-tealSoft text-teal" : "text-muted"}`}
              onClick={() => setAssessmentMode(value as "daily" | "weekly" | "phase")}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>

        {!assessment && <div className="rounded-lg border border-dashed border-line p-6 text-sm text-muted">{t("page.assessmentEmpty")}</div>}

        {assessment && (
          <div className="space-y-4">
            {assessment.items.map((item, index) => (
              <article key={item.item_id} className="block rounded-lg border border-line bg-[#fbfdfc] p-4" data-testid={`assessment-item-${item.question_type}`}>
                <span className="text-sm font-semibold">
                  {index + 1}. {item.prompt}
                </span>
                {item.question_type === "choice" ? (
                  <fieldset className="mt-3 space-y-2" aria-label={t("assessment.choiceGroup", { index: index + 1 })}>
                    {item.options.map((option) => (
                      <label key={option.option_id} className="flex cursor-pointer items-center gap-2 rounded border border-line bg-white px-3 py-2 text-sm">
                        <input
                          checked={assessmentAnswers[item.item_id] === option.option_id}
                          name={`assessment-${item.item_id}`}
                          onChange={() => setAssessmentAnswer(item.item_id, option.option_id)}
                          type="radio"
                          value={option.option_id}
                        />
                        {option.label}
                      </label>
                    ))}
                  </fieldset>
                ) : (
                  <textarea
                    data-testid={`assessment-answer-${item.question_type}`}
                    value={assessmentAnswers[item.item_id] || ""}
                    onChange={(event) => setAssessmentAnswer(item.item_id, event.target.value)}
                    className="mt-3 min-h-24 w-full resize-none rounded-lg border border-line bg-white p-3 text-sm outline-none focus:border-teal"
                    placeholder={t("page.answerPlaceholder")}
                  />
                )}
              </article>
            ))}
            <button
              data-testid="assessment-submit"
              className="h-10 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60"
              onClick={submitAssessment}
              disabled={Boolean(busy.submitAssessment)}
              type="button"
            >
              {busy.submitAssessment ? t("shell.submitting") : t("common.submit")}
            </button>
          </div>
        )}

        {assessmentResult && (
          <div className="mt-5 rounded-lg border border-[#f2dc9b] bg-amberSoft p-4 text-sm" data-testid="assessment-result">
            <div className="font-semibold">
              {assessmentResult.status === "review_required"
                ? t("assessment.reviewRequired")
                : t("page.score", { score: assessmentResult.score ?? "—" })}
            </div>
            <div className="mt-2 text-muted">{assessmentResult.feedback}</div>
            <div className="mt-2 text-xs text-muted">
              {t("assessment.gradingSummary", {
                mode: translateEnum(locale, "grading", assessmentResult.grading.mode),
                confidence: assessmentResult.grading.confidence ?? "—"
              })}
            </div>
            {assessmentResult.plan_adjustment && (
              <div className="mt-3 rounded border border-line bg-white p-3 text-xs" data-testid="assessment-plan-proposal">
                <div className="font-semibold">
                  {t("assessment.planProposal", {
                    decision: translateEnum(locale, "plan", assessmentResult.plan_adjustment.decision)
                  })}
                </div>
                <div className="mt-1 text-muted">{assessmentResult.plan_adjustment.rationale}</div>
                <div className="mt-1 text-muted">{t("assessment.confirmBeforeApply")}</div>
              </div>
            )}
          </div>
        )}
      </section>
    </>
  );
}

export function ProgressPage() {
  const { locale, t } = useLocale();
  const { adjustment, adjustmentMessage, applyPlanAdjustment, busy, masteryRows, requestPlanAdjustment, setAdjustmentMessage, state } = useLearning();
  return (
    <>
      <PageHeader
        eyebrow={t("page.progressEyebrow")}
        title={t("page.progressTitle")}
        description={t("page.progressDescription")}
      />
      <section className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">{t("shell.mastery")}</h2>
          <div className="mt-4 space-y-4">
            {masteryRows.map((item) => (
              <div key={item.label} className="grid grid-cols-[150px_1fr_48px] items-center gap-3 text-sm">
                <span className="truncate text-muted">{item.label}</span>
                <span className="h-3 rounded-full bg-[#e2ebec]">
                  <span className="block h-3 rounded-full bg-teal" style={{ width: `${Math.min(100, Math.max(0, item.score))}%` }} />
                </span>
                <span className="text-right text-xs text-muted">{Math.round(item.score)}%</span>
              </div>
            ))}
          </div>
          <h2 className="mt-6 font-semibold">{t("page.reviewQueue")}</h2>
          <div className="mt-3 rounded-lg border border-line bg-[#f8fbfb] p-4 text-sm text-muted">
            {state.current_state.review_queue?.length ? JSON.stringify(state.current_state.review_queue) : t("page.noReviewQueue")}
          </div>
        </div>

        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">{t("page.manualAdjustment")}</h2>
          <textarea
            value={adjustmentMessage}
            onChange={(event) => setAdjustmentMessage(event.target.value)}
            className="mt-4 min-h-28 w-full resize-none rounded-lg border border-line p-3 text-sm outline-none focus:border-teal"
          />
          <button
            className="mt-3 h-10 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60"
            onClick={requestPlanAdjustment}
            disabled={Boolean(busy.replan)}
            type="button"
          >
            {busy.replan ? t("shell.submitting") : t("shell.submitAdjustment")}
          </button>
          {adjustment && (
            <div className="mt-5 rounded-lg border border-line bg-[#fbfdfc] p-4 text-sm">
              <div className="flex items-center justify-between gap-3">
                <div className="font-semibold">{t("page.adjustmentResult", { decision: translateEnum(locale, "plan", adjustment.decision) })}</div>
                {adjustment.status === "proposed" && adjustment.automation_allowed !== false && !adjustment.plan_patch?.no_change && (
                  <button
                    className="h-9 rounded-lg bg-ink px-3 text-xs font-semibold text-white disabled:opacity-60"
                    onClick={applyPlanAdjustment}
                    disabled={Boolean(busy.applyAdjustment)}
                    type="button"
                  >
                    {busy.applyAdjustment ? t("shell.applying") : t("shell.applyAdjustment")}
                  </button>
                )}
              </div>
              <div className="mt-3 text-xs font-semibold text-ink">{t("shell.changeSummary")}</div>
              <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap text-xs text-muted">{JSON.stringify(adjustment.change_summary, null, 2)}</pre>
              <div className="mt-4 text-xs font-semibold text-ink">{t("shell.adjustmentReason")}</div>
              <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap text-xs text-muted">{JSON.stringify(adjustment.rationale_json, null, 2)}</pre>
            </div>
          )}
        </div>
      </section>
    </>
  );
}

export function SettingsPage() {
  const { locale, t } = useLocale();
  const {
    busy,
    documents,
    fetchDocuments,
    goalId,
    note,
    refreshDocument,
    saveNote,
    searchOfficialSources,
    setNote,
    setSourceQuery,
    sourceQuery,
    sourceResults,
    sourceSearchErrorCode,
    uploadFile
  } = useLearning();

  useEffect(() => {
    void fetchDocuments();
  }, [fetchDocuments]);

  return (
    <>
      <PageHeader
        eyebrow={t("page.settingsEyebrow")}
        title={t("page.settingsTitle")}
        description={t("page.settingsDescription")}
      />
      <MemorySettingsPanel goalId={goalId || undefined} />
      <section className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <div className="rounded-lg border border-line bg-white p-5">
            <div className="mb-4">
              <p className="text-xs font-semibold text-teal">{t("page.uploadFile")}</p>
              <h2 className="mt-1 font-semibold">{t("page.addMaterials")}</h2>
            </div>
            <DocumentUploadPanel busy={Boolean(busy.fileUpload)} onUpload={uploadFile} />
          </div>

          <div className="rounded-lg border border-line bg-white p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-teal">{t("page.saveNote")}</p>
                <h2 className="mt-1 font-semibold">{t("page.createMarkdown")}</h2>
              </div>
              <button
                data-testid="save-markdown-note"
                className="h-9 rounded-lg border border-teal px-3 text-xs font-semibold text-teal disabled:opacity-60"
                onClick={saveNote}
                disabled={!note.trim() || Boolean(busy.document)}
                type="button"
              >
                {busy.document ? t("page.savingNote") : t("page.saveNote")}
              </button>
            </div>
            <textarea
              data-testid="markdown-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="min-h-28 w-full resize-none rounded-lg border border-line p-3 text-sm outline-none focus:border-teal"
              placeholder={t("page.noteMarkdownPlaceholder")}
            />
          </div>

          <div className="rounded-lg border border-line bg-[#fbfdfc] p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-teal">{t("page.processingQueue")}</p>
                <h2 className="mt-1 font-semibold">{t("page.myMaterials")}</h2>
              </div>
              <button
                className="flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm text-teal disabled:opacity-60"
                onClick={fetchDocuments}
                disabled={Boolean(busy.document)}
                type="button"
              >
                <MdRefresh /> {busy.document ? t("page.refreshing") : t("page.refreshList")}
              </button>
            </div>
            <DocumentList documents={documents} onRefreshDocument={refreshDocument} />
          </div>
        </div>

        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">{t("page.accountSources")}</h2>
          <label className="mt-4 block text-sm">
            <span className="mb-2 block text-xs font-semibold text-muted">{t("page.sourceSearch")}</span>
            <input value={sourceQuery} onChange={(event) => setSourceQuery(event.target.value)} className="h-10 w-full rounded-lg border border-line px-3 outline-none focus:border-teal" />
          </label>
          <button className="mt-3 flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white" onClick={() => void searchOfficialSources()} type="button">
            <MdSearch /> {t("page.searchLearningMaterials")}
          </button>
          {sourceSearchErrorCode === "source_search.unavailable" && (
            <p data-testid="source-search-unavailable" role="status" className="mt-4 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {t("source.unavailable")}
            </p>
          )}
          <div className="mt-5 space-y-3">
            {sourceResults.map((source) => (
              <a key={source.url} href={source.url} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-line bg-tealSoft p-3 text-sm text-teal">
                <span className="font-semibold">{source.title}</span>
                <span className="mt-1 block text-xs text-muted">{source.source_level === "web" ? t("source.webUnverified") : translateEnum(locale, "source", source.source_level)} · {source.retrieved_at}</span>
              </a>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
