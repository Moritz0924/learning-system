"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { MdArrowBack, MdArrowForward, MdCheck } from "react-icons/md";

import { useLocale } from "@/components/providers/locale-provider";
import { ApiError } from "@/lib/api";

import { GoalForm, LearningPreferencesForm } from "./goal-form";
import { KnowledgeQuestionForm } from "./knowledge-question-form";
import { createDynamicDiagnosticDraft } from "./onboarding-api";
import type {
  DynamicDiagnosticDraftResponse,
  ExplanationMode,
  GoalInitializationInput,
  InitializeFromDraftRequest,
} from "./types";


type Props = {
  busy: boolean;
  onInitialize: (request: InitializeFromDraftRequest) => Promise<boolean>;
};

const stepLabelKeys = ["onboarding.goal", "onboarding.timePreferences", "onboarding.dynamicTitle"];


export function DiagnosisForm({ busy, onInitialize }: Props) {
  const { locale, t } = useLocale();
  const [step, setStep] = useState(0);
  const [formError, setFormError] = useState("");
  const [showConfigAction, setShowConfigAction] = useState(false);
  const [showRetryAction, setShowRetryAction] = useState(false);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draft, setDraft] = useState<DynamicDiagnosticDraftResponse | null>(null);
  const [title, setTitle] = useState("");
  const [targetOutcome, setTargetOutcome] = useState("");
  const [deadline, setDeadline] = useState("");
  const [weeklyHours, setWeeklyHours] = useState("");
  const [explanationOrder, setExplanationOrder] = useState<ExplanationMode[]>([]);
  const [preferredSessionMinutes, setPreferredSessionMinutes] = useState("");
  const [codeFirst, setCodeFirst] = useState(false);
  const [knowledgeAnswers, setKnowledgeAnswers] = useState<Record<string, string>>({});
  const draftRequestIdRef = useRef<string | null>(null);
  const initializeRequestIdRef = useRef<string | null>(null);

  const invalidateDraft = () => {
    if (!draft && !draftRequestIdRef.current) return;
    setDraft(null);
    setKnowledgeAnswers({});
    draftRequestIdRef.current = null;
    initializeRequestIdRef.current = null;
  };

  const validateStep = (targetStep: number) => {
    if (targetStep === 0 && (title.trim().length < 2 || targetOutcome.trim().length < 10)) {
      setFormError(t("onboarding.goalInvalid"));
      return false;
    }
    if (targetStep === 1) {
      const hours = Number(weeklyHours);
      const minutes = Number(preferredSessionMinutes);
      if (!Number.isInteger(hours) || hours < 1 || hours > 60) {
        setFormError(t("onboarding.weeklyInvalid"));
        return false;
      }
      if (!Number.isInteger(minutes) || minutes < 15 || minutes > 180) {
        setFormError(t("onboarding.sessionInvalid"));
        return false;
      }
      if (explanationOrder.length === 0) {
        setFormError(t("onboarding.explanationInvalid"));
        return false;
      }
    }
    if (targetStep === 2 && (!draft || draft.questions.some((question) => !knowledgeAnswers[question.question_id]))) {
      setFormError(t("onboarding.answersInvalid"));
      return false;
    }
    setFormError("");
    setShowConfigAction(false);
    setShowRetryAction(false);
    return true;
  };

  const showDynamicFailure = (error: unknown, allowRetryAction: boolean) => {
    setShowConfigAction(false);
    setShowRetryAction(false);
    if (error instanceof ApiError) {
      if (error.status === 401 || error.code?.startsWith("auth.")) {
        setFormError(t("onboarding.authRequired"));
        return;
      }
      if (error.code === "onboarding.dynamic_configuration_invalid") {
        setFormError(t("onboarding.dynamicConfigurationInvalid"));
        setShowConfigAction(true);
        return;
      }
      if (error.code === "onboarding.dynamic_provider_unavailable") {
        setFormError(t("onboarding.dynamicProviderUnavailable"));
        setShowRetryAction(allowRetryAction);
        return;
      }
      if (error.code === "onboarding.dynamic_output_invalid") {
        setFormError(t("onboarding.dynamicOutputInvalid"));
        setShowRetryAction(allowRetryAction);
        return;
      }
      if (error.code === "onboarding.dynamic_roadmap_infeasible") {
        setFormError(t("onboarding.dynamicRoadmapInfeasible"));
        setShowRetryAction(allowRetryAction);
        return;
      }
      if (error.code === "onboarding.draft_expired") {
        setDraft(null);
        setKnowledgeAnswers({});
        draftRequestIdRef.current = null;
        initializeRequestIdRef.current = null;
        setStep(1);
        setFormError(t("onboarding.draftExpired"));
        setShowRetryAction(true);
        return;
      }
    }
    setFormError(t("onboarding.dynamicUnavailable"));
    setShowRetryAction(allowRetryAction);
  };

  const goalInput = (): GoalInitializationInput => ({
    title: title.trim(),
    target_outcome: targetOutcome.trim(),
    deadline: deadline || null,
    weekly_hours_target: Number(weeklyHours),
    learning_preferences: {
      explanation_order: explanationOrder,
      preferred_session_minutes: Number(preferredSessionMinutes),
      code_first: codeFirst,
    },
  });

  const generateDraft = async () => {
    if (draftBusy || busy || !validateStep(1)) return;
    if (!draftRequestIdRef.current) draftRequestIdRef.current = crypto.randomUUID();
    setDraftBusy(true);
    setFormError("");
    setShowConfigAction(false);
    setShowRetryAction(false);
    try {
      const loaded = await createDynamicDiagnosticDraft({
        request_id: draftRequestIdRef.current,
        locale,
        goal: goalInput(),
      });
      setDraft(loaded);
      setStep(2);
    } catch (error) {
      showDynamicFailure(error, true);
    } finally {
      setDraftBusy(false);
    }
  };

  const nextStep = async () => {
    if (!validateStep(step)) return;
    if (step === 0) {
      setStep(1);
      return;
    }
    await generateDraft();
  };

  const toggleExplanationMode = (mode: ExplanationMode) => {
    invalidateDraft();
    setExplanationOrder((current) =>
      current.includes(mode) ? current.filter((item) => item !== mode) : [...current, mode]
    );
  };

  const submit = async () => {
    if (!draft || busy || !validateStep(2)) return;
    if (!initializeRequestIdRef.current) initializeRequestIdRef.current = crypto.randomUUID();
    try {
      const succeeded = await onInitialize({
        request_id: initializeRequestIdRef.current,
        draft_id: draft.draft_id,
        knowledge_answers: draft.questions.map((question) => ({
          question_id: question.question_id,
          selected_option_id: knowledgeAnswers[question.question_id],
        })),
      });
      if (!succeeded) {
        setFormError(t("onboarding.submitFailed"));
        setShowConfigAction(false);
      }
    } catch (error) {
      showDynamicFailure(error, true);
    }
  };

  return (
    <section data-testid="diagnosis-form-ready" className="border-t border-line pt-5">
      <ol className="grid grid-cols-3 gap-2 border-b border-line pb-5" aria-label={t("onboarding.progress")}>
        {stepLabelKeys.map((labelKey, index) => (
          <li key={labelKey} className="min-w-0">
            <div className="flex items-center gap-2">
              <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold ${
                index < step ? "bg-teal text-white" : index === step ? "bg-ink text-white" : "bg-[#e9efef] text-muted"
              }`}>
                {index < step ? <MdCheck /> : index + 1}
              </span>
              <span className={`truncate text-xs font-semibold ${index === step ? "text-ink" : "text-muted"}`}>
                {t(labelKey)}
              </span>
            </div>
          </li>
        ))}
      </ol>

      <div className="py-6">
        <div className="mb-5">
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            {t("onboarding.step", { current: step + 1, total: stepLabelKeys.length })}
          </div>
          <h2 className="mt-2 text-xl font-semibold">{step === 2 && draft ? draft.title : t(stepLabelKeys[step])}</h2>
          <p className="mt-2 text-sm leading-6 text-muted">
            {step === 0 && t("onboarding.step0Help")}
            {step === 1 && t("onboarding.step1Help")}
            {step === 2 && t("onboarding.step3Help")}
          </p>
        </div>

        {step === 0 && (
          <GoalForm
            title={title}
            targetOutcome={targetOutcome}
            deadline={deadline}
            onTitleChange={(value) => { invalidateDraft(); setTitle(value); }}
            onTargetOutcomeChange={(value) => { invalidateDraft(); setTargetOutcome(value); }}
            onDeadlineChange={(value) => { invalidateDraft(); setDeadline(value); }}
          />
        )}
        {step === 1 && (
          <LearningPreferencesForm
            weeklyHours={weeklyHours}
            explanationOrder={explanationOrder}
            preferredSessionMinutes={preferredSessionMinutes}
            codeFirst={codeFirst}
            onWeeklyHoursChange={(value) => { invalidateDraft(); setWeeklyHours(value); }}
            onToggleExplanationMode={toggleExplanationMode}
            onPreferredSessionMinutesChange={(value) => { invalidateDraft(); setPreferredSessionMinutes(value); }}
            onCodeFirstChange={(value) => { invalidateDraft(); setCodeFirst(value); }}
          />
        )}
        {step === 2 && draft && (
          <div data-testid="dynamic-diagnostic-ready">
            <KnowledgeQuestionForm
              questions={draft.questions}
              answers={knowledgeAnswers}
              onAnswer={(questionId, optionId) => {
                initializeRequestIdRef.current = null;
                setKnowledgeAnswers((current) => ({ ...current, [questionId]: optionId }));
              }}
            />
          </div>
        )}
        {draftBusy && (
          <div className="border-y border-line py-8" aria-live="polite">
            <div className="mx-auto h-1.5 max-w-sm overflow-hidden rounded-full bg-[#e2ebec]">
              <span className="block h-full w-2/5 animate-pulse rounded-full bg-teal" />
            </div>
            <p className="mt-4 text-center text-sm text-muted">{t("onboarding.generatingQuestions")}</p>
          </div>
        )}
      </div>

      {formError && (
        <div role="alert" className="mb-4 border-l-2 border-coral bg-[#fff6f3] px-3 py-3 text-sm text-coral">
          <p>{formError}</p>
          {showConfigAction && (
            <Link href="/ai-config" className="mt-2 inline-flex font-semibold underline underline-offset-4">
              {t("onboarding.openAiConfig")}
            </Link>
          )}
          {showRetryAction && (
            <button
              type="button"
              onClick={() => void (draft ? submit() : generateDraft())}
              className="mt-2 inline-flex font-semibold underline underline-offset-4"
            >
              {t("onboarding.retryQuestions")}
            </button>
          )}
        </div>
      )}

      <div className="flex items-center justify-between border-t border-line pt-5">
        <button
          data-testid="diagnosis-previous"
          type="button"
          onClick={() => {
            setFormError("");
            setShowConfigAction(false);
            setShowRetryAction(false);
            setStep((current) => Math.max(0, current - 1));
          }}
          disabled={step === 0 || busy || draftBusy}
          className="inline-flex h-10 items-center gap-2 rounded-lg border border-line px-4 text-sm font-semibold text-muted disabled:cursor-not-allowed disabled:opacity-40"
        >
          <MdArrowBack /> {t("onboarding.previous")}
        </button>
        {step < stepLabelKeys.length - 1 ? (
          <button
            data-testid="diagnosis-next"
            type="button"
            onClick={() => void nextStep()}
            disabled={busy || draftBusy}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-ink px-4 text-sm font-semibold text-white disabled:opacity-60"
          >
            {draftBusy ? t("onboarding.generatingQuestions") : t("onboarding.next")} <MdArrowForward />
          </button>
        ) : (
          <button
            data-testid="create-learning-path"
            type="button"
            onClick={() => void submit()}
            disabled={busy}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-teal px-4 text-sm font-semibold text-white shadow-material disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? t("shell.creating") : t("page.generatePath")} <MdArrowForward />
          </button>
        )}
      </div>
    </section>
  );
}
