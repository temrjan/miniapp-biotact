import { describe, it, expect, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import { getLegalDoc } from '@/legal';
import { useLanguageStore } from '@/stores/languageStore';
import { translations } from '@/i18n';
import { LegalDocPage } from './LegalDocPage';

function renderDoc(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/legal" element={<div>LEGAL_LIST</div>} />
        <Route path="/legal/:slug" element={<LegalDocPage />} />
      </Routes>
    </MemoryRouter>
  );
}

afterEach(() => {
  useLanguageStore.setState({ language: 'ru' });
});

describe('LegalDocPage', () => {
  it('renders the offer with title, revision date and every section', () => {
    useLanguageStore.setState({ language: 'ru' });
    const offer = getLegalDoc('offer');
    if (!offer) throw new Error('offer doc missing from registry');
    renderDoc('/legal/offer');

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(offer.title);
    expect(screen.getByText(/Редакция от 2026-08-20/)).toBeInTheDocument();
    for (const section of offer.sections) {
      if (section.heading !== '') {
        // Each section heading appears twice: in the table of contents and above the section.
        expect(screen.getAllByText(section.heading).length).toBeGreaterThanOrEqual(2);
      }
      expect(screen.getByText(section.paragraphs[0])).toBeInTheDocument();
    }
  });

  it('renders the table of contents with anchor entries for numbered sections', () => {
    useLanguageStore.setState({ language: 'ru' });
    const offer = getLegalDoc('offer');
    if (!offer) throw new Error('offer doc missing from registry');
    renderDoc('/legal/offer');

    expect(screen.getByText('Содержание')).toBeInTheDocument();
    const tocButtons = screen
      .getAllByRole('button')
      .filter((button) => offer.sections.some((s) => s.heading === button.textContent));
    expect(tocButtons.length).toBe(offer.sections.filter((s) => s.heading !== '').length);
  });

  it('anchors sections by id so the contents can target them', () => {
    useLanguageStore.setState({ language: 'ru' });
    const { container } = renderDoc('/legal/policy');
    const policy = getLegalDoc('policy');
    if (!policy) throw new Error('policy doc missing from registry');

    for (const section of policy.sections) {
      expect(container.querySelector(`#${section.id}`)).not.toBeNull();
    }
  });

  it('shows the russian-only notice only in the uz locale', () => {
    useLanguageStore.setState({ language: 'uz' });
    renderDoc('/legal/terms');
    expect(screen.getByText(translations.uz.legal.ruOnlyNotice)).toBeInTheDocument();
  });

  it('hides the russian-only notice in the ru locale', () => {
    useLanguageStore.setState({ language: 'ru' });
    renderDoc('/legal/terms');
    expect(screen.queryByText(translations.ru.legal.ruOnlyNotice)).not.toBeInTheDocument();
  });

  it('redirects an unknown slug back to the documents list', () => {
    useLanguageStore.setState({ language: 'ru' });
    renderDoc('/legal/unknown-doc');
    expect(screen.getByText('LEGAL_LIST')).toBeInTheDocument();
  });
});
