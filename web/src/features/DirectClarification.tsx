import type { ClarificationQuestion } from "./clarificationAnswers";

export function DirectClarification({ questions, answers, onChange, disabled, t }: {
  questions: ClarificationQuestion[];
  answers: Record<string, string>;
  onChange: (id: string, value: string) => void;
  disabled: boolean;
  t: (key: string) => string;
}) {
  return <div className="direct-clarification">
    <p>{t("请逐项回答，填写完成后一起提交。")}</p>
    {questions.map((question, index) => {
      const answer = typeof answers[question.id] === "string" ? answers[question.id] : "";
      return <fieldset className="clarification-question" key={question.id} disabled={disabled}>
        <legend>{index + 1}. {question.question} <span className="muted">{t(question.required ? "必填" : "选填")}</span></legend>
        {!!question.options.length && <div className="clarification-options">
          {question.options.map(option => <button type="button" key={option}
            aria-pressed={answer === option} onClick={() => onChange(question.id, option)}>{option}</button>)}
        </div>}
        <textarea aria-label={question.question} required={question.required} rows={2}
          placeholder={t("填写答案，也可以选择上面的选项后修改。")}
          value={answer} onChange={event => onChange(question.id, event.target.value)} />
      </fieldset>;
    })}
  </div>;
}
