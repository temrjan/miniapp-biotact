import { useEffect, lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useSearchParams } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { MainLayout } from '@components/layouts/MainLayout';
import { Loading } from '@components/ui/Loading';
import { LocationModal } from '@components/features/LocationModal';
import { useClearCartOnPaidOrder } from '@hooks/useClearCartOnPaidOrder';
import { useAppStore } from '@stores/appStore';
import { ROUTES } from '@utils/constants';
import { isLegalEnabled } from '@/legal/flag';

// ═══════════════════════════════════════════════════════════════════
// Lazy load pages
// ═══════════════════════════════════════════════════════════════════
const HomePage = lazy(() => import('@pages/HomePage').then((m) => ({ default: m.HomePage })));
const CatalogPage = lazy(() => import('@pages/CatalogPage').then((m) => ({ default: m.CatalogPage })));
const ProductPage = lazy(() => import('@pages/ProductPage').then((m) => ({ default: m.ProductPage })));
const CartPage = lazy(() => import('@pages/CartPage').then((m) => ({ default: m.CartPage })));
const ProfilePage = lazy(() => import('@pages/ProfilePage').then((m) => ({ default: m.ProfilePage })));
const CheckoutPage = lazy(() => import('@pages/CheckoutPage').then((m) => ({ default: m.CheckoutPage })));
const PaymentSuccessPage = lazy(() => import('@pages/PaymentSuccessPage').then((m) => ({ default: m.PaymentSuccessPage })));
const OrdersPage = lazy(() => import('@pages/OrdersPage').then((m) => ({ default: m.OrdersPage })));
const RamadanPage = lazy(() => import('@pages/RamadanPage').then((m) => ({ default: m.RamadanPage })));
const ActivationPage = lazy(() => import('@pages/ActivationPage').then((m) => ({ default: m.ActivationPage })));
const LegalListPage = lazy(() => import('@pages/LegalListPage').then((m) => ({ default: m.LegalListPage })));
const LegalDocPage = lazy(() => import('@pages/LegalDocPage').then((m) => ({ default: m.LegalDocPage })));

// The bot QR launcher opens `/?code=<code>` (start_param is not delivered for web_app buttons),
// so a code on the home path is routed to the dedicated activation screen; a bare `/` = home feed.
// Exported for unit testing the redirect (the feature's single entry point).
export function HomeOrActivate() {
  const [searchParams] = useSearchParams();
  const code = searchParams.get('code');
  if (code) {
    return <Navigate to={`${ROUTES.ACTIVATE}?code=${encodeURIComponent(code)}`} replace />;
  }
  return <HomePage />;
}

// Legal docs live behind the build-time flag VITE_LEGAL_DOCS. OFF = the screens are unreachable
// even by direct URL (a pasted /legal/offer link lands on HOME, not on a draft text).
// Exported for unit testing the flag gate in both states.
export function LegalRoutes() {
  if (!isLegalEnabled()) {
    return (
      <>
        <Route path={ROUTES.LEGAL} element={<Navigate to={ROUTES.HOME} replace />} />
        <Route path={ROUTES.LEGAL_DOC} element={<Navigate to={ROUTES.HOME} replace />} />
      </>
    );
  }
  return (
    <>
      <Route path={ROUTES.LEGAL} element={<LegalListPage />} />
      <Route path={ROUTES.LEGAL_DOC} element={<LegalDocPage />} />
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Query Client
// ═══════════════════════════════════════════════════════════════════
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

// ═══════════════════════════════════════════════════════════════════
// App Component
// ═══════════════════════════════════════════════════════════════════
export function App() {
  const initApp = useAppStore((state) => state.initApp);

  useEffect(() => {
    initApp();
  }, [initApp]);

  // Оплата картой уводит человека из мини-аппа, и экран «оплата прошла» со своей очисткой корзины
  // открывается в браузере. Здесь приложение узнаёт об оплате само — на старте и при возврате.
  useClearCartOnPaidOrder();

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <MainLayout>
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path={ROUTES.HOME} element={<HomeOrActivate />} />
              <Route path={ROUTES.ACTIVATE} element={<ActivationPage />} />
              <Route path={ROUTES.CATALOG} element={<CatalogPage />} />
              <Route path={ROUTES.CATEGORY} element={<CatalogPage />} />
              <Route path={ROUTES.PRODUCT} element={<ProductPage />} />
              <Route path={ROUTES.CART} element={<CartPage />} />
              <Route path={ROUTES.CHECKOUT} element={<CheckoutPage />} />
              <Route path={ROUTES.PROFILE} element={<ProfilePage />} />
              <Route path={ROUTES.ORDERS} element={<OrdersPage />} />
              <Route path={ROUTES.RAMADAN} element={<RamadanPage />} />
              <Route path={ROUTES.PAYMENT_SUCCESS} element={<PaymentSuccessPage />} />
              {LegalRoutes()}
            </Routes>
          </Suspense>
        </MainLayout>

        {/* Global modals */}
        <LocationModal />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
