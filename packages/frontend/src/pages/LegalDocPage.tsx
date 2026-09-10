import { motion } from 'framer-motion';
import { Navigate, useParams } from 'react-router-dom';
import { Card } from '@components/ui/Card';
import { useBackButton } from '@hooks/useBackButton';
import { useTranslation } from '@/i18n';
import { getLegalDoc } from '@/legal';
import { ROUTES } from '@utils/constants';

export function LegalDocPage() {
  const { slug } = useParams<{ slug: string }>();
  const { t, language } = useTranslation();

  useBackButton();

  const doc = slug ? getLegalDoc(slug) : undefined;

  if (!doc) {
    return <Navigate to={ROUTES.LEGAL} replace />;
  }

  const tocSections = doc.sections.filter((section) => section.heading !== '');

  const scrollToSection = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="p-3 space-y-4 max-w-2xl mx-auto pb-8">
      <motion.header initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-xl font-bold leading-snug">{doc.title}</h1>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          {t('legal.versionLabel', { date: doc.updatedAt })}
        </p>
      </motion.header>

      {/* Тексты документов существуют только на русском (uz-редакции — от юристов,
          отдельный круг) — узбекскоязычному пользователю говорим об этом явно. */}
      {language === 'uz' && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <Card className="p-3 text-sm text-[var(--text-secondary)]">{t('legal.ruOnlyNotice')}</Card>
        </motion.div>
      )}

      {tocSections.length > 0 && (
        <motion.section
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
        >
          <Card className="p-4">
            <h2 className="font-bold mb-2">{t('legal.contentsTitle')}</h2>
            <nav className="space-y-1">
              {tocSections.map((section) => (
                <button
                  key={section.id}
                  onClick={() => scrollToSection(section.id)}
                  className="block w-full text-left text-sm text-[var(--primary)] hover:underline"
                >
                  {section.heading}
                </button>
              ))}
            </nav>
          </Card>
        </motion.section>
      )}

      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="space-y-3"
      >
        {doc.sections.map((section) => (
          <Card key={section.id} id={section.id} className="p-4 scroll-mt-4">
            {section.heading !== '' && <h2 className="font-bold mb-2">{section.heading}</h2>}
            <div className="space-y-2 text-sm text-[var(--text-secondary)] leading-relaxed">
              {section.paragraphs.map((paragraph, index) => (
                <p key={index}>{paragraph}</p>
              ))}
            </div>
          </Card>
        ))}
      </motion.section>
    </div>
  );
}
