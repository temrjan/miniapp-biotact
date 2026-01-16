import { useLanguage, type Language } from '@/stores/languageStore';
import ru from './ru.json';
import uz from './uz.json';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

type TranslationKey = string;
type Translations = typeof ru;

// ═══════════════════════════════════════════════════════════════════
// Translation data
// ═══════════════════════════════════════════════════════════════════

const translations: Record<Language, Translations> = {
  ru,
  uz,
};

// ═══════════════════════════════════════════════════════════════════
// Helper function to get nested value
// ═══════════════════════════════════════════════════════════════════

function getNestedValue(obj: Record<string, unknown>, path: string): string {
  const keys = path.split('.');
  let current: unknown = obj;

  for (const key of keys) {
    if (current && typeof current === 'object' && key in current) {
      current = (current as Record<string, unknown>)[key];
    } else {
      return path; // Return key if not found
    }
  }

  return typeof current === 'string' ? current : path;
}

// ═══════════════════════════════════════════════════════════════════
// Hook
// ═══════════════════════════════════════════════════════════════════

export function useTranslation() {
  const language = useLanguage();

  const t = (key: TranslationKey, params?: Record<string, string | number>): string => {
    let text = getNestedValue(translations[language], key);

    // Replace params like {name} with values
    if (params) {
      Object.entries(params).forEach(([paramKey, value]) => {
        text = text.replace(new RegExp(`\\{${paramKey}\\}`, 'g'), String(value));
      });
    }

    return text;
  };

  return { t, language };
}

// ═══════════════════════════════════════════════════════════════════
// Exports
// ═══════════════════════════════════════════════════════════════════

export { translations };
export type { Language, Translations };
