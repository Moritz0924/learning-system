"use client";

import Link from "next/link";
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
import { DocumentList } from "@/features/documents/document-list";
import { DocumentUploadPanel } from "@/features/documents/document-upload-panel";
import { DiagnosisForm } from "@/features/onboarding/diagnosis-form";
import { getMemoryPrivacy } from "@/features/memory/memory-api";
import { MemorySettingsPanel } from "@/features/memory/memory-settings-panel";
import type { MemoryDeclarationDraft, MemoryPrivacySettings } from "@/features/memory/types";

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
  const { busy, initializeOnboarding } = useLearning();

  return (
    <>
      <PageHeader
        eyebrow="入学诊断"
        title="建立真实的能力起点"
        description="填写目标、时间与偏好，完成自评和知识题。后端将独立评分并一次性生成学习目标、路径与今日任务。"
      />
      <DiagnosisForm busy={Boolean(busy.path)} onInitialize={initializeOnboarding} />
    </>
  );
}

export function PathPage() {
  const { busy, createLearningPath, currentTask, goalId, state, note, saveNote, setNote } = useLearning();

  return (
    <>
      <PageHeader
        eyebrow="当前节点"
        title="3.3 模型选择与提示工程"
        description="学会根据场景选择合适模型，设计高质量提示词，提升输出效果的稳定性与可控性。"
        actions={<HeaderActions />}
      />

      <section className="border-b border-line pb-6">
        <div className="grid grid-cols-4 gap-4 text-sm max-[940px]:grid-cols-2">
          <Metric label="预计" value="90 分钟" />
          <Metric label="难度" value="中等" />
          <Metric label="掌握度" value={`${Math.round(state.mastery_summary.llm_api_basics?.score || 42)}%`} accent />
          <Metric label="计划状态" value={`版本 ${state.active_plan.version}`} />
        </div>
      </section>

      <section className="py-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="font-semibold">入学诊断与今日任务</h2>
          <div className="flex items-center gap-2">
            <button
              className="h-9 rounded-lg border border-teal px-3 text-xs font-semibold text-teal disabled:opacity-60"
              onClick={createLearningPath}
              disabled={Boolean(busy.path)}
              type="button"
            >
              {goalId ? "重新生成路径" : busy.path ? "生成中" : "生成学习路径"}
            </button>
          </div>
        </div>
        <TaskTable />
      </section>

      <section className="py-4">
        <h2 className="mb-3 font-semibold">学习资料</h2>
        <ResourceList />
      </section>

      <section className="py-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">学习笔记</h2>
          <button
            className="flex h-9 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
            onClick={saveNote}
            disabled={!note.trim() || Boolean(busy.document)}
            type="button"
          >
            <MdSave /> {busy.document ? "保存中" : "保存笔记"}
          </button>
        </div>
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder={`记录你关于「${currentTask?.title || "当前学习节点"}」的想法、问题或收获...`}
          className="min-h-36 w-full resize-none rounded-lg border border-line bg-white p-4 text-sm leading-6 outline-none focus:border-teal"
        />
      </section>
    </>
  );
}

export function TodayPage() {
  const { busy, currentTask, refreshState, goalId } = useLearning();
  return (
    <>
      <PageHeader
        eyebrow="今日学习"
        title="今日任务与学习节奏"
        description="从当前任务进入讲师页面，完成学习、笔记和测验。刷新按钮会拉取后端当前状态。"
        actions={
          <button
            data-testid="refresh-today-state"
            className="flex h-10 items-center gap-2 rounded-lg border border-line bg-white px-4 text-sm font-semibold text-teal"
            onClick={() => refreshState(goalId)}
            type="button"
          >
            <MdRefresh /> {busy.refresh ? "刷新中" : "刷新状态"}
          </button>
        }
      />
      <section className="grid gap-4 lg:grid-cols-[1fr_280px]">
        <TaskTable />
        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">当前推荐</h2>
          {currentTask ? (
            <>
              <p className="mt-3 text-sm leading-7 text-muted">{currentTask.objective}</p>
              <Link data-testid="primary-start-task" href={`/tutor?task=${encodeURIComponent(currentTask.id)}`} className="mt-5 flex h-10 items-center justify-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white">
                进入讲师 <MdArrowForward />
              </Link>
            </>
          ) : (
            <>
              <p className="mt-3 text-sm leading-7 text-muted">今天没有待学习任务。</p>
              <button
                data-testid="primary-start-task"
                className="mt-5 flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
                disabled
                type="button"
              >
                进入讲师 <MdArrowForward />
              </button>
            </>
          )}
        </div>
      </section>
    </>
  );
}

