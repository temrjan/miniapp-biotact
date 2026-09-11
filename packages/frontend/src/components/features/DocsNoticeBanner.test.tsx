import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import { buildConsentVersion } from '@/legal';
import { translations } from '@/i18n';
import { DocsNoticeBanner, DOCS_NOTICE_STORAGE_KEY } from './DocsNoticeBanner';

function renderBanner() {
  return render(
    <MemoryRouter initialEntries={['/profile']}>
      <Routes>
        <Route path="/profile" element={<DocsNoticeBanner />} />
        <Route path="/legal" element={<div>LEGAL_LIST</div>} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe('DocsNoticeBanner', () => {
  it('показывается, когда текущая версия документов ещё не отмечена как просмотренная', () => {
    renderBanner();

    const banner = screen.getByTestId('docs-notice-banner');
    expect(banner.textContent).toContain(translations.ru.profile.docsNoticeTitle);
    expect(banner.textContent).toContain(translations.ru.profile.docsNoticeBody);
  });

  it('закрытие прячет баннер и записывает ТЕКУЩУЮ версию в localStorage', () => {
    renderBanner();

    fireEvent.click(screen.getByRole('button', { name: translations.ru.profile.docsNoticeDismiss }));

    expect(screen.queryByTestId('docs-notice-banner')).not.toBeInTheDocument();
    // Ключ хранит именно версию, а не флаг «да/нет»: только так бамп версии
    // (смена редакции) отличается от «уже видел» и показывает баннер снова.
    expect(localStorage.getItem(DOCS_NOTICE_STORAGE_KEY)).toBe(buildConsentVersion());
  });

  it('после закрытия не появляется при повторном рендере', () => {
    localStorage.setItem(DOCS_NOTICE_STORAGE_KEY, buildConsentVersion());

    renderBanner();

    expect(screen.queryByTestId('docs-notice-banner')).not.toBeInTheDocument();
  });

  it('смена buildConsentVersion() показывает баннер снова (один раз НА ВЕРСИЮ)', () => {
    localStorage.setItem(DOCS_NOTICE_STORAGE_KEY, 'offer:old+policy:old');

    renderBanner();

    expect(screen.getByTestId('docs-notice-banner')).toBeInTheDocument();
  });

  it('кнопка «Ознакомиться» ведёт на /legal и тоже отмечает версию просмотренной', () => {
    renderBanner();

    fireEvent.click(screen.getByRole('button', { name: translations.ru.profile.docsNoticeCta }));

    expect(screen.getByText('LEGAL_LIST')).toBeInTheDocument();
    expect(localStorage.getItem(DOCS_NOTICE_STORAGE_KEY)).toBe(buildConsentVersion());
  });
});
