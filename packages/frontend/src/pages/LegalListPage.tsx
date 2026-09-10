import { motion } from 'framer-motion';
import { FileText, ChevronRight, Mail, Phone, MapPin, MessageCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Card } from '@components/ui/Card';
import { useBackButton } from '@hooks/useBackButton';
import { useHaptic } from '@hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { LEGAL_DOCS } from '@/legal';
import { LEGAL_CONTACTS } from '@/legal/contacts';
import { ROUTES } from '@utils/constants';

export function LegalListPage() {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();

  useBackButton();

  return (
    <div className="p-3 space-y-4 max-w-2xl mx-auto pb-8">
      <motion.h1
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="text-xl font-bold"
      >
        {t('legal.title')}
      </motion.h1>

      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.05 }}
      >
        <Card className="divide-y divide-[var(--shadow-dark)]/20">
          {LEGAL_DOCS.map((doc, index) => (
            <motion.button
              key={doc.slug}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.1 + index * 0.05 }}
              onClick={() => {
                haptic.light();
                navigate(`${ROUTES.LEGAL}/${doc.slug}`);
              }}
              className="w-full flex items-center gap-4 p-4 text-left hover:bg-[var(--shadow-dark)]/5 transition-colors first:rounded-t-[var(--radius-md)] last:rounded-b-[var(--radius-md)]"
            >
              <div className="neumorphic-sm w-10 h-10 rounded-full flex items-center justify-center shrink-0">
                <FileText size={20} className="text-[var(--text-secondary)]" />
              </div>
              <div className="flex-1 min-w-0">
                <span className="font-medium leading-snug block">{doc.title}</span>
                <p className="text-sm text-[var(--text-muted)]">
                  {t('legal.versionLabel', { date: doc.updatedAt })}
                </p>
              </div>
              <ChevronRight size={20} className="text-[var(--text-muted)] shrink-0" />
            </motion.button>
          ))}
        </Card>
      </motion.section>

      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <Card className="p-4 space-y-3">
          <h2 className="font-bold">{t('legal.contactsTitle')}</h2>
          <p className="flex items-center gap-3 text-sm">
            <MessageCircle size={16} className="text-[var(--text-muted)] shrink-0" />
            {t('legal.contactsSupport', { bot: LEGAL_CONTACTS.supportBot })}
          </p>
          <p className="flex items-center gap-3 text-sm">
            <Mail size={16} className="text-[var(--text-muted)] shrink-0" />
            {LEGAL_CONTACTS.email}
          </p>
          <p className="flex items-center gap-3 text-sm">
            <Phone size={16} className="text-[var(--text-muted)] shrink-0" />
            {LEGAL_CONTACTS.phones.join(', ')}
          </p>
          <p className="flex items-start gap-3 text-sm">
            <MapPin size={16} className="text-[var(--text-muted)] shrink-0 mt-0.5" />
            {LEGAL_CONTACTS.postalAddress}
          </p>
        </Card>
      </motion.section>
    </div>
  );
}