export function TutorPage() {
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
    currentTask,
    deleteConversation,
    message,
    selectConversation,
    setMessage,
  } = useLearning();
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
    const succeeded = await askTutor(undefined, draft);
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
        eyebrow="AI 讲师"
        title="围绕当前任务追问和校准理解"
        description={`当前任务：${currentTask?.title || "暂无任务"}。讲师回答会展示可追溯引用，生成学习路径后会调用后端 RAG/讲师工作流。`}
      />
      <section className="rounded-lg border border-line bg-white p-5">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <select
            aria-label="Tutor session"
            className="h-9 min-w-48 rounded-lg border border-line bg-white px-3 text-sm"
            value={activeConversationId}
            disabled={Boolean(activeRunId) || Boolean(busy.chat)}
            onChange={(event) => selectConversation(event.target.value)}
          >
            {conversations.map((conversation, index) => (
              <option key={conversation.thread_id} value={conversation.thread_id}>
                {conversation.title || `Tutor session ${index + 1}`}
              </option>
            ))}
          </select>
          <button
            className="h-9 rounded-lg border border-line px-3 text-xs font-semibold text-teal disabled:opacity-60"
            disabled={Boolean(activeRunId) || Boolean(busy.chat)}
            onClick={() => void createConversation()}
            type="button"
          >
            New session
          </button>
          <button
            className="h-9 rounded-lg border border-line px-3 text-xs font-semibold text-muted disabled:opacity-60"
            disabled={!activeConversationId || Boolean(activeRunId) || Boolean(busy.chat)}
            onClick={() => void deleteConversation(activeConversationId)}
            type="button"
          >
            Delete session
          </button>
        </div>
        <form onSubmit={(event) => void submitTutorQuestion(event)} className="space-y-4">
          <textarea
            data-testid="tutor-question"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            className="min-h-28 w-full resize-none rounded-lg border border-line p-4 text-sm leading-6 outline-none focus:border-teal"
          />
          {skills.length > 0 && (
            <fieldset className="rounded-lg border border-line bg-[#fbfdfc] p-3">
              <legend className="px-1 text-xs font-semibold text-muted">Tutor skills</legend>
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
            Also save a structured long-term memory
          </label>
          {!explicitMemoryAllowed && (
            <p className="text-xs text-muted">Explicit memory saving is unavailable under the current privacy settings.</p>
          )}
          {effectiveMemoryEnabled && (
            <div data-testid="memory-declaration-form" className="space-y-3 rounded-lg border border-line p-4">
              <select className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={memoryType} onChange={(event) => setMemoryType(event.target.value as "learning_preference" | "long_term_goal")}>
                <option value="learning_preference">Learning preference</option>
                <option value="long_term_goal">Long-term goal</option>
              </select>
              {memoryType === "learning_preference" ? (
                <>
                  <input data-testid="memory-preference-key" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={preferenceKey} onChange={(event) => setPreferenceKey(event.target.value)} placeholder="Preference key" />
                  <input data-testid="memory-preference-value" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={preferenceValue} onChange={(event) => setPreferenceValue(event.target.value)} placeholder="Preference value" />
                </>
              ) : (
                <>
                  <input data-testid="memory-goal-title" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={goalTitle} onChange={(event) => setGoalTitle(event.target.value)} placeholder="Goal title" />
                  <textarea data-testid="memory-goal-outcome" className="min-h-20 w-full rounded-lg border border-line p-3 text-sm" value={targetOutcome} onChange={(event) => setTargetOutcome(event.target.value)} placeholder="Target outcome" />
                  <input data-testid="memory-goal-deadline" type="date" className="h-10 w-full rounded-lg border border-line px-3 text-sm" value={deadline} onChange={(event) => setDeadline(event.target.value)} />
                </>
              )}
            </div>
          )}
          <button data-testid="tutor-submit" className="flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60" disabled={Boolean(busy.chat) || Boolean(activeRunId) || memoryDraftInvalid} type="submit">
            {busy.chat ? "发送中" : "发送给讲师"} <MdArrowForward />
          </button>
          {activeRunId && (
            <button
              className="h-10 rounded-lg border border-line px-4 text-sm font-semibold text-muted"
              onClick={() => void cancelTutor()}
              type="button"
            >
              Cancel response
            </button>
          )}
        </form>
        {toolApprovals.length > 0 && (
          <section className="mt-6 space-y-3 border-t border-line pt-5" aria-label="Tool approvals">
            <h2 className="font-semibold">Tool approvals</h2>
            {toolApprovals.map((approval) => (
              <article key={approval.approval_id} className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div><div className="text-xs font-semibold uppercase tracking-wide text-amber-700">{approval.server.name}</div><div className="mt-1 text-sm font-semibold">{approval.tool_name}</div></div>
                  <span className="rounded-full bg-white px-2 py-1 text-xs text-muted">{approval.status}</span>
                </div>
                <div className="mt-3 text-xs font-semibold text-muted">Sanitized arguments</div>
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-white p-3 text-xs leading-5 text-ink">{JSON.stringify(approval.arguments, null, 2)}</pre>
                {approval.status === "pending" && (
                  <div className="mt-3 flex gap-2">
                    <button disabled={Boolean(busy.chat)} type="button" onClick={() => void decideToolApproval(approval, "approve")} className="rounded-lg bg-teal px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve once</button>
                    <button disabled={Boolean(busy.chat)} type="button" onClick={() => void decideToolApproval(approval, "reject")} className="rounded-lg border border-line bg-white px-3 py-2 text-xs font-semibold text-coral disabled:opacity-50">Reject</button>
                  </div>
                )}
              </article>
            ))}
          </section>
        )}
        <div className="mt-6 border-t border-line pt-5">
          <h2 className="font-semibold">讲师回答</h2>
          <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold">
            <span className="rounded-lg border border-line bg-tealSoft px-2 py-1 text-teal">
              LLM {chat.runtime_metadata?.llm?.mode || "unknown"}
            </span>
            <span className="rounded-lg border border-line bg-amber-50 px-2 py-1 text-amber-700">
              RAG {chat.runtime_metadata?.rag?.citation_count ?? chat.citations.length} 引用
            </span>
          </div>
          <p className="mt-3 text-sm leading-7 text-muted">{chat.final_answer}</p>
          {chat.grounding_status && (
            <div className="mt-3 rounded-lg border border-line bg-slate-50 px-3 py-2 text-xs text-muted">
              Grounding: {chat.grounding_status}
              {chat.missing_information?.length ? `；需要补充：${chat.missing_information.join("、")}` : ""}
            </div>
          )}
          <div className="mt-3 flex gap-2 text-xs">
            <button className="rounded-lg border border-line px-3 py-2" type="button" onClick={() => void submitTutorFeedback(true)}>有帮助</button>
            <button className="rounded-lg border border-line px-3 py-2" type="button" onClick={() => void submitTutorFeedback(false)}>需要改进</button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {chat.citations.map((citation) => (
              <a key={citation.citation_label} href={citation.source_url || "#"} target="_blank" rel="noreferrer" className="rounded-lg border border-line bg-tealSoft px-3 py-2 text-xs font-semibold text-teal">
                {citation.citation_label}
              </a>
            ))}
            {chat.citations.length === 0 && <span className="rounded-lg border border-line px-3 py-2 text-xs font-semibold text-muted">暂无检索引用</span>}
          </div>
        </div>
      </section>
    </>
  );
}

