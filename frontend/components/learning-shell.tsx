"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useMemo, useState } from "react";
import type { IconType } from "react-icons";
import {
  MdAccountCircle,
  MdBookmarkBorder,
  MdBookmark,
  MdCheckCircle,
  MdClose,
  MdContentCopy,
  MdFilterList,
  MdKeyboardArrowRight,
  MdLibraryBooks,
  MdNotificationsNone,
  MdOpenInNew,
  MdPlayArrow,
  MdRadioButtonUnchecked,
  MdSchool,
  MdSearch,
  MdSend,
  MdSync,
  MdUploadFile
} from "react-icons/md";

import { useLearning } from "@/components/learning-provider";
import { navItems, pathNodes, resourceRows, statusText } from "@/lib/learning-data";

export function LearningShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const {
    askTutor,
    assessment,
    assessmentMode,
    busy,
    chat,
    closeResource,
    copyResource,
    createDailyAssessment,
    dismissToast,
    message,
    masteryRows,
    notify,
    requestPlanAdjustment,
    applyPlanAdjustment,
    resourceModal,
    savedNodes,
    setAssessmentMode,
    setMessage,
    setUserId,
    sourceResults,
    status,
    toast,
    toggleSavedNode,
    userId,
    adjustmentMessage,
    setAdjustmentMessage,
    documents,
    searchOfficialSources,
    adjustment
  } = useLearning();
  const [filterOpen, setFilterOpen] = useState(false);
  const [pathFilter, setPathFilter] = useState<"all" | "active" | "queued">("all");
  const [popover, setPopover] = useState<"notifications" | "profile" | null>(null);

  const visibleNodes = useMemo(() => {
    if (pathFilter === "all") return pathNodes;
    return pathNodes.filter((node) => node.state === pathFilter);
  }, [pathFilter]);

  return (
    <main className="min-h-screen bg-[#f7faf9] text-ink">
      <div className="grid min-h-screen grid-cols-[206px_246px_minmax(480px,1fr)_410px] max-[1380px]:grid-cols-[76px_230px_minmax(520px,1fr)] max-[1380px]:[&_.rightRail]:col-span-3 max-[1380px]:[&_.rightRail]:grid max-[1380px]:[&_.rightRail]:grid-cols-2 max-[840px]:block">
        <aside className="border-r border-line bg-white max-[840px]:sticky max-[840px]:top-0 max-[840px]:z-20 max-[840px]:border-b max-[840px]:border-r-0">
          <div className="flex h-16 items-center gap-3 border-b border-line px-5 max-[1380px]:justify-center max-[1380px]:px-3 max-[840px]:h-14 max-[840px]:border-b-0">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-teal text-white">
              <MdSchool size={22} />
            </div>
            <div className="text-sm font-semibold max-[1380px]:hidden">
              自适应 AI 应用开发
              <br />
              私教
            </div>
          </div>
          <nav className="space-y-1 px-2 py-6 max-[840px]:flex max-[840px]:gap-1 max-[840px]:space-y-0 max-[840px]:overflow-x-auto max-[840px]:px-3 max-[840px]:py-2">
            {navItems.map((item) => {
              const Icon = item.icon;
              const selected = pathname === item.href;
              return (
                <Link
                  key={item.id}
                  href={item.href}
                  className={`flex h-11 w-full items-center gap-3 rounded-lg px-4 text-sm transition ${
                    selected ? "bg-tealSoft text-teal shadow-sm" : "text-muted hover:bg-[#f1f6f6]"
                  } max-[1380px]:justify-center max-[1380px]:px-0 max-[840px]:h-10 max-[840px]:min-w-10`}
                  title={item.label}
                >
                  <Icon size={22} />
                  <span className="font-medium max-[1380px]:hidden">{item.label}</span>
                </Link>
              );
            })}
          </nav>
          <div className="mt-auto border-t border-line px-5 py-6 text-sm max-[1380px]:hidden">
            <div className="text-xs text-muted">当前阶段</div>
            <div className="mt-2 font-semibold">阶段 3</div>
            <div className="mt-1 text-muted">AI 应用构建</div>
            <button className="mt-6 flex items-center gap-2 text-teal" onClick={() => router.push("/path")} type="button">
              切换阶段 <MdKeyboardArrowRight />
            </button>
          </div>
        </aside>

        <section className="relative border-r border-line bg-white/80 px-5 py-6 max-[840px]:border-b">
          <div className="flex items-center justify-between border-b border-line pb-4">
            <h2 className="text-base font-semibold">学习路线图</h2>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg hover:bg-[#edf4f3]"
              title="筛选路线"
              onClick={() => setFilterOpen((value) => !value)}
              type="button"
            >
              <MdFilterList />
            </button>
          </div>
          {filterOpen && (
            <div className="mt-3 rounded-lg border border-line bg-white p-2 text-xs shadow-material">
              {[
                ["all", "全部"],
                ["active", "进行中"],
                ["queued", "待学习"]
              ].map(([value, label]) => (
                <button
                  key={value}
                  className={`mr-2 rounded-lg px-3 py-2 ${pathFilter === value ? "bg-teal text-white" : "bg-[#f3f7f7] text-muted"}`}
                  onClick={() => setPathFilter(value as "all" | "active" | "queued")}
                  type="button"
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <div className="mt-7 space-y-2">
            {visibleNodes.map((node) => (
              <button
                key={`${node.phase}-${node.title}`}
                className={`group grid w-full grid-cols-[22px_1fr] gap-3 rounded-lg px-2 py-2 text-left transition ${
                  node.title.includes("模型选择") ? "bg-[#eaf2ff] text-[#1769d5]" : "hover:bg-[#f1f6f6]"
                }`}
                onClick={() => {
                  notify(`已选中 ${node.phase} ${node.title}`);
                  router.push(`/path?node=${encodeURIComponent(node.phase)}`);
                }}
                type="button"
              >
                <span className="relative mt-1 flex justify-center">
                  <span
                    className={`h-4 w-4 rounded-full border-2 ${
                      node.state === "done"
                        ? "border-teal bg-teal"
                        : node.state === "active"
                          ? "border-[#2d73d9] bg-white"
                          : "border-[#98a6aa] bg-white"
                    }`}
                  />
                  <span className="absolute top-5 h-9 border-l border-dashed border-[#b8c6c8] group-last:hidden" />
                </span>
                <span>
                  <span className="block text-xs font-semibold text-muted">{node.phase}</span>
                  <span className="block text-sm font-semibold">{node.title}</span>
                  <span className={`mt-1 block text-xs ${node.state === "done" ? "text-teal" : "text-muted"}`}>
                    {node.progress > 0 ? `${node.progress}% 完成` : "待学习"}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="overflow-y-auto bg-[#fbfdfc] px-7 py-5">{children}</section>

        <aside className="rightRail overflow-y-auto border-l border-line bg-white px-5 py-5">
          <div className="relative mb-5 flex h-11 items-center justify-between border-b border-line pb-4">
            <div className="flex items-center gap-3 text-sm font-semibold">
              <Image src="/coach-avatar.png" alt="AI 讲师头像" width={34} height={34} className="rounded-full" />
              AI 讲师 · 你的专属教练
            </div>
            <div className="flex items-center gap-3 text-muted">
              <button
                className="grid h-8 w-8 place-items-center rounded-lg hover:bg-[#f1f6f6]"
                onClick={() => setPopover(popover === "notifications" ? null : "notifications")}
                title="通知"
                type="button"
              >
                <MdNotificationsNone />
              </button>
              <button
                className="grid h-8 w-8 place-items-center rounded-lg hover:bg-[#f1f6f6]"
                onClick={() => setPopover(popover === "profile" ? null : "profile")}
                title="账户"
                type="button"
              >
                <MdAccountCircle />
              </button>
            </div>
            {popover && (
              <div className="absolute right-0 top-10 z-20 w-64 rounded-lg border border-line bg-white p-3 text-xs shadow-material">
                {popover === "notifications" ? (
                  <div>
                    <div className="font-semibold text-ink">通知</div>
                    <p className="mt-2 leading-5 text-muted">当前没有新的系统通知。创建测验或计划调整后，这里会同步提醒。</p>
                  </div>
                ) : (
                  <div>
                    <label className="text-xs font-semibold text-muted" htmlFor="profile-user-id">
                      用户 ID
                    </label>
                    <input
                      id="profile-user-id"
                      value={userId}
                      onChange={(event) => setUserId(event.target.value)}
                      className="mt-2 h-9 w-full rounded-lg border border-line px-3 outline-none focus:border-teal"
                    />
                  </div>
                )}
              </div>
            )}
          </div>

          <section className="rounded-lg border border-line bg-[#fbfdfc] p-4">
            <form onSubmit={askTutor} className="space-y-3">
              <label className="block text-xs font-semibold text-muted" htmlFor="quick-tutor-question">
                追问讲师
              </label>
              <textarea
                id="quick-tutor-question"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                className="min-h-16 w-full resize-none rounded-lg border border-line bg-white p-3 text-sm outline-none focus:border-teal"
              />
              <button
                className="ml-auto flex h-9 items-center gap-2 rounded-lg bg-teal px-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
                type="submit"
                disabled={Boolean(busy.chat)}
              >
                <MdSend /> {busy.chat ? "发送中" : "发送"}
              </button>
            </form>
            <div className="mt-4 border-t border-line pt-4 text-sm leading-7">
              {chat.final_answer}
              <div className="mt-3 flex flex-wrap gap-2">
                {chat.citations.map((citation) => (
                  <a
                    key={citation.citation_label}
                    href={citation.source_url || "#"}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-line bg-tealSoft px-2 py-1 text-xs font-semibold text-teal"
                  >
                    {citation.citation_label}
                  </a>
                ))}
              </div>
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">测验与评估</h2>
              <div className="grid grid-cols-3 rounded-lg border border-line text-xs">
                {[
                  ["daily", "日测"],
                  ["weekly", "周测"],
                  ["phase", "阶段"]
                ].map(([value, label]) => (
                  <button
                    key={value}
                    className={`px-3 py-2 ${assessmentMode === value ? "bg-tealSoft text-teal" : "text-muted"}`}
                    onClick={() => setAssessmentMode(value as "daily" | "weekly" | "phase")}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-line bg-white p-4">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <div className="text-sm font-semibold">今日测验进度</div>
                  <div className="mt-1 text-xs text-muted">{assessment ? `${assessment.items.length} 题已生成` : "尚未创建测验"}</div>
                </div>
                <button
                  className="h-9 rounded-lg border border-teal px-3 text-xs font-semibold text-teal disabled:opacity-60"
                  onClick={createDailyAssessment}
                  disabled={Boolean(busy.assessment)}
                  type="button"
                >
                  {busy.assessment ? "创建中" : "创建测验"}
                </button>
              </div>
              {assessment && (
                <div className="space-y-3 border-t border-line pt-3">
                  <div className="text-sm">{assessment.items[0].prompt}</div>
                  <button
                    className="h-9 rounded-lg bg-teal px-3 text-xs font-semibold text-white"
                    onClick={() => router.push("/assessment")}
                    type="button"
                  >
                    去提交答案
                  </button>
                </div>
              )}
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-5">
            <h2 className="mb-3 font-semibold">知识掌握度</h2>
            <div className="space-y-3">
              {masteryRows.slice(0, 5).map(([name, item]) => (
                <div key={name} className="grid grid-cols-[120px_1fr_42px] items-center gap-3 text-sm">
                  <span className="truncate text-muted">{name}</span>
                  <span className="h-2 rounded-full bg-[#e2ebec]">
                    <span className="block h-2 rounded-full bg-teal" style={{ width: `${Math.min(100, Math.max(0, item.score))}%` }} />
                  </span>
                  <span className="text-right text-xs text-muted">{Math.round(item.score)}%</span>
                </div>
              ))}
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">计划调整</h2>
              <button
                className="h-9 rounded-lg bg-teal px-3 text-xs font-semibold text-white disabled:opacity-60"
                onClick={requestPlanAdjustment}
                disabled={Boolean(busy.replan)}
                type="button"
              >
                {busy.replan ? "提交中" : "提交调整"}
              </button>
            </div>
            <textarea
              value={adjustmentMessage}
              onChange={(event) => setAdjustmentMessage(event.target.value)}
              className="mb-3 min-h-16 w-full resize-none rounded-lg border border-line p-3 text-xs outline-none focus:border-teal"
            />
            <div className="grid grid-cols-[1fr_28px_1fr] gap-3 text-xs">
              <div className="rounded-lg border border-line bg-[#f8fbfb] p-3">
                <div className="font-semibold">调整前</div>
                <JsonPreview value={adjustment?.before_snapshot} fallback="暂无后端调整记录" />
              </div>
              <div className="grid place-items-center text-amber-500">
                <MdKeyboardArrowRight size={24} />
              </div>
              <div className="rounded-lg border border-[#f2dc9b] bg-amberSoft p-3">
                <div className="font-semibold">调整后</div>
                <JsonPreview value={adjustment?.after_snapshot} fallback="等待计划调整结果" />
              </div>
            </div>
            {adjustment && (
              <div className="mt-3 space-y-2 rounded-lg border border-line bg-[#fbfdfc] p-3 text-xs">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold">差异摘要</div>
                  {adjustment.status === "proposed" && (
                    <button
                      className="h-8 rounded-lg bg-ink px-3 text-xs font-semibold text-white disabled:opacity-60"
                      onClick={applyPlanAdjustment}
                      disabled={Boolean(busy.applyAdjustment)}
                      type="button"
                    >
                      {busy.applyAdjustment ? "应用中" : "应用调整"}
                    </button>
                  )}
                </div>
                <JsonPreview value={adjustment.change_summary} fallback="暂无差异摘要" />
                <div className="font-semibold">调整依据</div>
                <JsonPreview value={adjustment.rationale_json} fallback="暂无调整依据" />
              </div>
            )}
          </section>

          <section className="mt-5 border-t border-line pt-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">设置与数据</h2>
              <button className="flex items-center gap-1 text-xs text-teal" onClick={searchOfficialSources} type="button">
                <MdSearch /> 搜索官方来源
              </button>
            </div>
            <div className="overflow-hidden rounded-lg border border-line bg-white text-sm">
              <SettingRow icon={MdLibraryBooks} label="学习资料库" value={documents[0]?.parse_status ? `${documents.length} 个文件` : "等待上传"} />
              <SettingRow icon={MdUploadFile} label="资料上传状态" value={documents[0]?.filename || "未上传"} />
              <SettingRow icon={MdSync} label="模型与网关设置" value="经后端 LLM Gateway" />
            </div>
            {sourceResults[0] && (
              <a href={sourceResults[0].url} target="_blank" rel="noreferrer" className="mt-3 block rounded-lg border border-line bg-tealSoft p-3 text-xs text-teal">
                {sourceResults[0].title}
              </a>
            )}
          </section>

          <div className="mt-5 rounded-lg border border-line bg-[#fbfdfc] px-3 py-2 text-xs text-muted">{status}</div>
        </aside>
      </div>

      {resourceModal && (
        <div className="fixed inset-0 z-40 grid place-items-center bg-black/20 px-4">
          <div className="w-full max-w-lg rounded-lg border border-line bg-white p-5 shadow-material">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-xs font-semibold text-teal">{resourceModal.type}</div>
                <h2 className="mt-1 text-lg font-semibold">{resourceModal.title}</h2>
              </div>
              <button className="grid h-9 w-9 place-items-center rounded-lg hover:bg-[#f1f6f6]" onClick={closeResource} title="关闭" type="button">
                <MdClose />
              </button>
            </div>
            <p className="mt-4 text-sm leading-7 text-muted">{resourceModal.detail}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm" onClick={() => copyResource(resourceModal)} type="button">
                <MdContentCopy /> 复制内容
              </button>
              <button className="flex h-9 items-center gap-2 rounded-lg bg-teal px-3 text-sm font-semibold text-white" onClick={closeResource} type="button">
                <MdOpenInNew /> 知道了
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg bg-ink px-4 py-3 text-sm text-white shadow-material">
          {toast}
          <button className="grid h-6 w-6 place-items-center rounded hover:bg-white/10" onClick={dismissToast} title="关闭提示" type="button">
            <MdClose size={16} />
          </button>
        </div>
      )}
    </main>
  );
}

function SettingRow({ icon: Icon, label, value }: { icon: IconType; label: string; value: string }) {
  return (
    <div className="grid grid-cols-[22px_1fr_1fr] items-center gap-2 border-b border-line px-3 py-3 last:border-b-0">
      <Icon className="text-muted" />
      <span>{label}</span>
      <span className="truncate text-right text-xs text-muted">{value}</span>
    </div>
  );
}

function JsonPreview({ value, fallback }: { value?: Record<string, unknown>; fallback: string }) {
  if (!value || Object.keys(value).length === 0) {
    return <div className="mt-2 text-muted">{fallback}</div>;
  }
  return <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-muted">{JSON.stringify(value, null, 2)}</pre>;
}

export function HeaderActions() {
  const { busy, currentTask, savedNodes, startTask, toggleSavedNode } = useLearning();
  const nodeId = currentTask?.knowledge_node_id || "node-current";
  const saved = savedNodes.has(nodeId);
  return (
    <div className="flex items-center gap-2">
      <button
        className="flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white shadow-material disabled:opacity-60"
        onClick={() => void startTask(currentTask)}
        disabled={!currentTask || Boolean(busy.startTask)}
        type="button"
      >
        <MdPlayArrow size={20} /> {busy.startTask ? "启动中" : "开始学习"}
      </button>
      <button
        className="grid h-10 w-10 place-items-center rounded-lg border border-line bg-white text-teal"
        onClick={() => toggleSavedNode(nodeId)}
        title={saved ? "取消收藏" : "收藏节点"}
        type="button"
      >
        {saved ? <MdBookmark size={20} /> : <MdBookmarkBorder size={20} />}
      </button>
    </div>
  );
}

export function TaskStatusIcon({ status }: { status: string }) {
  return status === "done" || status === "completed" ? <MdCheckCircle className="text-teal" /> : <MdRadioButtonUnchecked className="text-muted" />;
}

export function ResourceList() {
  const { copyResource, openResource } = useLearning();
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-white">
      {resourceRows.map((row) => {
        const Icon = row.icon;
        const action = row.action === "复制" ? () => copyResource(row) : () => openResource(row);
        return (
          <div key={row.title} className="grid grid-cols-[1fr_120px_90px_64px] items-center border-b border-line px-4 py-3 text-sm last:border-b-0 max-[980px]:grid-cols-[1fr_80px] max-[980px]:gap-2">
            <span className="flex items-center gap-3">
              <Icon className="text-coral" size={20} />
              {row.title}
            </span>
            <span className="text-muted">{row.type}</span>
            <span className="text-muted max-[980px]:hidden">{row.size}</span>
            <button className="text-teal" onClick={action} type="button">
              {row.action}
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function TaskTable({ compact = false }: { compact?: boolean }) {
  const { busy, completeTask, startTask, state } = useLearning();
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-white">
      <div className="grid grid-cols-[1.5fr_0.7fr_0.7fr_0.8fr_0.7fr] border-b border-line bg-[#f8fbfb] px-4 py-3 text-xs font-semibold text-muted max-[980px]:hidden">
        <span>任务</span>
        <span>类型</span>
        <span>预计用时</span>
        <span>状态</span>
        <span>操作</span>
      </div>
      {state.today_tasks.map((task, index) => (
        <div
          key={task.id}
          className={`grid items-center border-b border-line px-4 py-3 text-sm last:border-b-0 ${
            compact ? "grid-cols-[1fr_72px]" : "grid-cols-[1.5fr_0.7fr_0.7fr_0.8fr_0.7fr] max-[980px]:grid-cols-[1fr_72px]"
          }`}
        >
          <span>
            {index + 1}. {task.title}
            <span className="mt-1 block text-xs text-muted">{task.objective}</span>
          </span>
          {!compact && <span className="text-muted max-[980px]:hidden">{task.task_type}</span>}
          {!compact && <span className="text-muted max-[980px]:hidden">{task.estimated_minutes} 分钟</span>}
          {!compact && (
            <span className="flex items-center gap-2 max-[980px]:hidden">
              <TaskStatusIcon status={task.status} />
              {statusText(task.status)}
            </span>
          )}
          <span className="flex items-center gap-2">
            <button
              className="h-8 rounded-lg border border-line px-3 text-xs text-teal hover:border-teal disabled:opacity-60"
              onClick={() => void startTask(task)}
              disabled={Boolean(busy.startTask) || task.status === "completed" || task.status === "done"}
              type="button"
            >
              {task.status === "active" ? "继续" : "开始"}
            </button>
            {!compact && (
              <button
                className="h-8 rounded-lg bg-ink px-3 text-xs font-semibold text-white disabled:opacity-60"
                onClick={() => void completeTask(task)}
                disabled={Boolean(busy.completeTask) || task.status === "completed" || task.status === "done"}
                type="button"
              >
                完成
              </button>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
