import type { ExplanationMode } from "./types";
import { useLocale } from "@/components/providers/locale-provider";


type GoalStepProps = {
  title: string;
  targetOutcome: string;
  deadline: string;
  onTitleChange: (value: string) => void;
  onTargetOutcomeChange: (value: string) => void;
  onDeadlineChange: (value: string) => void;
};

type PreferencesStepProps = {
  weeklyHours: string;
  explanationOrder: ExplanationMode[];
  preferredSessionMinutes: string;
  codeFirst: boolean;
  onWeeklyHoursChange: (value: string) => void;
  onToggleExplanationMode: (value: ExplanationMode) => void;
  onPreferredSessionMinutesChange: (value: string) => void;
  onCodeFirstChange: (value: boolean) => void;
};

const explanationModes: Array<{ value: ExplanationMode; labelKey: string; descriptionKey: string }> = [
  { value: "analogy", labelKey: "onboarding.analogy", descriptionKey: "onboarding.analogyHelp" },
  { value: "definition", labelKey: "onboarding.definition", descriptionKey: "onboarding.definitionHelp" },
  { value: "principle", labelKey: "onboarding.principle", descriptionKey: "onboarding.principleHelp" },
  { value: "engineering", labelKey: "onboarding.engineering", descriptionKey: "onboarding.engineeringHelp" },
];

const inputClass =
  "mt-2 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/10";


export function GoalForm({
  title,
  targetOutcome,
  deadline,
  onTitleChange,
  onTargetOutcomeChange,
  onDeadlineChange,
}: GoalStepProps) {
  const { t } = useLocale();
  return (
    <div className="grid gap-5">
      <label className="text-sm font-medium">
        {t("onboarding.goal")}
        <input
          data-testid="goal-title"
          className={`${inputClass} h-11`}
          value={title}
          onChange={(event) => onTitleChange(event.target.value)}
          placeholder={t("onboarding.goalPlaceholder")}
          maxLength={120}
        />
      </label>
      <label className="text-sm font-medium">
        {t("onboarding.outcome")}
        <textarea
          data-testid="target-outcome"
          className={`${inputClass} min-h-28 resize-none py-3 leading-6`}
          value={targetOutcome}
          onChange={(event) => onTargetOutcomeChange(event.target.value)}
          placeholder={t("onboarding.outcomePlaceholder")}
          maxLength={1000}
        />
      </label>
      <label className="max-w-xs text-sm font-medium">
        {t("onboarding.deadline")}
        <input
          data-testid="goal-deadline"
          className={`${inputClass} h-11`}
          type="date"
          value={deadline}
          onChange={(event) => onDeadlineChange(event.target.value)}
        />
      </label>
    </div>
  );
}


export function LearningPreferencesForm({
  weeklyHours,
  explanationOrder,
  preferredSessionMinutes,
  codeFirst,
  onWeeklyHoursChange,
  onToggleExplanationMode,
  onPreferredSessionMinutesChange,
  onCodeFirstChange,
}: PreferencesStepProps) {
  const { t } = useLocale();
  return (
    <div className="grid gap-7">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm font-medium">
          {t("onboarding.weeklyHours")}
          <input
            data-testid="weekly-hours"
            className={`${inputClass} h-11`}
            type="number"
            min={1}
            max={60}
            value={weeklyHours}
            onChange={(event) => onWeeklyHoursChange(event.target.value)}
            placeholder="1–60"
          />
        </label>
        <label className="text-sm font-medium">
          {t("onboarding.sessionMinutes")}
          <input
            data-testid="preferred-session-minutes"
            className={`${inputClass} h-11`}
            type="number"
            min={15}
            max={180}
            value={preferredSessionMinutes}
            onChange={(event) => onPreferredSessionMinutesChange(event.target.value)}
            placeholder="15–180"
          />
        </label>
      </div>

      <fieldset>
        <legend className="text-sm font-medium">{t("onboarding.explanationOrder")}</legend>
        <p className="mt-1 text-xs leading-5 text-muted">{t("onboarding.explanationOrderHelp")}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {explanationModes.map((mode) => {
            const selectedIndex = explanationOrder.indexOf(mode.value);
            return (
              <button
                key={mode.value}
                data-testid={`preference-${mode.value}`}
                type="button"
                aria-pressed={selectedIndex >= 0}
                onClick={() => onToggleExplanationMode(mode.value)}
                className={`flex items-center gap-3 rounded-lg border px-3 py-3 text-left transition ${
                  selectedIndex >= 0
                    ? "border-teal bg-tealSoft text-ink"
                    : "border-line bg-white hover:border-teal/50"
                }`}
              >
                <span className={`grid h-7 w-7 place-items-center rounded-full text-xs font-semibold ${selectedIndex >= 0 ? "bg-teal text-white" : "bg-[#edf3f3] text-muted"}`}>
                  {selectedIndex >= 0 ? selectedIndex + 1 : "·"}
                </span>
                <span>
                  <span className="block text-sm font-semibold">{t(mode.labelKey)}</span>
                  <span className="mt-0.5 block text-xs text-muted">{t(mode.descriptionKey)}</span>
                </span>
              </button>
            );
          })}
        </div>
      </fieldset>

      <label className="flex items-center gap-3 rounded-lg border border-line bg-white px-4 py-3 text-sm">
        <input
          data-testid="code-first"
          type="checkbox"
          checked={codeFirst}
          onChange={(event) => onCodeFirstChange(event.target.checked)}
          className="h-4 w-4 accent-teal"
        />
        <span>
          <span className="block font-semibold">{t("onboarding.codeFirst")}</span>
          <span className="mt-0.5 block text-xs text-muted">{t("onboarding.codeFirstHelp")}</span>
        </span>
      </label>
    </div>
  );
}