export function AssessmentPage() {
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
        eyebrow="测验"
        title="创建测验、提交答案并查看反馈"
        description="日测、周测和阶段测使用同一套后端测验接口。提交后会刷新掌握度和复习队列。"
        actions={
          <button
            className="h-10 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60"
            onClick={createDailyAssessment}
            disabled={Boolean(busy.assessment)}
            type="button"
          >
            {busy.assessment ? "创建中" : "创建测验"}
          </button>
        }
      />
      <section className="rounded-lg border border-line bg-white p-5">
        <div className="mb-5 grid w-fit grid-cols-3 rounded-lg border border-line text-sm">
          {[
            ["daily", "日测"],
            ["weekly", "周测"],
            ["phase", "阶段测"]
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

        {!assessment && <div className="rounded-lg border border-dashed border-line p-6 text-sm text-muted">尚未创建测验。点击“创建测验”后会在这里出现题目。</div>}

        {assessment && (
          <div className="space-y-4">
            {assessment.items.map((item, index) => (
              <label key={item.item_id} className="block rounded-lg border border-line bg-[#fbfdfc] p-4">
                <span className="text-sm font-semibold">
                  {index + 1}. {item.prompt}
                </span>
                <textarea
                  value={assessmentAnswers[item.item_id] || ""}
                  onChange={(event) => setAssessmentAnswer(item.item_id, event.target.value)}
                  className="mt-3 min-h-24 w-full resize-none rounded-lg border border-line bg-white p-3 text-sm outline-none focus:border-teal"
                  placeholder="写下你的答案..."
                />
              </label>
            ))}
            <button
              className="h-10 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60"
              onClick={submitAssessment}
              disabled={Boolean(busy.submitAssessment)}
              type="button"
            >
              {busy.submitAssessment ? "提交中" : "提交答案"}
            </button>
          </div>
        )}

        {assessmentResult && (
          <div className="mt-5 rounded-lg border border-[#f2dc9b] bg-amberSoft p-4 text-sm">
            <div className="font-semibold">得分 {assessmentResult.score}</div>
            <div className="mt-2 text-muted">{assessmentResult.feedback}</div>
          </div>
        )}
      </section>
    </>
  );
}

export function ProgressPage() {
  const { adjustment, adjustmentMessage, applyPlanAdjustment, busy, masteryRows, requestPlanAdjustment, setAdjustmentMessage, state } = useLearning();
  return (
    <>
      <PageHeader
        eyebrow="进度"
        title="掌握度、复习队列与计划调整"
        description="这里展示后端返回的掌握度快照和计划调整结果，前端只负责展示与提交请求。"
      />
      <section className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">知识掌握度</h2>
          <div className="mt-4 space-y-4">
            {masteryRows.map(([name, item]) => (
              <div key={name} className="grid grid-cols-[150px_1fr_48px] items-center gap-3 text-sm">
                <span className="truncate text-muted">{name}</span>
                <span className="h-3 rounded-full bg-[#e2ebec]">
                  <span className="block h-3 rounded-full bg-teal" style={{ width: `${Math.min(100, Math.max(0, item.score))}%` }} />
                </span>
                <span className="text-right text-xs text-muted">{Math.round(item.score)}%</span>
              </div>
            ))}
          </div>
          <h2 className="mt-6 font-semibold">复习队列</h2>
          <div className="mt-3 rounded-lg border border-line bg-[#f8fbfb] p-4 text-sm text-muted">
            {state.current_state.review_queue?.length ? JSON.stringify(state.current_state.review_queue) : "暂无复习队列"}
          </div>
        </div>

        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">手动计划调整</h2>
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
            {busy.replan ? "提交中" : "提交调整"}
          </button>
          {adjustment && (
            <div className="mt-5 rounded-lg border border-line bg-[#fbfdfc] p-4 text-sm">
              <div className="flex items-center justify-between gap-3">
                <div className="font-semibold">调整结果：{adjustment.decision}</div>
                {adjustment.status === "proposed" && (
                  <button
                    className="h-9 rounded-lg bg-ink px-3 text-xs font-semibold text-white disabled:opacity-60"
                    onClick={applyPlanAdjustment}
                    disabled={Boolean(busy.applyAdjustment)}
                    type="button"
                  >
                    {busy.applyAdjustment ? "应用中" : "应用调整"}
                  </button>
                )}
              </div>
              <div className="mt-3 text-xs font-semibold text-ink">差异摘要</div>
              <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap text-xs text-muted">{JSON.stringify(adjustment.change_summary, null, 2)}</pre>
              <div className="mt-4 text-xs font-semibold text-ink">调整依据</div>
              <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap text-xs text-muted">{JSON.stringify(adjustment.rationale_json, null, 2)}</pre>
            </div>
          )}
        </div>
      </section>
    </>
  );
}

export function SettingsPage() {
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
    uploadFile
  } = useLearning();

  useEffect(() => {
    void fetchDocuments();
  }, [fetchDocuments]);

  return (
    <>
      <PageHeader
        eyebrow="设置"
        title="上传资料并跟踪处理状态"
        description="文件和 Markdown 笔记使用独立入口；解析由后端异步完成，这里只展示安全的处理状态和结果摘要。"
      />
      <MemorySettingsPanel goalId={goalId || undefined} />
      <section className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <div className="rounded-lg border border-line bg-white p-5">
            <div className="mb-4">
              <p className="text-xs font-semibold text-teal">上传文件</p>
              <h2 className="mt-1 font-semibold">添加可解析的学习资料</h2>
            </div>
            <DocumentUploadPanel busy={Boolean(busy.fileUpload)} onUpload={uploadFile} />
          </div>

          <div className="rounded-lg border border-line bg-white p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-teal">保存笔记</p>
                <h2 className="mt-1 font-semibold">创建 Markdown 学习资料</h2>
              </div>
              <button
                data-testid="save-markdown-note"
                className="h-9 rounded-lg border border-teal px-3 text-xs font-semibold text-teal disabled:opacity-60"
                onClick={saveNote}
                disabled={!note.trim() || Boolean(busy.document)}
                type="button"
              >
                {busy.document ? "保存中" : "保存笔记"}
              </button>
            </div>
            <textarea
              data-testid="markdown-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="min-h-28 w-full resize-none rounded-lg border border-line p-3 text-sm outline-none focus:border-teal"
              placeholder="把学习笔记保存为 Markdown 资料..."
            />
          </div>

          <div className="rounded-lg border border-line bg-[#fbfdfc] p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-teal">处理队列</p>
                <h2 className="mt-1 font-semibold">我的学习资料</h2>
              </div>
              <button
                className="flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm text-teal disabled:opacity-60"
                onClick={fetchDocuments}
                disabled={Boolean(busy.document)}
                type="button"
              >
                <MdRefresh /> {busy.document ? "刷新中" : "刷新列表"}
              </button>
            </div>
            <DocumentList documents={documents} onRefreshDocument={refreshDocument} />
          </div>
        </div>

        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">账户与官方来源</h2>
          <label className="mt-4 block text-sm">
            <span className="mb-2 block text-xs font-semibold text-muted">官方来源检索</span>
            <input value={sourceQuery} onChange={(event) => setSourceQuery(event.target.value)} className="h-10 w-full rounded-lg border border-line px-3 outline-none focus:border-teal" />
          </label>
          <button className="mt-3 flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white" onClick={searchOfficialSources} type="button">
            <MdSearch /> 检索官方资料
          </button>
          <div className="mt-5 space-y-3">
            {sourceResults.map((source) => (
              <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="block rounded-lg border border-line bg-tealSoft p-3 text-sm text-teal">
                <span className="font-semibold">{source.title}</span>
                <span className="mt-1 block text-xs text-muted">{source.source_level} · {source.retrieved_at}</span>
              </a>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
