import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useSearchParams } from 'react-router-dom';

import { HomeOrActivate } from './App';

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
