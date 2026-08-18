import type { SelfAssessmentDimension } from "./types";
import { useLocale } from "@/components/providers/locale-provider";


type Props = {
  dimensions: SelfAssessmentDimension[];
  answers: Record<string, number>;
  onAnswer: (dimensionCode: string, level: number) => void;
};


export function SelfAssessmentForm({ dimensions, answers, onAnswer }: Props) {
  const { t } = useLocale();
  return (
    <div className="divide-y divide-line">
      {dimensions.map((dimension) => (
        <fieldset key={dimension.code} className="grid gap-4 py-5 first:pt-0 last:pb-0 lg:grid-cols-[minmax(180px,1fr)_minmax(260px,1.4fr)] lg:items-center">
          <legend className="sr-only">{dimension.title}</legend>
          <div>
            <div className="text-sm font-semibold">{dimension.title}</div>
            <p className="mt-1 text-xs leading-5 text-muted">{dimension.description}</p>
          </div>
          <div className="grid grid-cols-5 gap-2">
            {Array.from(
              { length: dimension.maximum - dimension.minimum + 1 },
              (_, index) => dimension.minimum + index
            ).map((level) => {
              const selected = answers[dimension.code] === level;
              return (
                <button
                  key={level}
                  data-testid={`self-level-${dimension.code}-${level}`}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onAnswer(dimension.code, level)}
                  className={`h-11 rounded-lg border text-sm font-semibold transition ${
                    selected
                      ? "border-teal bg-teal text-white"
                      : "border-line bg-white text-muted hover:border-teal hover:text-teal"
                  }`}
                >
                  {level}
                </button>
              );
            })}
          </div>
        </fieldset>
      ))}
      <div className="flex justify-between pt-3 text-[11px] text-muted">
        <span>{t("onboarding.noExperience")}</span>
        <span>{t("onboarding.independent")}</span>
      </div>
    </div>
  );
}
