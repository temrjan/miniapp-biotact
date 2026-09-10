// ═══════════════════════════════════════════════════════════════════
// Legal documents registry — единственный источник версий редакций (F1).
// Версия живёт в контент-файле рядом с текстом; бамп версии = тот же коммит,
// что и правка текста. Бэкенд версий не знает: клиент присылает композит
// buildConsentVersion() при записи согласия (PR-2).
// ═══════════════════════════════════════════════════════════════════

import { offerDoc } from './offer';
import { policyDoc } from './policy';
import { termsDoc } from './terms';

export interface LegalDocSection {
  id: string;
  heading: string;
  paragraphs: string[];
}

export interface LegalDoc {
  slug: 'offer' | 'policy' | 'terms';
  title: string;
  // ЕДИНСТВЕННЫЙ источник версии. Бюджет: ≤ 18 символов (композит обязан
  // влезать в String(50) на бэке).
  version: string;
  updatedAt: string; // ISO-дата редакции
  sections: LegalDocSection[];
}

export const LEGAL_DOCS: LegalDoc[] = [offerDoc, policyDoc, termsDoc];

export function getLegalDoc(slug: string): LegalDoc | undefined {
  return LEGAL_DOCS.find((doc) => doc.slug === slug);
}

// Композит `offer:<v>+policy:<v>` — валидация на бэке: ≤ 50 символов (М1).
export function buildConsentVersion(): string {
  return `offer:${offerDoc.version}+policy:${policyDoc.version}`;
}
