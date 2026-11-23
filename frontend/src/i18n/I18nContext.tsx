import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { LANGUAGE_STORAGE_KEY, translateUiText, type Language } from "./translations";

type I18nContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  toggleLanguage: () => void;
  t: (value: string) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

function initialLanguage(): Language {
  return window.localStorage.getItem(LANGUAGE_STORAGE_KEY) === "zh" ? "zh" : "en";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguage] = useState<Language>(initialLanguage);

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    document.documentElement.dataset.language = language;
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  }, [language]);

  const toggleLanguage = useCallback(() => {
    setLanguage((current) => (current === "en" ? "zh" : "en"));
  }, []);
  const t = useCallback((value: string) => translateUiText(value, language), [language]);
  const contextValue = useMemo(() => ({ language, setLanguage, toggleLanguage, t }), [language, t, toggleLanguage]);

  return <I18nContext.Provider value={contextValue}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used inside I18nProvider");
  return context;
}
