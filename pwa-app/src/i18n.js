import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import it from "./locales/it.json";
import en from "./locales/en.json";

const LANG_KEY = "clorofilla-lang";
const savedLang = (typeof window !== "undefined" && window.localStorage.getItem(LANG_KEY)) || "it";

i18n.use(initReactI18next).init({
  resources: {
    it: { translation: it },
    en: { translation: en },
  },
  lng: savedLang,
  fallbackLng: "it",
  interpolation: {
    escapeValue: false,
  },
});

i18n.on("languageChanged", (lng) => {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(LANG_KEY, lng);
  }
});

export default i18n;
