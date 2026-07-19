import type { DiagnosticQuestion } from "./types";


type Props = {
  questions: DiagnosticQuestion[];
  answers: Record<string, string>;
  onAnswer: (questionId: string, optionId: string) => void;
};


export function KnowledgeQuestionForm({ questions, answers, onAnswer }: Props) {
  return (
    <div className="divide-y divide-line">
      {questions.map((question, questionIndex) => (
        <fieldset data-testid="knowledge-question" key={question.question_id} className="py-6 first:pt-0 last:pb-0">
          <legend className="text-sm font-semibold leading-6">
            <span className="mr-2 text-teal">{String(questionIndex + 1).padStart(2, "0")}</span>
            {question.prompt}
          </legend>
          <div className="mt-3 grid gap-2">
            {question.options.map((option) => (
              <label
                key={option.option_id}
                className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-3 text-sm transition ${
                  answers[question.question_id] === option.option_id
                    ? "border-teal bg-tealSoft"
                    : "border-line bg-white hover:border-teal/50"
                }`}
              >
                <input
                  type="radio"
                  name={question.question_id}
                  value={option.option_id}
                  checked={answers[question.question_id] === option.option_id}
                  onChange={() => onAnswer(question.question_id, option.option_id)}
                  className="h-4 w-4 accent-teal"
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
