import { api } from './client';
import type {
  ActivationResult,
  ActivationStatus,
  ConsentResult,
  Gender,
  LoyaltySummary,
  LoyaltyTier,
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
    };
  },

  // POST /loyalty/consent — record data-processing consent + entry events (idempotent). 404 while dark.
  consent: async (source: RegistrationSource): Promise<ConsentResult> => {
    const dto = await api.post<ConsentResponseDto>('/loyalty/consent', { source });
    return {
      consentRecorded: dto.consent_recorded,
      joined: dto.joined,
      phoneShared: dto.phone_shared,
    };
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
