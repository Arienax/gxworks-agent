// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useCallback, useEffect, useState } from "react";
import type { Locale } from "../i18n";
import { loadJapanese, translate } from "../i18n";

export function usePreferences() {
  const [locale, setLocale] = useState<Locale>(
    () => (localStorage.getItem("gx.locale") as Locale) || "zh-CN",
  );
  const [japaneseRevision, setJapaneseRevision] = useState(0);
  useEffect(() => {
    if (locale !== "ja") return;
    let active = true;
    void loadJapanese().then(() => {
      if (active) setJapaneseRevision((value) => value + 1);
    });
    return () => { active = false; };
  }, [locale]);
  const t = useCallback((s: string) => translate(locale, s), [locale, japaneseRevision]);
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
