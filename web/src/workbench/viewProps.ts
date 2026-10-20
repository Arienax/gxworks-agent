export function pickView<T, K extends keyof T>(value: T, fields: readonly K[]): Pick<T, K> {
  return Object.fromEntries(fields.map((field) => [field, value[field]])) as Pick<T, K>;
}
