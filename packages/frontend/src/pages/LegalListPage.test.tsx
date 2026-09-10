import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import { LEGAL_DOCS, getLegalDoc, buildConsentVersion } from '@/legal';
import { LEGAL_CONTACTS } from '@/legal/contacts';
import { translations } from '@/i18n';
import { LegalListPage } from './LegalListPage';

function renderList() {
  return render(
    <MemoryRouter initialEntries={['/legal']}>
      <Routes>
        <Route path="/legal" element={<LegalListPage />} />
        <Route path="/legal/:slug" element={<div>DOC_SCREEN</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('LegalListPage', () => {
  it('lists all three legal documents with their revision dates', () => {
    renderList();

    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);
    for (const doc of LEGAL_DOCS) {
      expect(screen.getByText(doc.title)).toBeInTheDocument();
    }
    expect(screen.getAllByText(/Редакция от 2026-08-20/)).toHaveLength(3);
  });

  it('shows the section-16 contacts block', () => {
    renderList();

    const supportLine = translations.ru.legal.contactsSupport.replace(
      '{bot}',
      LEGAL_CONTACTS.supportBot
    );
    expect(screen.getByText(supportLine)).toBeInTheDocument();
    expect(screen.getByText(LEGAL_CONTACTS.email)).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(LEGAL_CONTACTS.phones[0].replace(/[+]/g, '\\+')))
    ).toBeInTheDocument();
  });

  it('navigates to the document screen on click', async () => {
    renderList();

    fireEvent.click(screen.getByText(getLegalDoc('offer')?.title ?? 'offer'));
    expect(await screen.findByText('DOC_SCREEN')).toBeInTheDocument();
  });
});

describe('legal registry', () => {
  it('keeps every doc version within the 18-char budget', () => {
    // The composite must fit the backend String(50) — the per-doc budget is what
    // guarantees that, so it is asserted here and not eyeballed at review.
    for (const doc of LEGAL_DOCS) {
      expect(doc.version.length).toBeLessThanOrEqual(18);
    }
  });

  it('builds the composite consent version from the content files within 50 chars', () => {
    const composite = buildConsentVersion();
    const offer = getLegalDoc('offer');
    const policy = getLegalDoc('policy');

    expect(composite).toBe(`offer:${offer?.version}+policy:${policy?.version}`);
    expect(composite.length).toBeLessThanOrEqual(50);
  });

  it('resolves docs by slug and returns undefined for an unknown slug', () => {
    expect(getLegalDoc('terms')?.slug).toBe('terms');
    expect(getLegalDoc('nope')).toBeUndefined();
  });
});
