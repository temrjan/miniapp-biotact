import { api } from './client';
import type {
  ActivationResult,
  ActivationStatus,
  ConsentResult,
  Gender,
  LoyaltySummary,
  LoyaltyTier,
  MarketingConsentResult,
  MyTickets,
  RegistrationSource,
  SurveyPayload,
  SurveyResult,
} from '@/types';

// ═══════════════════════════════════════════════════════════════════
// Loyalty API (Biotact Club)
// ═══════════════════════════════════════════════════════════════════

// Raw server shapes (snake_case); mapped to the camelCase domain types below.
interface LoyaltySummaryDto {
  tier: LoyaltyTier;
  balance: number;
  available?: number;
  reserved_until?: string | null;
  lifetime_activations: number;
  activations_to_friend: number;
  is_member: boolean;
  gender: Gender | null;
  age: number | null;
  city: string | null;
  is_adult?: boolean;
  consents?: { data_processing: boolean; marketing: boolean };
}

interface ConsentResponseDto {
  consent_recorded: boolean;
  joined: boolean;
  phone_shared: boolean;
}

interface ActivateResponseDto {
  status: ActivationStatus;
  tier: LoyaltyTier;
  points_awarded: number;
  welcome_bonus: number;
  friend_bonus: number;
  balance: number;
  lifetime_activations: number;
}

interface SurveyResponseDto {
  updated_fields: string[];
  survey_done: boolean;
}

// Wire shape of POST /loyalty/survey. Only `is_adult` needs renaming — the other keys are
// spelled the same in both conventions, so they pass through untouched.
interface SurveyRequestDto {
  gender?: Gender;
  age?: number;
  city?: string;
  is_adult?: boolean;
}

interface DrawResultDto {
  draw_number: number;
  tickets: number;
  won: boolean;
  prize_rank: number | null;
  prize_label: string | null;
}

interface MyTicketsDto {
  tickets: number;
  is_frozen: boolean;
  draw_number: number | null;
  prize_label: string | null;
  result: DrawResultDto | null;
  numbers?: number[];
  clean_tickets?: number;
  not_playing_reason?: string | null;
}

