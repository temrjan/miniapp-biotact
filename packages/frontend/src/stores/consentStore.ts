import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

// ═══════════════════════════════════════════════════════════════════
// Состояние чекбоксов согласия при вступлении в Клуб (legal-docs PR-2)
// ═══════════════════════════════════════════════════════════════════
//
// In-memory НАМЕРЕННО (без persist): стор существует, чтобы галочки пережили
// переход на экран документа (/legal/*) и возврат — локальный useState терялся
// при размонтировании (Finding 2). Переживать ПЕРЕЗАПУСК мини-аппа галочки не
// должны: неподтверждённое согласие — не состояние, которое возвращают из прошлого.

interface ConsentState {
  // Обязательный: оферта + политика + обработка ПД. Без него кнопка неактивна.
  dataProcessing: boolean;
  // Опциональный: маркетинговые рассылки. Не блокирует кнопку.
  marketing: boolean;

  setDataProcessing: (checked: boolean) => void;
  setMarketing: (checked: boolean) => void;
  // После успешной записи согласия — оба обратно в «не отмечено».
  reset: () => void;
}

export const useConsentStore = create<ConsentState>()(
  devtools(
    (set) => ({
      dataProcessing: false,
      marketing: false,

      setDataProcessing: (checked) => {
        set({ dataProcessing: checked }, false, 'setDataProcessing');
      },

      setMarketing: (checked) => {
        set({ marketing: checked }, false, 'setMarketing');
      },

      reset: () => {
        set({ dataProcessing: false, marketing: false }, false, 'reset');
      },
    }),
    { name: 'ConsentStore' }
  )
);
