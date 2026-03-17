import { useState, useCallback, type ChangeEvent, type KeyboardEvent } from 'react';
import { haptic } from '@/services/telegram';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface PhoneInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  disabled?: boolean;
  autoFocus?: boolean;
}

// ═══════════════════════════════════════════════════════════════════
// Utils
// ═══════════════════════════════════════════════════════════════════

function formatPhone(input: string): string {
  // Remove non-digits except +
  const cleaned = input.replace(/[^\d+]/g, '');

  // Ensure starts with +998
  if (!cleaned.startsWith('+998')) {
    if (cleaned.startsWith('998')) return '+' + cleaned;
    if (cleaned.startsWith('+')) return cleaned;
    return '+998' + cleaned.replace(/^\+?/, '');
  }

  return cleaned;
}

function isValidPhone(phone: string): boolean {
  // +998XXXXXXXXX = 13 characters
  return phone.length >= 13 && phone.length <= 15;
}

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function PhoneInput({
  value,
  onChange,
  onSubmit,
  disabled = false,
  autoFocus = false,
}: PhoneInputProps) {
  const [localValue, setLocalValue] = useState(() => value ? formatPhone(value) : '+998');
  const [touched, setTouched] = useState(false);

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const formatted = formatPhone(e.target.value);
      setLocalValue(formatted);
      onChange(formatted);
    },
    [onChange]
  );

  const handleBlur = useCallback(() => {
    setTouched(true);
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter' && isValidPhone(localValue) && onSubmit) {
        haptic.light();
        onSubmit();
      }
    },
    [localValue, onSubmit]
  );

  const isValid = isValidPhone(localValue);
  const showError = touched && !isValid && localValue.length > 4;

  return (
    <div className="phone-input">
      <label className="block text-sm font-medium mb-1 text-[var(--text-muted)]">
        Номер телефона
      </label>
      <input
        type="tel"
        value={localValue}
        onChange={handleChange}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        placeholder="+998 90 123 45 67"
        disabled={disabled}
        autoFocus={autoFocus}
        className={`w-full p-3 rounded-lg bg-[var(--bg-secondary)] border ${
          showError
            ? 'border-red-500'
            : 'border-transparent focus:border-[var(--tg-theme-button-color)]'
        } outline-none transition-colors`}
      />
      {showError && (
        <p className="text-sm text-red-500 mt-1">
          Введите полный номер телефона
        </p>
      )}
    </div>
  );
}
