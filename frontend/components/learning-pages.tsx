"use client";

import Link from "next/link";
import {
  MdArrowForward,
  MdLibraryBooks,
  MdRefresh,
  MdSave,
  MdSearch
} from "react-icons/md";

import { HeaderActions, ResourceList, TaskTable } from "@/components/learning-shell";
import { useLearning } from "@/components/learning-provider";

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
  const {
    busy,
    createLearningPath,
    goalTitle,
    setGoalTitle,
    targetOutcome,
    setTargetOutcome,
    userId,
    setUserId,
    weeklyHours,
    setWeeklyHours
  } = useLearning();

  return (
    <>
      <PageHeader
        eyebrow="入学诊断"
        title="建立目标、能力起点与学习路径"
        description="提交基础目标和诊断答案后，系统会生成当前学习路径与今日任务。这里使用已有后端诊断接口，不在前端自行计算掌握度。"
        actions={
          <button
            className="h-10 rounded-lg bg-teal px-4 text-sm font-semibold text-white shadow-material disabled:opacity-60"
            onClick={createLearningPath}
            disabled={Boolean(busy.path)}
            type="button"
          >
            {busy.path ? "生成中" : "生成学习路径"}
          </button>
        }
      />

      <section className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">目标信息</h2>
          <div className="mt-4 grid gap-4">
            <label className="text-sm">
              <span className="mb-2 block text-xs font-semibold text-muted">用户 ID</span>
              <input
                value={userId}
                onChange={(event) => setUserId(event.target.value)}
                className="h-10 w-full rounded-lg border border-line px-3 outline-none focus:border-teal"
              />
            </label>
            <label className="text-sm">
              <span className="mb-2 block text-xs font-semibold text-muted">学习目标</span>
              <input
                value={goalTitle}
                onChange={(event) => setGoalTitle(event.target.value)}
                className="h-10 w-full rounded-lg border border-line px-3 outline-none focus:border-teal"
              />
            </label>
            <label className="text-sm">
              <span className="mb-2 block text-xs font-semibold text-muted">目标产出</span>
              <textarea
                value={targetOutcome}
                onChange={(event) => setTargetOutcome(event.target.value)}
                className="min-h-24 w-full resize-none rounded-lg border border-line p-3 outline-none focus:border-teal"
              />
            </label>
            <label className="text-sm">
              <span className="mb-2 block text-xs font-semibold text-muted">每周学习时间</span>
              <input
                type="number"
                min={1}
                max={80}
                value={weeklyHours}
                onChange={(event) => setWeeklyHours(Number(event.target.value))}
                className="h-10 w-32 rounded-lg border border-line px-3 outline-none focus:border-teal"
              />
            </label>
          </div>
        </div>

        <div className="rounded-lg border border-line bg-[#f8fbfb] p-5">
          <h2 className="font-semibold">诊断答案预览</h2>
          <div className="mt-4 space-y-3 text-sm">
            {[
              ["Python 基础", "已掌握"],
              ["FastAPI 基础", "已掌握"],
              ["LLM API", "需要补强"],
              ["RAG 基础", "需要补强"],
              ["LangGraph", "待学习"]
            ].map(([name, status]) => (
              <div key={name} className="flex items-center justify-between rounded-lg border border-line bg-white px-3 py-2">
                <span>{name}</span>
                <span className={status === "已掌握" ? "text-teal" : "text-coral"}>{status}</span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}

export function PathPage() {
  const { busy, createLearningPath, currentTask, goalId, setUserId, state, userId, note, setNote, uploadDocument } = useLearning();

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
            <input
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              className="h-9 w-44 rounded-lg border border-line bg-white px-3 text-xs outline-none focus:border-teal"
              aria-label="用户 ID"
            />
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
            onClick={uploadDocument}
            disabled={!note.trim() || Boolean(busy.document)}
            type="button"
          >
            <MdSave /> {busy.document ? "保存中" : "保存笔记"}
          </button>
        </div>
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder={`记录你关于「${currentTask.title}」的想法、问题或收获...`}
          className="min-h-36 w-full resize-none rounded-lg border border-line bg-white p-4 text-sm leading-6 outline-none focus:border-teal"
        />
      </section>
    </>
  );
}

export function TodayPage() {
  const { busy, currentTask, refreshState, goalId, userId } = useLearning();
  return (
    <>
      <PageHeader
        eyebrow="今日学习"
        title="今日任务与学习节奏"
        description="从当前任务进入讲师页面，完成学习、笔记和测验。刷新按钮会拉取后端当前状态。"
        actions={
          <button
            className="flex h-10 items-center gap-2 rounded-lg border border-line bg-white px-4 text-sm font-semibold text-teal"
            onClick={() => refreshState(goalId, userId)}
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
          <p className="mt-3 text-sm leading-7 text-muted">{currentTask.objective}</p>
          <Link href={`/tutor?task=${encodeURIComponent(currentTask.id)}`} className="mt-5 flex h-10 items-center justify-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white">
            进入讲师 <MdArrowForward />
          </Link>
        </div>
      </section>
    </>
  );
}

export function TutorPage() {
  const { askTutor, busy, chat, currentTask, message, setMessage } = useLearning();
  return (
    <>
      <PageHeader
        eyebrow="AI 讲师"
        title="围绕当前任务追问和校准理解"
        description={`当前任务：${currentTask.title}。讲师回答会展示可追溯引用，生成学习路径后会调用后端 RAG/讲师工作流。`}
      />
      <section className="rounded-lg border border-line bg-white p-5">
        <form onSubmit={askTutor} className="space-y-4">
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            className="min-h-28 w-full resize-none rounded-lg border border-line p-4 text-sm leading-6 outline-none focus:border-teal"
          />
          <button className="flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60" disabled={Boolean(busy.chat)} type="submit">
            {busy.chat ? "发送中" : "发送给讲师"} <MdArrowForward />
          </button>
        </form>
        <div className="mt-6 border-t border-line pt-5">
          <h2 className="font-semibold">讲师回答</h2>
          <p className="mt-3 text-sm leading-7 text-muted">{chat.final_answer}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {chat.citations.map((citation) => (
              <a key={citation.citation_label} href={citation.source_url || "#"} target="_blank" rel="noreferrer" className="rounded-lg border border-line bg-tealSoft px-3 py-2 text-xs font-semibold text-teal">
                {citation.citation_label}
              </a>
            ))}
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
    note,
    searchOfficialSources,
    setNote,
    setSourceQuery,
    sourceQuery,
    sourceResults,
    uploadDocument,
    userId,
    setUserId
  } = useLearning();

  return (
    <>
      <PageHeader
        eyebrow="设置"
        title="资料、来源与本地学习配置"
        description="管理学习资料、查看处理状态，并检索官方学习来源。上传解析仍由后端处理。"
      />
      <section className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="rounded-lg border border-line bg-white p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold">学习资料</h2>
            <button className="flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm text-teal" onClick={fetchDocuments} type="button">
              <MdRefresh /> {busy.document ? "刷新中" : "刷新资料"}
            </button>
          </div>
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="min-h-32 w-full resize-none rounded-lg border border-line p-3 text-sm outline-none focus:border-teal"
            placeholder="把学习笔记保存为 markdown 资料..."
          />
          <button
            className="mt-3 flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white disabled:opacity-60"
            onClick={uploadDocument}
            disabled={!note.trim() || Boolean(busy.document)}
            type="button"
          >
            <MdLibraryBooks /> 保存为资料
          </button>
          <div className="mt-5 overflow-hidden rounded-lg border border-line text-sm">
            {documents.length === 0 && <div className="p-4 text-muted">暂无上传资料。</div>}
            {documents.map((document) => (
              <div key={document.id} className="grid grid-cols-[1fr_100px_80px] border-b border-line px-4 py-3 last:border-b-0">
                <span>{document.filename}</span>
                <span className="text-muted">{document.parse_status}</span>
                <span className="text-right text-muted">L{document.trusted_level}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-line bg-white p-5">
          <h2 className="font-semibold">账户与官方来源</h2>
          <label className="mt-4 block text-sm">
            <span className="mb-2 block text-xs font-semibold text-muted">用户 ID</span>
            <input value={userId} onChange={(event) => setUserId(event.target.value)} className="h-10 w-full rounded-lg border border-line px-3 outline-none focus:border-teal" />
          </label>
          <label className="mt-4 block text-sm">
            <span className="mb-2 block text-xs font-semibold text-muted">官方来源检索</span>
            <input value={sourceQuery} onChange={(event) => setSourceQuery(event.target.value)} className="h-10 w-full rounded-lg border border-line px-3 outline-none focus:border-teal" />
          </label>
          <button className="mt-3 flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white" onClick={searchOfficialSources} type="button">
            <MdSearch /> 搜索官方资料
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
