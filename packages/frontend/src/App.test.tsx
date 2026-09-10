import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useSearchParams } from 'react-router-dom';
import { Suspense } from 'react';

import { isLegalEnabled } from '@/legal/flag';
import { HomeOrActivate, LegalRoutes } from './App';

// Renders the code the redirect target received, so we can assert it was preserved.
function CodeEcho() {
  const [params] = useSearchParams();
  return <div>code:{params.get('code')}</div>;
}

describe('HomeOrActivate', () => {
  it('redirects a ?code= launch to the activation route, preserving the code', () => {
    render(
      <MemoryRouter initialEntries={['/?code=ABC123']}>
        <Routes>
          <Route path="/" element={<HomeOrActivate />} />
          <Route path="/activate" element={<CodeEcho />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('code:ABC123')).toBeInTheDocument();
  });
});

// The suite runs twice in CI gates: default (flag OFF) and VITE_LEGAL_DOCS=true (ON).
// Each branch asserts the observable behavior of the state it runs in, so the OFF run
// is the regression shield ("a pasted link must NOT open a draft document") and the ON
// run proves the screens are reachable when the flag ships.
describe('LegalRoutes (VITE_LEGAL_DOCS gate)', () => {
  function renderAt(entry: string) {
    render(
      <MemoryRouter initialEntries={[entry]}>
        <Suspense fallback={null}>
          <Routes>
            <Route path="/" element={<div>HOME_SCREEN</div>} />
            {LegalRoutes()}
          </Routes>
        </Suspense>
      </MemoryRouter>
    );
  }

  it('gates a direct /legal/offer URL by the flag', async () => {
    renderAt('/legal/offer');

    if (isLegalEnabled()) {
      // ON: the document screen renders (lazy, hence findBy).
      expect(
        await screen.findByRole('heading', { level: 1, name: /оферта/i })
      ).toBeInTheDocument();
    } else {
      // OFF: no draft text is reachable — the URL lands on HOME.
      expect(await screen.findByText('HOME_SCREEN')).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /оферта/i })).not.toBeInTheDocument();
    }
  });

  it('gates the /legal list by the flag', async () => {
    renderAt('/legal');

    if (isLegalEnabled()) {
      expect(await screen.findByRole('heading', { level: 1, name: 'Документы' })).toBeInTheDocument();
    } else {
      expect(await screen.findByText('HOME_SCREEN')).toBeInTheDocument();
    }
  });
});