/** ISO timestamp → Date, or null when absent or unparseable (never an Invalid Date downstream). */
function parseReservedUntil(raw: string | null | undefined): Date | null {
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export const loyaltyApi = {
  // GET /loyalty/me — current user's loyalty snapshot. 404 while the program is dark.
  getMe: async (): Promise<LoyaltySummary> => {
    const dto = await api.get<LoyaltySummaryDto>('/loyalty/me');
    return {
      tier: dto.tier,
      balance: dto.balance,
      // Same deploy-window reasoning as the survey echo below: an older API omits these keys.
      // Falling back to `balance` degrades to the previous behaviour (offer the full balance and
      // let the server's 409 backstop it) rather than to 0, which would tell a customer with
      // points that they have none — the very lie this field exists to remove.
      available: dto.available ?? dto.balance,
      reservedUntil: parseReservedUntil(dto.reserved_until),
      lifetimeActivations: dto.lifetime_activations,
      activationsToFriend: dto.activations_to_friend,
      isMember: dto.is_member,
      // `?? null` — an older API (deploy window) omits these keys entirely; the domain
      // type promises `| null`, so absent must not leak through as `undefined`.
      gender: dto.gender ?? null,
      age: dto.age ?? null,
      city: dto.city ?? null,
      // Same deploy-window reasoning: an older API omits the key. `false` is the safe default —
      // it asks an already-confirmed member once more rather than showing a confirmation they
      // never gave.
      isAdult: dto.is_adult ?? false,
      // Same deploy-window reasoning: an older API omits the key. false/false is the safe
      // default — it re-shows the consent gate to someone who already consented rather than
      // hiding a consent gate from someone who never did.
      consents: {
        dataProcessing: dto.consents?.data_processing ?? false,
        marketing: dto.consents?.marketing ?? false,
      },
    };
  },

  // GET /loyalty/my-tickets — the member's own lottery participation. 404 while the program is dark.
  getMyTickets: async (): Promise<MyTickets> => {
    const dto = await api.get<MyTicketsDto>('/loyalty/my-tickets');
    return {
      tickets: dto.tickets,
      // Same deploy-window reasoning as `getMe` above: an older API omits the key. `false` is the
      // safe default — it words the number as a live recount that may still change, which is true
      // of a frozen count too. The opposite default would promise a fixed count we cannot back.
      isFrozen: dto.is_frozen ?? false,
      drawNumber: dto.draw_number ?? null,
      prizeLabel: dto.prize_label ?? null,
      // Optional for the same deploy-window reason as the fields above: during a rollout the API
      // may still be the previous one. The defaults show LESS than the member has, never something
      // untrue — an empty list of numbers and a zero count read as "nothing to show yet", while a
      // missing reason simply stays silent.
      numbers: dto.numbers ?? [],
      cleanTickets: dto.clean_tickets ?? 0,
      notPlayingReason: dto.not_playing_reason ?? null,
      result:
        dto.result == null
          ? null
          : {
              drawNumber: dto.result.draw_number,
              tickets: dto.result.tickets,
              won: dto.result.won,
              prizeRank: dto.result.prize_rank ?? null,
              prizeLabel: dto.result.prize_label ?? null,
            },
    };
  },

  // POST /loyalty/consent — record data-processing consent + entry events (idempotent). 404 while dark.
  // `docVersion` is the legal-docs edition from the client registry (buildConsentVersion); the key
  // stays ABSENT when undefined — the flag-OFF request must be byte-identical to the pre-PR-2 one
  // (М2: the server records "1.0" for a version-less body).
  consent: async (source: RegistrationSource, docVersion?: string): Promise<ConsentResult> => {
    const body = docVersion === undefined ? { source } : { source, doc_version: docVersion };
    const dto = await api.post<ConsentResponseDto>('/loyalty/consent', body);
    return {
      consentRecorded: dto.consent_recorded,
      joined: dto.joined,
      phoneShared: dto.phone_shared,
    };
  },

  // POST /loyalty/consent/marketing — grant/revoke marketing consent (append-style, member-only).
  // Same doc_version rule as `consent` above. The response IS the state to render from.
  setMarketingConsent: async (
    granted: boolean,
    docVersion?: string
  ): Promise<MarketingConsentResult> => {
    const body = docVersion === undefined ? { granted } : { granted, doc_version: docVersion };
    const dto = await api.post<{ marketing_active: boolean }>('/loyalty/consent/marketing', body);
    return { marketingActive: dto.marketing_active };
  },

  // POST /qr/activate — claim a scanned code, accrue cashback (idempotent replay). 404 while dark.
  activate: async (code: string): Promise<ActivationResult> => {
    const dto = await api.post<ActivateResponseDto>('/qr/activate', { code });
    return {
      status: dto.status,
      tier: dto.tier,
      pointsAwarded: dto.points_awarded,
      welcomeBonus: dto.welcome_bonus,
      friendBonus: dto.friend_bonus,
      balance: dto.balance,
      lifetimeActivations: dto.lifetime_activations,
    };
  },

  // POST /loyalty/survey — write progressive survey fields (idempotent event). 404 while dark.
  survey: async (payload: SurveyPayload): Promise<SurveyResult> => {
    const { isAdult, ...rest } = payload;
    // `isAdult === undefined` means "not touched" — the key must stay absent, otherwise the
    // server would read it as a withdrawal of a stored confirmation.
    const body: SurveyRequestDto = isAdult === undefined ? rest : { ...rest, is_adult: isAdult };
    const dto = await api.post<SurveyResponseDto>('/loyalty/survey', body);
    return {
      updatedFields: dto.updated_fields,
      surveyDone: dto.survey_done,
    };
  },
};
