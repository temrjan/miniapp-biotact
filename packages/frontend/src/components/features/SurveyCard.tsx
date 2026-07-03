import { useState } from 'react';
import { motion } from 'framer-motion';
import { ClipboardList } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { Button } from '@components/ui/Button';
import { useSurvey } from '@hooks/useSurvey';
import { useTranslation } from '@/i18n';
import type { Gender, SurveyPayload } from '@/types';

// Progressive survey (member-only, rendered by LoyaltySection next to LoyaltyCard). Optional
// gender/age/city; the backend overwrites on every call (no COALESCE), so this form is always
// empty — no pre-fill from GET /loyalty/me, no post-save disabling (spec Q4). A successful save
// shows a confirmation but keeps the fields active for a later correction/extension.
export function SurveyCard() {
  const { t } = useTranslation();
  const [gender, setGender] = useState<Gender | ''>('');
  const [age, setAge] = useState('');
  const [city, setCity] = useState('');
  const survey = useSurvey();

  const isEmpty = gender === '' && age === '' && city.trim() === '';

  const handleSave = () => {
    // exactOptionalPropertyTypes forbids `key: undefined` — omit unset keys entirely.
    const payload: SurveyPayload = {};
    if (gender !== '') payload.gender = gender;
    if (age !== '') payload.age = Number(age);
    const trimmedCity = city.trim();
    if (trimmedCity !== '') payload.city = trimmedCity;
    survey.mutate(payload);
  };

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
