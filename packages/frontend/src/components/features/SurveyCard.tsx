import { useState } from 'react';
import { motion } from 'framer-motion';
import { Check, ClipboardList } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { Button } from '@components/ui/Button';
import { useSurvey } from '@hooks/useSurvey';
import { useTranslation } from '@/i18n';
import type { Gender, SurveyPayload } from '@/types';

interface SurveyCardProps {
  // Saved survey echoed by GET /loyalty/me — null means "not answered yet".
  gender: Gender | null;
  age: number | null;
  city: string | null;
  // Stored 18+ confirmation (lottery entry). Not part of "is the survey filled": it is a legal
  // self-confirmation, not an answer about the member — see `filled` below.
  isAdult: boolean;
}

// Shared entrance animation for both branches (filled row / form) of this card.
const ENTRANCE = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { delay: 0.05 },
} as const;

// Progressive survey (member-only, rendered by LoyaltySection next to LoyaltyCard). Optional
// gender/age/city. Saved at least once (any field non-null) → the form collapses to a compact
// "survey filled" row with an edit button; the edit form is pre-filled from the saved values.
// Present fields overwrite on the server (no COALESCE), so partial corrections are safe.
// The collapse itself is the save confirmation — no extra post-save state.
export function SurveyCard({
  gender: savedGender,
  age: savedAge,
  city: savedCity,
  isAdult: savedIsAdult,
}: SurveyCardProps) {
  const { t } = useTranslation();
  const [gender, setGender] = useState<Gender | ''>('');
  const [age, setAge] = useState('');
  const [city, setCity] = useState('');
  // Seeded from the server (unlike the text fields, which start blank): the box can already be
  // ticked while the survey itself is unanswered, and a stored confirmation must render as given.
  const [isAdult, setIsAdult] = useState(savedIsAdult);
  const [editing, setEditing] = useState(false);
  const survey = useSurvey();

  // "Survey filled" is about the DEMOGRAPHIC answers only. The 18+ box deliberately does not
  // collapse the card: confirming an age bracket is not telling us who you are, and the server
  // agrees — a call carrying only `is_adult` does not mark the survey done either.
  const filled = savedGender !== null || savedAge !== null || savedCity !== null;
  // The server rejects an empty body (422), so Save stays disabled until this call would actually
  // carry something: a typed value, or the box toggled away from what is stored.
  const adultChanged = isAdult !== savedIsAdult;
  const isEmpty = gender === '' && age === '' && city.trim() === '' && !adultChanged;

  const startEditing = () => {
    setGender(savedGender ?? '');
    setAge(savedAge !== null ? String(savedAge) : '');
    setCity(savedCity ?? '');
    setIsAdult(savedIsAdult);
    setEditing(true);
  };

  const handleSave = () => {
    // exactOptionalPropertyTypes forbids `key: undefined` — omit unset keys entirely.
    const payload: SurveyPayload = {};
    if (gender !== '') payload.gender = gender;
    if (age !== '') payload.age = Number(age);
    const trimmedCity = city.trim();
    if (trimmedCity !== '') payload.city = trimmedCity;
    // Sent only when it differs from the stored value: re-sending an unchanged `true` would list
    // `is_adult` among the fields this call wrote, which is not what happened.
    if (adultChanged) payload.isAdult = isAdult;
    survey.mutate(payload, { onSuccess: () => setEditing(false) });
  };

  if (filled && !editing) {
    return (
      <motion.section {...ENTRANCE}>
        <Card className="p-4">
          <div className="flex items-center gap-2">
            <Check size={18} className="text-[var(--primary)]" aria-hidden />
            <span className="flex-1 font-semibold">{t('survey.filledTitle')}</span>
            <Button variant="ghost" size="sm" onClick={startEditing}>
              {t('survey.editButton')}
            </Button>
          </div>
        </Card>
      </motion.section>
    );
  }

  return (
    <motion.section {...ENTRANCE}>
      <Card className="p-4 space-y-3">
        <div className="flex items-center gap-2">
          <ClipboardList size={18} className="text-[var(--primary)]" aria-hidden />
          <span className="font-semibold">{t('survey.title')}</span>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-[var(--text-secondary)]">{t('survey.genderLabel')}</span>
          <select
            value={gender}
            onChange={(e) => setGender(e.target.value as Gender | '')}
            className="rounded-lg border border-[var(--shadow-dark)]/20 bg-transparent p-2"
          >
            <option value="">—</option>
            <option value="male">{t('survey.genderMale')}</option>
            <option value="female">{t('survey.genderFemale')}</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-[var(--text-secondary)]">{t('survey.ageLabel')}</span>
          <input
            type="number"
            min={1}
            max={120}
            value={age}
            onChange={(e) => setAge(e.target.value)}
            className="rounded-lg border border-[var(--shadow-dark)]/20 bg-transparent p-2"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-[var(--text-secondary)]">{t('survey.cityLabel')}</span>
          <input
            type="text"
            value={city}
            onChange={(e) => setCity(e.target.value)}
            className="rounded-lg border border-[var(--shadow-dark)]/20 bg-transparent p-2"
          />
        </label>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={isAdult}
            onChange={(e) => setIsAdult(e.target.checked)}
            className="size-4 accent-[var(--primary)]"
          />
          <span className="text-[var(--text-secondary)]">{t('survey.adultLabel')}</span>
        </label>

        {survey.isError && (
          <p className="text-sm text-[var(--error)]">{t('activation.error.generic')}</p>
        )}
        {survey.isSuccess && <p className="text-sm text-[var(--primary)]">{t('survey.success')}</p>}

        {editing ? (
          <div className="flex gap-2">
            <Button variant="ghost" fullWidth onClick={() => setEditing(false)}>
              {t('common.cancel')}
            </Button>
            <Button fullWidth disabled={isEmpty} isLoading={survey.isPending} onClick={handleSave}>
              {t('survey.saveButton')}
            </Button>
          </div>
        ) : (
          <Button fullWidth disabled={isEmpty} isLoading={survey.isPending} onClick={handleSave}>
            {t('survey.saveButton')}
          </Button>
        )}
      </Card>
    </motion.section>
  );
}
