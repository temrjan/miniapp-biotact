import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, MapPin, Locate, Loader2 } from 'lucide-react';
import { useLocationStore, type DeliveryAddress } from '@/stores/locationStore';
import { useHaptic } from '@/hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { useGeolocation } from '@/hooks/useGeolocation';
import { reverseGeocode } from '@/utils/geocoding';

const EMPTY_FORM: DeliveryAddress = {
  street: '',
  house: '',
  apartment: '',
  entrance: '',
  floor: '',
  comment: '',
};

export function LocationModal() {
  // ═══════════════════════════════════════════════════════════════════
  // Stores & Hooks
  // ═══════════════════════════════════════════════════════════════════

  const address = useLocationStore((state) => state.address);
  const isModalOpen = useLocationStore((state) => state.isModalOpen);
  const setAddress = useLocationStore((state) => state.setAddress);
  const clearAddress = useLocationStore((state) => state.clearAddress);
  const closeModal = useLocationStore((state) => state.closeModal);
  const haptic = useHaptic();
  const { t } = useTranslation();

  // ═══════════════════════════════════════════════════════════════════
  // Local State
  // ═══════════════════════════════════════════════════════════════════

  const [form, setForm] = useState<DeliveryAddress>(EMPTY_FORM);
  const [coordinates, setCoordinates] = useState<{ lat: number; lng: number } | null>(null);
  const [isGeocoding, setIsGeocoding] = useState(false);
  const [geocodingError, setGeocodingError] = useState<string | null>(null);

  // ═══════════════════════════════════════════════════════════════════
  // Geolocation Hook
  // ═══════════════════════════════════════════════════════════════════

  const {
    location,
    isLoading: isLoadingLocation,
    error: locationError,
    isSupported,
    requestLocation,
  } = useGeolocation({
    onSuccess: async (coords) => {
      haptic.success();
      setCoordinates({ lat: coords.latitude, lng: coords.longitude });

      // Reverse geocode to get address
      setIsGeocoding(true);
      setGeocodingError(null);

      try {
        const addressData = await reverseGeocode(
          coords.latitude,
          coords.longitude,
          'ru' // TODO: Get from language store
        );

        // Auto-fill form fields
        setForm((prev) => ({
          ...prev,
          street: addressData.street || prev.street,
          house: addressData.house || prev.house,
        }));

        haptic.success();
      } catch (error) {
        haptic.error();
        setGeocodingError(
          error instanceof Error
            ? error.message
            : 'Не удалось определить адрес'
        );
      } finally {
        setIsGeocoding(false);
      }
    },
    onError: (error) => {
      haptic.error();
      setGeocodingError(error);
    },
  });

  // ═══════════════════════════════════════════════════════════════════
  // Effects
  // ═══════════════════════════════════════════════════════════════════

  useEffect(() => {
    if (isModalOpen) {
      setForm(address ?? EMPTY_FORM);
      setCoordinates(null);
      setGeocodingError(null);
    }
  }, [isModalOpen, address]);

  // ═══════════════════════════════════════════════════════════════════
  // Handlers
  // ═══════════════════════════════════════════════════════════════════

  const handleSubmit = useCallback(() => {
    if (!form.street.trim() || !form.house.trim()) {
      haptic.error();
      return;
    }

    haptic.success();

    // Include coordinates if available
    const addressWithCoords: DeliveryAddress = {
      ...form,
      lat: coordinates?.lat,
      lng: coordinates?.lng,
    };

    setAddress(addressWithCoords);
  }, [form, coordinates, haptic, setAddress]);

  const handleClose = useCallback(() => {
    haptic.light();
    closeModal();
  }, [haptic, closeModal]);

  const updateField = useCallback((field: keyof DeliveryAddress, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  }, []);

  const handleLocationRequest = useCallback(() => {
    haptic.medium();
    requestLocation();
  }, [haptic, requestLocation]);

  const handleClear = useCallback(() => {
    haptic.medium();
    clearAddress();
    setForm(EMPTY_FORM);
    setCoordinates(null);
  }, [haptic, clearAddress]);

  // ═══════════════════════════════════════════════════════════════════
  // Computed Values
  // ═══════════════════════════════════════════════════════════════════

  const isSubmitDisabled = !form.street.trim() || !form.house.trim();
  const showLocationButton = isSupported;
  const showClearButton = address?.street; // Show if address exists
  const showAccuracy = location && location.accuracy < 100; // Show if better than 100m

  // ═══════════════════════════════════════════════════════════════════
  // Render
  // ═══════════════════════════════════════════════════════════════════

  return (
    <AnimatePresence>
      {isModalOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={handleClose}
            className="fixed inset-0 bg-black/50 z-40"
          />

          {/* Modal */}
          <motion.div
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', damping: 30, stiffness: 300 }}
            className="fixed bottom-0 left-0 right-0 bg-[var(--bg-primary)] rounded-t-3xl z-50 max-h-[90vh] overflow-y-auto"
          >
            {/* Header */}
            <div className="sticky top-0 bg-[var(--bg-primary)] border-b border-[var(--border)] px-4 py-3">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-lg font-semibold">{t('checkout.deliveryAddress')}</h2>
                <button
                  onClick={handleClose}
                  className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-[var(--bg-secondary)]"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Action buttons */}
              <div className="flex gap-2">
                {showLocationButton && (
                  <button
                    onClick={handleLocationRequest}
                    disabled={isLoadingLocation || isGeocoding}
                    className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-[var(--primary)] text-white text-sm font-medium disabled:opacity-50"
                  >
                    {isLoadingLocation || isGeocoding ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Locate className="w-4 h-4" />
                    )}
                    {isLoadingLocation
                      ? 'Определение...'
                      : isGeocoding
                      ? 'Поиск адреса...'
                      : 'Моё местоположение'}
                  </button>
                )}

                {showClearButton && (
                  <button
                    onClick={handleClear}
                    className="px-4 py-2 rounded-lg border border-[var(--border)] text-[var(--text-secondary)] text-sm font-medium hover:bg-[var(--bg-secondary)]"
                  >
                    Очистить
                  </button>
                )}
              </div>
            </div>

            {/* Accuracy Indicator */}
            {showAccuracy && (
              <div className="mx-4 mt-3 p-3 rounded-xl bg-green-50 border border-green-200 flex items-center gap-2">
                <MapPin className="w-4 h-4 text-green-600" />
                <span className="text-sm text-green-700">
                  Точность: {location.accuracy.toFixed(0)}м
                </span>
              </div>
            )}

            {/* Error Messages */}
            {(locationError || geocodingError) && (
              <div className="mx-4 mt-3 p-3 rounded-xl bg-red-50 border border-red-200">
                <p className="text-sm text-red-700">
                  {locationError || geocodingError}
                </p>
              </div>
            )}

            {/* Form */}
            <div className="p-4 space-y-4">
              {/* Street (Required) */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                  {t('checkout.street')} *
                </label>
                <div className="relative">
                  <MapPin className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-[var(--text-muted)]" />
                  <input
                    type="text"
                    value={form.street}
                    onChange={(e) => updateField('street', e.target.value)}
                    placeholder={t('checkout.streetPlaceholder')}
                    className="w-full pl-10 pr-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] focus:outline-none focus:border-[var(--primary)]"
                  />
                </div>
              </div>

              {/* House (Required) */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                  {t('checkout.house')} *
                </label>
                <input
                  type="text"
                  value={form.house}
                  onChange={(e) => updateField('house', e.target.value)}
                  placeholder={t('checkout.housePlaceholder')}
                  className="w-full px-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] focus:outline-none focus:border-[var(--primary)]"
                />
              </div>

              {/* Apartment (Optional) */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                  {t('checkout.apartment')}
                </label>
                <input
                  type="text"
                  value={form.apartment}
                  onChange={(e) => updateField('apartment', e.target.value)}
                  placeholder={t('checkout.apartmentPlaceholder')}
                  className="w-full px-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] focus:outline-none focus:border-[var(--primary)]"
                />
              </div>

              {/* Entrance & Floor (Row) */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                    {t('checkout.entrance')}
                  </label>
                  <input
                    type="text"
                    value={form.entrance}
                    onChange={(e) => updateField('entrance', e.target.value)}
                    placeholder={t('checkout.entrancePlaceholder')}
                    className="w-full px-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] focus:outline-none focus:border-[var(--primary)]"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                    {t('checkout.floor')}
                  </label>
                  <input
                    type="text"
                    value={form.floor}
                    onChange={(e) => updateField('floor', e.target.value)}
                    placeholder={t('checkout.floorPlaceholder')}
                    className="w-full px-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] focus:outline-none focus:border-[var(--primary)]"
                  />
                </div>
              </div>

              {/* Comment (Optional) */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5">
                  {t('checkout.comment')}
                </label>
                <textarea
                  value={form.comment}
                  onChange={(e) => updateField('comment', e.target.value)}
                  placeholder={t('checkout.commentPlaceholder')}
                  rows={3}
                  className="w-full px-4 py-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] focus:outline-none focus:border-[var(--primary)] resize-none"
                />
              </div>

              {/* Submit Button */}
              <button
                onClick={handleSubmit}
                disabled={isSubmitDisabled}
                className="w-full py-4 rounded-xl bg-[var(--primary)] text-white font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {t('common.save')}
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
