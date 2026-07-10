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
}

// Progressive survey (member-only, rendered by LoyaltySection next to LoyaltyCard). Optional
// gender/age/city. Saved at least once (any field non-null) → the form collapses to a compact
// "survey filled" row with an edit button; the edit form is pre-filled from the saved values.
// Present fields overwrite on the server (no COALESCE), so partial corrections are safe.
// The collapse itself is the save confirmation — no extra post-save state.
export function SurveyCard({ gender: savedGender, age: savedAge, city: savedCity }: SurveyCardProps) {
  const { t } = useTranslation();
  const [gender, setGender] = useState<Gender | ''>('');
  const [age, setAge] = useState('');
  const [city, setCity] = useState('');
  const [editing, setEditing] = useState(false);
  const survey = useSurvey();

  const filled = savedGender !== null || savedAge !== null || savedCity !== null;
  const isEmpty = gender === '' && age === '' && city.trim() === '';

  const startEditing = () => {
    setGender(savedGender ?? '');
    setAge(savedAge !== null ? String(savedAge) : '');
    setCity(savedCity ?? '');
    setEditing(true);
  };

  const handleSave = () => {
    // exactOptionalPropertyTypes forbids `key: undefined` — omit unset keys entirely.
    const payload: SurveyPayload = {};
    if (gender !== '') payload.gender = gender;
    if (age !== '') payload.age = Number(age);
    const trimmedCity = city.trim();
    if (trimmedCity !== '') payload.city = trimmedCity;
    survey.mutate(payload, { onSuccess: () => setEditing(false) });
  };

  if (filled && !editing) {
    return (
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.05 }}
      >
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
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.05 }}
    >
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

        {survey.isError && (
          <p className="text-sm text-[var(--error)]">{t('activation.error.generic')}</p>
        )}
        {survey.isSuccess && <p className="text-sm text-[var(--primary)]">{t('survey.success')}</p>}

        <Button fullWidth disabled={isEmpty} isLoading={survey.isPending} onClick={handleSave}>
          {t('survey.saveButton')}
        </Button>
      </Card>
    </motion.section>
  );
}
