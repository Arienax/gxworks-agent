import { useCallback, useEffect, useState } from "react";
import type { Locale } from "../i18n";
import { translate } from "../i18n";

export function usePreferences() {
  const [locale, setLocale] = useState<Locale>(
    () => (localStorage.getItem("gx.locale") as Locale) || "zh-CN",
  );
  const t = useCallback((s: string) => translate(locale, s), [locale]);
  const [constructionExamples, setConstructionExamples] = useState(
    () => localStorage.getItem("gx.developer.constructionExamples") === "1",
  );
  const [freshConfirmedGeneration, setFreshConfirmedGeneration] = useState(
    () => localStorage.getItem("gx.developer.freshConfirmedGeneration") === "1",
  );
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    localStorage.getItem("gx.theme") === "light" ? "light" : "dark",
  );
  useEffect(() => {
    localStorage.setItem("gx.locale", locale);
    document.documentElement.lang = locale;
  }, [locale]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("gx.theme", theme);
  }, [theme]);
  useEffect(() => {
    localStorage.setItem("gx.developer.constructionExamples", constructionExamples ? "1" : "0");
  }, [constructionExamples]);
  useEffect(() => {
    localStorage.setItem(
      "gx.developer.freshConfirmedGeneration",
      freshConfirmedGeneration ? "1" : "0",
    );
  }, [freshConfirmedGeneration]);

  return {
    locale,
    setLocale,
    t,
    theme,
    setTheme,
    constructionExamples,
    setConstructionExamples,
    freshConfirmedGeneration,
    setFreshConfirmedGeneration,
  };
}
