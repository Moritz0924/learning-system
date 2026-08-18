"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useMemo, useState } from "react";
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
import { LanguageToggle } from "@/components/language-toggle";
import { useAuth } from "@/components/providers/auth-provider";
import { useLocale } from "@/components/providers/locale-provider";
import { localizeDemoTask, navItems, pathNodes, resourceRows, statusText } from "@/lib/learning-data";
import { shouldNavigateToAiConfig } from "@/lib/ai-config-shortcut.mjs";

export function LearningShell({ children }: { children: ReactNode }) {
  const { t } = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const {
    askTutor,
    activeRunId,
    assessment,
    assessmentMode,
    busy,
    chat,
    skills,
    selectedSkillIds,
    setSelectedSkillIds,
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
    sourceResults,
    status,
    toast,
    toggleSavedNode,
    adjustmentMessage,
    setAdjustmentMessage,
    documents,
    searchOfficialSources,
    adjustment,
    isDemoMode
  } = useLearning();
  const [filterOpen, setFilterOpen] = useState(false);
  const [pathFilter, setPathFilter] = useState<"all" | "active" | "queued">("all");
  const [popover, setPopover] = useState<"notifications" | "profile" | null>(null);

  useEffect(() => {
    const navigate = (event: KeyboardEvent) => {
      if (!shouldNavigateToAiConfig(event)) return;
      event.preventDefault();
      router.push("/ai-config");
    };
    window.addEventListener("keydown", navigate);
    return () => window.removeEventListener("keydown", navigate);
  }, [router]);

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
              {t("shell.productName")}
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
                  title={t(item.labelKey)}
                >
                  <Icon size={22} />
                  <span className="font-medium max-[1380px]:hidden">{t(item.labelKey)}</span>
                </Link>
              );
            })}
          </nav>
          <div className="mt-auto border-t border-line px-5 py-6 text-sm max-[1380px]:hidden">
            <div className="text-xs text-muted">{t("shell.currentPhase")}</div>
            <div className="mt-2 font-semibold">{t("shell.phase3")}</div>
            <div className="mt-1 text-muted">{t("shell.aiBuild")}</div>
            <button className="mt-6 flex items-center gap-2 text-teal" onClick={() => router.push("/path")} type="button">
              {t("shell.switchPhase")} <MdKeyboardArrowRight />
            </button>
          </div>
        </aside>

        <section className="relative border-r border-line bg-white/80 px-5 py-6 max-[840px]:border-b">
          <div className="flex items-center justify-between border-b border-line pb-4">
            <h2 className="text-base font-semibold">{t("shell.learningRoadmap")}</h2>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg hover:bg-[#edf4f3]"
              title={t("shell.filterRoadmap")}
              onClick={() => setFilterOpen((value) => !value)}
              type="button"
            >
              <MdFilterList />
            </button>
          </div>
          {filterOpen && (
            <div className="mt-3 rounded-lg border border-line bg-white p-2 text-xs shadow-material">
              {[
                ["all", t("shell.all")],
                ["active", t("status.active")],
                ["queued", t("shell.queued")]
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
                key={`${node.phaseKey}-${node.titleKey}`}
                className={`group grid w-full grid-cols-[22px_1fr] gap-3 rounded-lg px-2 py-2 text-left transition ${
                  node.titleKey === "path.modelPrompt" ? "bg-[#eaf2ff] text-[#1769d5]" : "hover:bg-[#f1f6f6]"
                }`}
                onClick={() => {
                  notify(t("shell.nodeSelected", { phase: t(node.phaseKey), title: t(node.titleKey) }));
                  router.push(`/path?node=${encodeURIComponent(node.phaseKey)}`);
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
                  <span className="block text-xs font-semibold text-muted">{t(node.phaseKey)}</span>
                  <span className="block text-sm font-semibold">{t(node.titleKey)}</span>
                  <span className={`mt-1 block text-xs ${node.state === "done" ? "text-teal" : "text-muted"}`}>
                    {node.progress > 0 ? t("shell.progressDone", { progress: node.progress }) : t("shell.queued")}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="overflow-y-auto bg-[#fbfdfc] px-7 py-5">
          {isDemoMode && (
            <div data-testid="demo-mode-banner" className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-800">
              {t("shell.demoMode")}
            </div>
          )}
          {children}
        </section>

        <aside className="rightRail overflow-y-auto border-l border-line bg-white px-5 py-5">
          <div className="relative mb-5 flex h-11 items-center justify-between border-b border-line pb-4">
            <div className="flex items-center gap-3 text-sm font-semibold">
              <Image src="/coach-avatar.png" alt={t("shell.coachAlt")} width={34} height={34} className="rounded-full" />
              {t("shell.coach")}
            </div>
            <div className="flex items-center gap-3 text-muted">
              <LanguageToggle />
              <button
                className="grid h-8 w-8 place-items-center rounded-lg hover:bg-[#f1f6f6]"
                onClick={() => setPopover(popover === "notifications" ? null : "notifications")}
                title={t("shell.notifications")}
                type="button"
              >
                <MdNotificationsNone />
              </button>
              <button
                className="grid h-8 w-8 place-items-center rounded-lg hover:bg-[#f1f6f6]"
                onClick={() => setPopover(popover === "profile" ? null : "profile")}
                title={t("shell.account")}
                type="button"
              >
                <MdAccountCircle />
              </button>
            </div>
            {popover && (
              <div className="absolute right-0 top-10 z-20 w-64 rounded-lg border border-line bg-white p-3 text-xs shadow-material">
                {popover === "notifications" ? (
                  <div>
                    <div className="font-semibold text-ink">{t("shell.notifications")}</div>
                    <p className="mt-2 leading-5 text-muted">{t("shell.noNotifications")}</p>
                  </div>
                ) : (
                  <div>
                    <div className="font-semibold text-ink">{user?.display_name}</div>
                    <div className="mt-1 text-muted">{user?.email}</div>
                    <button data-testid="logout" className="mt-3 rounded-lg border border-line px-3 py-2 text-teal" onClick={() => void logout()} type="button">{t("shell.logOut")}</button>
                  </div>
                )}
              </div>
            )}
          </div>

          <section className="rounded-lg border border-line bg-[#fbfdfc] p-4">
            <form onSubmit={askTutor} className="space-y-3">
              <label className="block text-xs font-semibold text-muted" htmlFor="quick-tutor-question">
                {t("shell.askTutor")}
              </label>
              <textarea
                id="quick-tutor-question"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                className="min-h-16 w-full resize-none rounded-lg border border-line bg-white p-3 text-sm outline-none focus:border-teal"
              />
              {skills.length > 0 && (
                <label className="block text-xs font-semibold text-muted">
                  {t("shell.tutorSkills")}
                  <select
                    aria-label={t("shell.quickTutorSkills")}
                    multiple
                    value={selectedSkillIds}
                    onChange={(event) => setSelectedSkillIds(Array.from(event.target.selectedOptions, (option) => option.value))}
                    className="mt-2 min-h-20 w-full rounded-lg border border-line bg-white p-2 text-sm font-normal outline-none focus:border-teal"
                  >
                    {skills.map((skill) => <option key={skill.id} value={skill.id}>{skill.name}</option>)}
                  </select>
                </label>
              )}
              <button
                className="ml-auto flex h-9 items-center gap-2 rounded-lg bg-teal px-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
                type="submit"
                disabled={Boolean(busy.chat) || Boolean(activeRunId)}
              >
                <MdSend /> {busy.chat ? t("shell.sending") : t("shell.send")}
              </button>
            </form>
            <div className="mt-4 border-t border-line pt-4 text-sm leading-7">
              <div className="mb-2 flex flex-wrap gap-2 text-xs font-semibold">
                <span className="rounded-lg border border-line bg-tealSoft px-2 py-1 text-teal">
                  LLM {chat.runtime_metadata?.llm?.mode || t("common.unknown")}
                </span>
                <span className="rounded-lg border border-line bg-amber-50 px-2 py-1 text-amber-700">
                  RAG {t("shell.citations", { count: chat.runtime_metadata?.rag?.citation_count ?? chat.citations.length })}
                </span>
              </div>
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
                {chat.citations.length === 0 && <span className="rounded-lg border border-line px-2 py-1 text-xs font-semibold text-muted">{t("shell.noCitations")}</span>}
              </div>
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">{t("shell.assessment")}</h2>
              <div className="grid grid-cols-3 rounded-lg border border-line text-xs">
                {[
                  ["daily", t("shell.daily")],
                  ["weekly", t("shell.weekly")],
                  ["phase", t("shell.phase")]
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
                  <div className="text-sm font-semibold">{t("shell.assessmentProgress")}</div>
                  <div className="mt-1 text-xs text-muted">{assessment ? t("shell.questionsGenerated", { count: assessment.items.length }) : t("shell.assessmentNotCreated")}</div>
                </div>
                <button
                  className="h-9 rounded-lg border border-teal px-3 text-xs font-semibold text-teal disabled:opacity-60"
                  onClick={createDailyAssessment}
                  disabled={Boolean(busy.assessment)}
                  type="button"
                >
                  {busy.assessment ? t("shell.creating") : t("shell.createAssessment")}
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
                    {t("shell.submitAnswers")}
                  </button>
                </div>
              )}
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-5">
            <h2 className="mb-3 font-semibold">{t("shell.mastery")}</h2>
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
              <h2 className="font-semibold">{t("shell.planAdjustment")}</h2>
              <button
                className="h-9 rounded-lg bg-teal px-3 text-xs font-semibold text-white disabled:opacity-60"
                onClick={requestPlanAdjustment}
                disabled={Boolean(busy.replan)}
                type="button"
              >
                {busy.replan ? t("shell.submitting") : t("shell.submitAdjustment")}
              </button>
            </div>
            <textarea
              value={adjustmentMessage}
              onChange={(event) => setAdjustmentMessage(event.target.value)}
              className="mb-3 min-h-16 w-full resize-none rounded-lg border border-line p-3 text-xs outline-none focus:border-teal"
            />
            <div className="grid grid-cols-[1fr_28px_1fr] gap-3 text-xs">
              <div className="rounded-lg border border-line bg-[#f8fbfb] p-3">
                <div className="font-semibold">{t("shell.beforeAdjustment")}</div>
                <JsonPreview value={adjustment?.before_snapshot} fallback={t("shell.noAdjustmentRecord")} />
              </div>
              <div className="grid place-items-center text-amber-500">
                <MdKeyboardArrowRight size={24} />
              </div>
              <div className="rounded-lg border border-[#f2dc9b] bg-amberSoft p-3">
                <div className="font-semibold">{t("shell.afterAdjustment")}</div>
                <JsonPreview value={adjustment?.after_snapshot} fallback={t("shell.waitingAdjustment")} />
              </div>
            </div>
            {adjustment && (
              <div className="mt-3 space-y-2 rounded-lg border border-line bg-[#fbfdfc] p-3 text-xs">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold">{t("shell.changeSummary")}</div>
                  {adjustment.status === "proposed" && (
                    <button
                      className="h-8 rounded-lg bg-ink px-3 text-xs font-semibold text-white disabled:opacity-60"
                      onClick={applyPlanAdjustment}
                      disabled={Boolean(busy.applyAdjustment)}
                      type="button"
                    >
                      {busy.applyAdjustment ? t("shell.applying") : t("shell.applyAdjustment")}
                    </button>
                  )}
                </div>
                <JsonPreview value={adjustment.change_summary} fallback={t("shell.noChangeSummary")} />
                <div className="font-semibold">{t("shell.adjustmentReason")}</div>
                <JsonPreview value={adjustment.rationale_json} fallback={t("shell.noChangeSummary")} />
              </div>
            )}
          </section>

          <section className="mt-5 border-t border-line pt-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">{t("shell.settingsData")}</h2>
              <button className="flex items-center gap-1 text-xs text-teal" onClick={searchOfficialSources} type="button">
                <MdSearch /> {t("shell.searchOfficial")}
              </button>
            </div>
            <div className="overflow-hidden rounded-lg border border-line bg-white text-sm">
              <SettingRow icon={MdLibraryBooks} label={t("shell.learningLibrary")} value={documents[0]?.parse_status ? t("shell.filesCount", { count: documents.length }) : t("shell.waitingUpload")} />
              <SettingRow icon={MdUploadFile} label={t("shell.uploadStatus")} value={documents[0]?.filename || t("shell.notUploaded")} />
              <SettingRow icon={MdSync} label={t("shell.modelGateway")} value={chat.runtime_metadata?.llm?.mode === "remote" ? t("shell.remoteModel") : t("shell.offlineModel")} />
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
                <div className="text-xs font-semibold text-teal">{t(resourceModal.typeKey)}</div>
                <h2 className="mt-1 text-lg font-semibold">{t(resourceModal.titleKey)}</h2>
              </div>
              <button className="grid h-9 w-9 place-items-center rounded-lg hover:bg-[#f1f6f6]" onClick={closeResource} title={t("common.close")} type="button">
                <MdClose />
              </button>
            </div>
            <p className="mt-4 text-sm leading-7 text-muted">{t(resourceModal.detailKey)}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm" onClick={() => copyResource(resourceModal)} type="button">
                <MdContentCopy /> {t("shell.copyContent")}
              </button>
              <button className="flex h-9 items-center gap-2 rounded-lg bg-teal px-3 text-sm font-semibold text-white" onClick={closeResource} type="button">
                <MdOpenInNew /> {t("shell.gotIt")}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg bg-ink px-4 py-3 text-sm text-white shadow-material">
          {toast}
          <button className="grid h-6 w-6 place-items-center rounded hover:bg-white/10" onClick={dismissToast} title={t("shell.closeNotice")} type="button">
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
  const { t } = useLocale();
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
        <MdPlayArrow size={20} /> {busy.startTask ? t("shell.starting") : t("shell.startLearning")}
      </button>
      <button
        className="grid h-10 w-10 place-items-center rounded-lg border border-line bg-white text-teal"
        onClick={() => toggleSavedNode(nodeId)}
        title={saved ? t("shell.unsave") : t("shell.saveNode")}
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
  const { t } = useLocale();
  const { copyResource, openResource } = useLearning();
  return (
    <div data-testid="task-table" className="overflow-hidden rounded-lg border border-line bg-white">
      {resourceRows.map((row) => {
        const Icon = row.icon;
        const action = row.action === "copy" ? () => copyResource(row) : () => openResource(row);
        return (
          <div key={row.titleKey} className="grid grid-cols-[1fr_120px_90px_64px] items-center border-b border-line px-4 py-3 text-sm last:border-b-0 max-[980px]:grid-cols-[1fr_80px] max-[980px]:gap-2">
            <span className="flex items-center gap-3">
              <Icon className="text-coral" size={20} />
              {t(row.titleKey)}
            </span>
            <span className="text-muted">{t(row.typeKey)}</span>
            <span className="text-muted max-[980px]:hidden">{row.size}</span>
            <button className="text-teal" onClick={action} type="button">
              {t(`resource.${row.action}`)}
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function TaskTable({ compact = false }: { compact?: boolean }) {
  const { t } = useLocale();
  const { busy, completeTask, startTask, state } = useLearning();
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-white">
      <div className="grid grid-cols-[1.5fr_0.7fr_0.7fr_0.8fr_0.7fr] border-b border-line bg-[#f8fbfb] px-4 py-3 text-xs font-semibold text-muted max-[980px]:hidden">
        <span>{t("shell.task")}</span>
        <span>{t("shell.type")}</span>
        <span>{t("shell.estimatedTime")}</span>
        <span>{t("shell.state")}</span>
        <span>{t("shell.action")}</span>
      </div>
      {state.today_tasks.length === 0 && (
        <div data-testid="empty-task-list" className="px-4 py-8 text-center text-sm text-muted">
          {t("shell.noTasks")}
        </div>
      )}
      {state.today_tasks.map((task, index) => {
        const displayTask = localizeDemoTask(task, t);
        return (
        <div
          data-testid="task-row"
          key={task.id}
          className={`grid items-center border-b border-line px-4 py-3 text-sm last:border-b-0 ${
            compact ? "grid-cols-[1fr_72px]" : "grid-cols-[1.5fr_0.7fr_0.7fr_0.8fr_0.7fr] max-[980px]:grid-cols-[1fr_72px]"
          }`}
        >
          <span>
            {index + 1}. {displayTask.title}
            <span className="mt-1 block text-xs text-muted">{displayTask.objective}</span>
          </span>
          {!compact && <span className="text-muted max-[980px]:hidden">{displayTask.task_type}</span>}
          {!compact && <span className="text-muted max-[980px]:hidden">{t("shell.minutes", { count: task.estimated_minutes })}</span>}
          {!compact && (
            <span className="flex items-center gap-2 max-[980px]:hidden">
              <TaskStatusIcon status={task.status} />
              {statusText(task.status, t)}
            </span>
          )}
          <span className="flex items-center gap-2">
            <button
              className="h-8 rounded-lg border border-line px-3 text-xs text-teal hover:border-teal disabled:opacity-60"
              onClick={() => void startTask(task)}
              disabled={Boolean(busy.startTask) || task.status === "completed" || task.status === "done"}
              type="button"
            >
              {task.status === "active" ? t("shell.continue") : t("shell.startLearning")}
            </button>
            {!compact && (
              <button
                className="h-8 rounded-lg bg-ink px-3 text-xs font-semibold text-white disabled:opacity-60"
                onClick={() => void completeTask(task)}
                disabled={Boolean(busy.completeTask) || task.status === "completed" || task.status === "done"}
                type="button"
              >
                {t("shell.complete")}
              </button>
            )}
          </span>
        </div>
        );
      })}
    </div>
  );
}
