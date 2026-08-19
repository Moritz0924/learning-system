"use client";

import { useEffect, useRef, useState } from "react";
import { MdArrowBack, MdArrowForward, MdCheck, MdRefresh } from "react-icons/md";

import { GoalForm, LearningPreferencesForm } from "./goal-form";
import { KnowledgeQuestionForm } from "./knowledge-question-form";
import { loadDiagnosticTemplate } from "./onboarding-api";
import { SelfAssessmentForm } from "./self-assessment-form";
import { useLocale } from "@/components/providers/locale-provider";
import type {
  DiagnosticTemplateResponse,
  ExplanationMode,
  OnboardingInitializeRequest,
} from "./types";


type Props = {
  busy: boolean;
  onInitialize: (request: OnboardingInitializeRequest) => Promise<boolean>;
};

const stepLabelKeys = ["onboarding.goal", "onboarding.timePreferences", "onboarding.selfAssessment", "onboarding.knowledgeDiagnosis"];


export function DiagnosisForm({ busy, onInitialize }: Props) {
  const { locale, t } = useLocale();
  const [template, setTemplate] = useState<DiagnosticTemplateResponse | null>(null);
  const [templateError, setTemplateError] = useState("");
  const [reloadSequence, setReloadSequence] = useState(0);
  const [step, setStep] = useState(0);
  const [formError, setFormError] = useState("");
  const [title, setTitle] = useState("");
  const [targetOutcome, setTargetOutcome] = useState("");
  const [deadline, setDeadline] = useState("");
  const [weeklyHours, setWeeklyHours] = useState("");
  const [explanationOrder, setExplanationOrder] = useState<ExplanationMode[]>([]);
  const [preferredSessionMinutes, setPreferredSessionMinutes] = useState("");
  const [codeFirst, setCodeFirst] = useState(false);
  const [selfAnswers, setSelfAnswers] = useState<Record<string, number>>({});
  const [knowledgeAnswers, setKnowledgeAnswers] = useState<Record<string, string>>({});
  const requestIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadDiagnosticTemplate("ai_app_dev")
      .then((loaded) => {
        if (!cancelled) setTemplate(loaded);
      })
      .catch((error) => {
        if (!cancelled) {
          setTemplateError(error instanceof Error ? error.message : t("onboarding.loadFailed"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reloadSequence, t]);

  const validateStep = (targetStep: number) => {
    if (!template) return false;
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
    if (
      targetStep === 2 &&
      template.self_assessment_dimensions.some(
        (dimension) => selfAnswers[dimension.code] === undefined
      )
    ) {
      setFormError(t("onboarding.selfInvalid"));
      return false;
    }
    if (
      targetStep === 3 &&
      template.questions.some((question) => !knowledgeAnswers[question.question_id])
    ) {
      setFormError(t("onboarding.answersInvalid"));
      return false;
    }
    setFormError("");
    return true;
  };

  const nextStep = () => {
    if (validateStep(step)) setStep((current) => Math.min(stepLabelKeys.length - 1, current + 1));
  };

  const toggleExplanationMode = (mode: ExplanationMode) => {
    setExplanationOrder((current) =>
      current.includes(mode) ? current.filter((item) => item !== mode) : [...current, mode]
    );
  };

  const submit = async () => {
    if (!template || busy || !validateStep(3)) return;
    if (!requestIdRef.current) requestIdRef.current = crypto.randomUUID();
    const request: OnboardingInitializeRequest = {
      request_id: requestIdRef.current,
      template_version: template.template_version,
      locale,
      goal: {
        title: title.trim(),
        target_outcome: targetOutcome.trim(),
        deadline: deadline || null,
        weekly_hours_target: Number(weeklyHours),
        learning_preferences: {
          explanation_order: explanationOrder,
          preferred_session_minutes: Number(preferredSessionMinutes),
          code_first: codeFirst,
        },
      },
      self_assessment_answers: template.self_assessment_dimensions.map((dimension) => ({
        dimension_code: dimension.code,
        level: selfAnswers[dimension.code],
      })),
      knowledge_answers: template.questions.map((question) => ({
        question_id: question.question_id,
        selected_option_id: knowledgeAnswers[question.question_id],
      })),
    };
    const succeeded = await onInitialize(request);
    if (!succeeded) setFormError(t("onboarding.submitFailed"));
  };

  if (templateError) {
    return (
      <section className="border-y border-line py-10 text-center">
        <h2 className="font-semibold">{t("onboarding.unavailable")}</h2>
        <p className="mt-2 text-sm text-muted">{templateError}</p>
        <button
          type="button"
          onClick={() => {
            setTemplateError("");
            setTemplate(null);
            setReloadSequence((current) => current + 1);
          }}
          className="mt-5 inline-flex h-10 items-center gap-2 rounded-lg border border-teal px-4 text-sm font-semibold text-teal"
        >
          <MdRefresh /> {t("onboarding.reload")}
        </button>
      </section>
    );
  }

  if (!template) {
    return (
      <section className="border-y border-line py-12" aria-live="polite">
        <div className="mx-auto h-1.5 max-w-sm overflow-hidden rounded-full bg-[#e2ebec]">
          <span className="block h-full w-2/5 animate-pulse rounded-full bg-teal" />
        </div>
        <p className="mt-4 text-center text-sm text-muted">{t("onboarding.loading")}</p>
      </section>
    );
  }

  return (
    <section data-testid="diagnosis-template-ready" className="border-t border-line pt-5">
      <ol className="grid grid-cols-4 gap-2 border-b border-line pb-5" aria-label={t("onboarding.progress")}>
        {stepLabelKeys.map((labelKey, index) => (
          <li key={labelKey} className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold transition ${
                  index < step
                    ? "bg-teal text-white"
                    : index === step
                      ? "bg-ink text-white"
                      : "bg-[#e9efef] text-muted"
                }`}
              >
                {index < step ? <MdCheck /> : index + 1}
              </span>
              <span className={`truncate text-xs font-semibold ${index === step ? "text-ink" : "text-muted"}`}>
                {t(labelKey)}
              </span>
            </div>
          </li>
        ))}
      </ol>

      <div className="py-6 transition-opacity duration-200">
        <div className="mb-5">
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            {t("onboarding.step", { current: step + 1, total: stepLabelKeys.length })}
          </div>
          <h2 className="mt-2 text-xl font-semibold">{t(stepLabelKeys[step])}</h2>
          <p className="mt-2 text-sm leading-6 text-muted">
            {step === 0 && t("onboarding.step0Help")}
            {step === 1 && t("onboarding.step1Help")}
            {step === 2 && t("onboarding.step2Help")}
            {step === 3 && t("onboarding.step3Help")}
          </p>
        </div>

        {step === 0 && (
          <GoalForm
            title={title}
            targetOutcome={targetOutcome}
            deadline={deadline}
            onTitleChange={setTitle}
            onTargetOutcomeChange={setTargetOutcome}
            onDeadlineChange={setDeadline}
          />
        )}
        {step === 1 && (
          <LearningPreferencesForm
            weeklyHours={weeklyHours}
            explanationOrder={explanationOrder}
            preferredSessionMinutes={preferredSessionMinutes}
            codeFirst={codeFirst}
            onWeeklyHoursChange={setWeeklyHours}
            onToggleExplanationMode={toggleExplanationMode}
            onPreferredSessionMinutesChange={setPreferredSessionMinutes}
            onCodeFirstChange={setCodeFirst}
          />
        )}
        {step === 2 && (
          <SelfAssessmentForm
            dimensions={template.self_assessment_dimensions}
            answers={selfAnswers}
            onAnswer={(dimensionCode, level) =>
              setSelfAnswers((current) => ({ ...current, [dimensionCode]: level }))
            }
          />
        )}
        {step === 3 && (
          <KnowledgeQuestionForm
            questions={template.questions}
            answers={knowledgeAnswers}
            onAnswer={(questionId, optionId) =>
              setKnowledgeAnswers((current) => ({ ...current, [questionId]: optionId }))
            }
          />
        )}
      </div>

      {formError && (
        <div role="alert" className="mb-4 border-l-2 border-coral bg-[#fff6f3] px-3 py-2 text-sm text-coral">
          {formError}
        </div>
      )}

      <div className="flex items-center justify-between border-t border-line pt-5">
        <button
          data-testid="diagnosis-previous"
          type="button"
          onClick={() => {
            setFormError("");
            setStep((current) => Math.max(0, current - 1));
          }}
          disabled={step === 0 || busy}
          className="inline-flex h-10 items-center gap-2 rounded-lg border border-line px-4 text-sm font-semibold text-muted disabled:cursor-not-allowed disabled:opacity-40"
        >
          <MdArrowBack /> {t("onboarding.previous")}
        </button>
        {step < stepLabelKeys.length - 1 ? (
          <button
            data-testid="diagnosis-next"
            type="button"
            onClick={nextStep}
            disabled={busy}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-ink px-4 text-sm font-semibold text-white disabled:opacity-60"
          >
            {t("onboarding.next")} <MdArrowForward />
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
