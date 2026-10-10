import { useTranslation } from '@/i18n';

interface Props {
  sizes: string[];
  selected: string | null;
  onChange: (size: string) => void;
}

export function SizeSelector({ sizes, selected, onChange }: Props) {
  const { t } = useTranslation();
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{t('product.chooseSize')}</legend>
      <div className="flex gap-2">
        {sizes.map((size) => (
          <button
            type="button"
            key={size}
            aria-pressed={selected === size}
            onClick={() => onChange(size)}
            className={`rounded-lg border px-4 py-2 ${selected === size ? 'bg-[var(--primary)] text-white' : ''}`}
          >
            {size}
          </button>
        ))}
      </div>
      {sizes.length === 0 && <p>{t('product.outOfStock')}</p>}
    </fieldset>
  );
}
