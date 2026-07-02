import { useState } from 'react';
import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { User, Package, Settings, LogOut, ChevronRight, Globe, Phone, MapPin, Check } from 'lucide-react';
import { Card } from '@components/ui/Card';
import { LoyaltySection } from '@components/features/LoyaltySection';
import { useUser } from '@stores/appStore';
import { useLanguage, useSetLanguage, type Language } from '@stores/languageStore';
import { useTranslation } from '@/i18n';
import { useHaptic } from '@hooks/useTelegram';
import WebApp from '@twa-dev/sdk';

// ═══════════════════════════════════════════════════════════════════
// LanguageSelector Component
// ═══════════════════════════════════════════════════════════════════

function LanguageSelector() {
  const language = useLanguage();
  const setLanguage = useSetLanguage();
  const haptic = useHaptic();

  const handleToggle = async () => {
    haptic.light();
    const newLang: Language = language === 'ru' ? 'uz' : 'ru';
    try {
      await setLanguage(newLang);
      haptic.success();
    } catch {
      haptic.error();
    }
  };

  return (
    <button
      onClick={handleToggle}
      className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--shadow-dark)]/10 hover:bg-[var(--shadow-dark)]/20 transition-colors"
    >
      <span className="text-sm font-medium">
        {language === 'ru' ? '🇷🇺 RU' : "🇺🇿 UZ"}
      </span>
      <ChevronRight size={14} className="text-[var(--text-muted)]" />
    </button>
  );
}

// ═══════════════════════════════════════════════════════════════════
// ProfilePage Component
// ═══════════════════════════════════════════════════════════════════

interface MenuItem {
  icon: typeof User;
  labelKey: string;
  descriptionKey?: string;
  onClick: () => void;
  color?: string;
}

export function ProfilePage() {
  const user = useUser();
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();

  // Settings state
  const [showSettings, setShowSettings] = useState(false);
  const [phone, setPhone] = useState(() => localStorage.getItem('user_phone') || '');
  const [address, setAddress] = useState(() => localStorage.getItem('user_address') || '');
  const [saved, setSaved] = useState(false);

  const handleSaveSettings = () => {
    haptic.success();
    localStorage.setItem('user_phone', phone);
    localStorage.setItem('user_address', address);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const menuItems: MenuItem[] = [
    {
      icon: Package,
      labelKey: 'profile.myOrders',
      descriptionKey: 'profile.ordersDescription',
      onClick: () => {
        haptic.light();
        navigate('/orders');
      },
    },
    {
      icon: Settings,
      labelKey: 'profile.settings',
      descriptionKey: 'profile.settingsDescription',
      onClick: () => {
        haptic.light();
        setShowSettings(!showSettings);
      },
    },
  ];

  const handleClose = () => {
    haptic.medium();
    WebApp.close();
  };

  return (
    <div className="p-3 space-y-4 max-w-2xl mx-auto">
      {/* User Info */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <Card className="flex items-center gap-4">
          <div className="neumorphic w-16 h-16 rounded-full flex items-center justify-center">
            {user?.photoUrl ? (
              <img
                src={user.photoUrl}
                alt={user.firstName}
                className="w-full h-full rounded-full object-cover"
              />
            ) : (
              <User size={28} className="text-[var(--text-muted)]" />
            )}
          </div>
          <div className="flex-1">
            <h2 className="text-lg font-bold">
              {user?.firstName} {user?.lastName}
            </h2>
            {user?.username && (
              <p className="text-[var(--text-muted)]">@{user.username}</p>
            )}
            {user?.isPremium && (
              <span className="inline-flex items-center text-xs text-[var(--primary)] font-medium mt-1">
                ⭐ Premium
              </span>
            )}
          </div>
        </Card>
      </motion.div>

      {/* Loyalty (Biotact Club) — member card vs direct-join CTA; hidden while dark (404) */}
      <LoyaltySection />

      {/* Language Selector */}
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.05 }}
      >
        <Card className="flex items-center justify-between p-4">
          <div className="flex items-center gap-4">
            <div className="neumorphic-sm w-10 h-10 rounded-full flex items-center justify-center">
              <Globe size={20} className="text-[var(--text-secondary)]" />
            </div>
            <div>
              <span className="font-medium">{t('profile.language')}</span>
              <p className="text-sm text-[var(--text-muted)]">
                {t('profile.settingsDescription')}
              </p>
            </div>
          </div>
          <LanguageSelector />
        </Card>
      </motion.section>

      {/* Menu Items */}
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
      >
        <Card className="divide-y divide-[var(--shadow-dark)]/20">
          {menuItems.map((item, index) => {
            const Icon = item.icon;
            return (
              <motion.button
                key={item.labelKey}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.15 + index * 0.05 }}
                onClick={item.onClick}
                className="w-full flex items-center gap-4 p-4 text-left hover:bg-[var(--shadow-dark)]/5 transition-colors first:rounded-t-[var(--radius-md)] last:rounded-b-[var(--radius-md)]"
              >
                <div className="neumorphic-sm w-10 h-10 rounded-full flex items-center justify-center">
                  <Icon size={20} style={{ color: item.color || 'var(--text-secondary)' }} />
                </div>
                <div className="flex-1">
                  <span className="font-medium">{t(item.labelKey)}</span>
                  {item.descriptionKey && (
                    <p className="text-sm text-[var(--text-muted)]">{t(item.descriptionKey)}</p>
                  )}
                </div>
                <ChevronRight
                  size={20}
                  className={`text-[var(--text-muted)] transition-transform ${item.labelKey === 'profile.settings' && showSettings ? 'rotate-90' : ''}`}
                />
              </motion.button>
            );
          })}
        </Card>
      </motion.section>

      {/* Settings Panel */}
      {showSettings && (
        <motion.section
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.2 }}
        >
          <Card className="p-4 space-y-4">
            {/* Phone */}
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm font-medium text-[var(--text-secondary)]">
                <Phone size={16} />
                {t('profile.phoneLabel')}
              </label>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+998 XX XXX XX XX"
                className="w-full px-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--primary)]"
              />
            </div>

            {/* Address */}
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm font-medium text-[var(--text-secondary)]">
                <MapPin size={16} />
                {t('profile.addressLabel')}
              </label>
              <textarea
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder={t('profile.addressPlaceholder')}
                rows={2}
                className="w-full px-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--primary)] resize-none"
              />
            </div>

            {/* Save Button */}
            <button
              onClick={handleSaveSettings}
              className="w-full py-3 bg-[var(--primary)] text-white font-semibold rounded-xl flex items-center justify-center gap-2 active:scale-[0.98] transition-transform"
            >
              {saved ? (
                <>
                  <Check size={20} />
                  {t('profile.saved')}
                </>
              ) : (
                t('profile.save')
              )}
            </button>
          </Card>
        </motion.section>
      )}

      {/* Close App Button */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.25 }}
      >
        <Card
          className="flex items-center gap-4 p-4 cursor-pointer hover:bg-[var(--shadow-dark)]/5 transition-colors"
          onClick={handleClose}
        >
          <div className="neumorphic-sm w-10 h-10 rounded-full flex items-center justify-center">
            <LogOut size={20} className="text-[var(--error)]" />
          </div>
          <span className="font-medium text-[var(--error)]">{t('profile.closeApp')}</span>
        </Card>
      </motion.div>

      {/* App Version */}
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        className="text-center text-sm text-[var(--text-muted)]"
      >
        app.biotact.uz v1.0.0
      </motion.p>
    </div>
  );
}
