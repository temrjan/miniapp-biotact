import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { LoginPage } from '../LoginPage';
import Dashboard from '../Dashboard';
import { Products } from '../Products';
import Categories from '../Categories';
import Banners from '../Banners';

// Mock API
vi.mock('../services/api', () => ({
  api: {
    getCategories: vi.fn(() =>
      Promise.resolve({ items: [], total: 0, page: 1, limit: 10 })
    ),
    getProducts: vi.fn(() =>
      Promise.resolve({ items: [], total: 0, page: 1, limit: 10 })
    ),
    getBanners: vi.fn(() =>
      Promise.resolve({ items: [], total: 0, page: 1, limit: 10 })
    ),
  },
}));

describe('Admin Panel Pages - Smoke Tests', () => {
  it('LoginPage renders without crashing', () => {
    const { container } = render(
      <BrowserRouter>
        <LoginPage />
      </BrowserRouter>
    );
    expect(container).toBeTruthy();
  });

  it('Dashboard renders without crashing', () => {
    const { container } = render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );
    expect(container).toBeTruthy();
  });

  it('Products renders without crashing', () => {
    const { container } = render(
      <BrowserRouter>
        <Products />
      </BrowserRouter>
    );
    expect(container).toBeTruthy();
  });

  it('Categories renders without crashing', () => {
    const { container } = render(
      <BrowserRouter>
        <Categories />
      </BrowserRouter>
    );
    expect(container).toBeTruthy();
  });

  it('Banners renders without crashing', () => {
    const { container } = render(
      <BrowserRouter>
        <Banners />
      </BrowserRouter>
    );
    expect(container).toBeTruthy();
  });
});
