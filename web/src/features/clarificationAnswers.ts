export type ClarificationQuestion = {
  id: string;
  question: string;
  required: boolean;
  options: string[];
};

export function clarificationQuestions(value: unknown): ClarificationQuestion[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.flatMap(row => {
    if (!row || typeof row.id !== "string" || !row.id.trim() ||
      typeof row.question !== "string" || !row.question.trim() || seen.has(row.id)) return [];
    seen.add(row.id);
    return [{ id: row.id, question: row.question, required: row.required !== false,
      options: Array.isArray(row.options)
        ? [...new Set<string>(row.options.filter((option: unknown): option is string =>
          typeof option === "string" && !!option.trim()))] : [] }];
  });
}

export function clarificationSubmission(questions: ClarificationQuestion[], draft: Record<string, string>) {
  // Defaults and suggested options never become answers without user input.
  const answers = Object.fromEntries(questions.flatMap(question => {
    const answer = typeof draft[question.id] === "string" ? draft[question.id].trim() : "";
    return answer ? [[question.id, answer]] : [];
  }));
  return { answers, complete: questions.length > 0 && questions.every(question =>
    !question.required || Object.hasOwn(answers, question.id)) };
}
