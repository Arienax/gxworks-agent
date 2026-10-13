import { useId } from "react";

/** Show the choices immediately; an answer may still be entered freely. */
export function ChoiceInput({ label, value, options, onChange, required, hint, error, t }: {
  label: string; value: string; options: string[];
  onChange: (value: string) => void; required: boolean;
  hint?: string; error?: string; t: (key: string) => string;
}) {
  const id = useId();
  return (
    <div className="spec-question">
      <label id={`${id}-label`} htmlFor={id}>{label}{required && <span className="required-mark">{t("必填")}</span>}</label>
      {!!options.length && <div role="group" aria-labelledby={`${id}-label`} className="choice-buttons">
        {[...new Set(options)].map((option) => <button type="button" key={option}
          aria-pressed={value === option} onClick={() => onChange(option)}>{option}</button>)}
      </div>}
      <div className="choice-input">
        <input id={id}
          aria-required={required} aria-invalid={!!error}
          aria-describedby={hint || error ? `${id}-hint` : undefined}
          placeholder={options.length ? t("也可以自行填写") : t("请填写")}
          value={value} onChange={(event) => onChange(event.target.value)} />
      </div>
      {(hint || error) && <p id={`${id}-hint`} className={error ? "error-text" : "muted"} role={error ? "alert" : undefined}>{error || hint}</p>}
    </div>
  );
}
