/**
 * Localisation, assembled from the modules.
 *
 * Each module ships its own strings; the kernel merges them and flips the document direction.
 * A new module never needs a central translation file edited.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { getRegistry } from "./registry";
import { readSetting, writeSetting } from "./storage";

export type Locale = "ar" | "en";

const LOCALE_KEY = "tzone.locale";

interface I18nValue {
  locale: Locale;
  dir: "rtl" | "ltr";
  setLocale(locale: Locale): void;
  t(key: string, params?: Record<string, string | number>): string;
  /** Pick the right field of a record that carries both languages. */
  pick(record: { name_en?: string; name_ar?: string } | undefined): string;
}

const I18nContext = createContext<I18nValue | null>(null);

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, key) =>
    key in params ? String(params[key]) : match,
  );
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(
    (readSetting(LOCALE_KEY) as Locale | null) ?? "ar",
  );

  const dir = locale === "ar" ? "rtl" : "ltr";

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = dir;
  }, [locale, dir]);

  const setLocale = useCallback((next: Locale) => {
    writeSetting(LOCALE_KEY, next);
    setLocaleState(next);
  }, []);

  const value = useMemo<I18nValue>(() => {
    const table = getRegistry().translations[locale];
    const fallback = getRegistry().translations.en;
    return {
      locale,
      dir,
      setLocale,
      t: (key, params) => interpolate(table[key] ?? fallback[key] ?? key, params),
      pick: (record) =>
        (locale === "ar" ? record?.name_ar || record?.name_en : record?.name_en) ?? "",
    };
  }, [locale, dir, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}
