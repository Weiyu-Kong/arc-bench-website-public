import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, theme } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";

import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { I18nProvider, useI18n } from "./i18n/I18nContext";
import LocalizedDocument from "./i18n/LocalizedDocument";
import "./styles/global.css";

function resolveThemeMode() {
  if (document.documentElement.dataset.theme === "dark" || window.localStorage.getItem("theme") === "dark") {
    return "dark" as const;
  }
  return "light" as const;
}

function LocalizedRoot() {
  const [themeMode, setThemeMode] = useState<"light" | "dark">(() => resolveThemeMode());
  const { language } = useI18n();

  useEffect(() => {
    const syncThemeMode = () => {
      setThemeMode(resolveThemeMode());
    };

    syncThemeMode();
    const observer = new MutationObserver(syncThemeMode);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    window.addEventListener("storage", syncThemeMode);

    return () => {
      observer.disconnect();
      window.removeEventListener("storage", syncThemeMode);
    };
  }, []);

  return (
    <ConfigProvider
      locale={language === "zh" ? zhCN : enUS}
      theme={{
        algorithm: themeMode === "light" ? theme.defaultAlgorithm : theme.darkAlgorithm,
        token: {
          colorPrimary: "#24272d",
          fontFamily: "'Outfit', sans-serif",
          borderRadius: 12,
        },
      }}
    >
      <AuthProvider>
        <App />
        <LocalizedDocument />
      </AuthProvider>
    </ConfigProvider>
  );
}

function Root() {
  return <I18nProvider><LocalizedRoot /></I18nProvider>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
