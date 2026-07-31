import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryProvider } from './app/providers/QueryProvider'
import { ThemeProvider } from './app/providers/ThemeProvider'
import { ErrorBoundary } from './shared/components/common/ErrorBoundary'
import { PrivateRoute } from './app/routes/PrivateRoute'
import { MainLayout } from './app/layouts/MainLayout'
import { PageLoader } from './shared/components/common/PageLoader'
import './index.css'

// Lazy load all pages
const LoginPage = lazy(() => import('./features/auth/pages/LoginPage').then(m => ({ default: m.LoginPage })))
const DashboardPage = lazy(() => import('./features/dashboard/pages/DashboardPage').then(m => ({ default: m.DashboardPage })))
const ProductsListPage = lazy(() => import('./features/products/pages/ProductsListPage').then(m => ({ default: m.ProductsListPage })))
const ProductCreatePage = lazy(() => import('./features/products/pages/ProductCreatePage').then(m => ({ default: m.ProductCreatePage })))
const ProductEditPage = lazy(() => import('./features/products/pages/ProductEditPage').then(m => ({ default: m.ProductEditPage })))
const CategoriesListPage = lazy(() => import('./features/categories/pages/CategoriesListPage').then(m => ({ default: m.CategoriesListPage })))
const CategoryEditPage = lazy(() => import('./features/categories/pages/CategoryEditPage').then(m => ({ default: m.CategoryEditPage })))
const BannersListPage = lazy(() => import('./features/banners/pages/BannersListPage').then(m => ({ default: m.BannersListPage })))
const OrdersListPage = lazy(() => import('./features/orders/pages/OrdersListPage').then(m => ({ default: m.OrdersListPage })))
const OrderDetailsPage = lazy(() => import('./features/orders/pages/OrderDetailsPage').then(m => ({ default: m.OrderDetailsPage })))
const UsersListPage = lazy(() => import('./features/users/pages/UsersListPage').then(m => ({ default: m.UsersListPage })))
const UserDetailPage = lazy(() => import('./features/users/pages/UserDetailPage').then(m => ({ default: m.UserDetailPage })))
const SettingsPage = lazy(() => import('./features/settings/pages/SettingsPage').then(m => ({ default: m.SettingsPage })))
const CustomersPage = lazy(() => import('./features/customers/pages/CustomersPage').then(m => ({ default: m.CustomersPage })))
const BroadcastsPage = lazy(() => import('./features/broadcasts/pages/BroadcastsPage').then(m => ({ default: m.BroadcastsPage })))
const LoyaltyPage = lazy(() => import('./features/loyalty/pages/LoyaltyPage').then(m => ({ default: m.LoyaltyPage })))
const DrawPage = lazy(() => import('./features/loyalty/pages/DrawPage').then(m => ({ default: m.DrawPage })))
const DrawPrintPage = lazy(() => import('./features/loyalty/pages/DrawPrintPage').then(m => ({ default: m.DrawPrintPage })))

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider defaultTheme="system">
        <QueryProvider>
          <BrowserRouter>
            <Suspense fallback={<PageLoader />}>
              <Routes>
                <Route path='/login' element={<LoginPage />} />

                <Route
                  path='/dashboard'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <DashboardPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/products'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <ProductsListPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/products/new'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <ProductCreatePage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/products/:id/edit'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <ProductEditPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/categories'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <CategoriesListPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/categories/new'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <CategoryEditPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/categories/:id/edit'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <CategoryEditPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/banners'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <BannersListPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/orders'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <OrdersListPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/orders/:id'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <OrderDetailsPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/users'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <UsersListPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/users/:id'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <UserDetailPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/settings'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <SettingsPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/customers'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <CustomersPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/loyalty'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <LoyaltyPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/loyalty/draw'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <DrawPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/loyalty/draw/print/:drawId'
                  element={
                    <PrivateRoute>
                      <DrawPrintPage />
                    </PrivateRoute>
                  }
                />

                <Route
                  path='/broadcasts'
                  element={
                    <PrivateRoute>
                      <MainLayout>
                        <BroadcastsPage />
                      </MainLayout>
                    </PrivateRoute>
                  }
                />

                <Route path='/' element={<Navigate to='/dashboard' replace />} />
                <Route path='*' element={<Navigate to='/dashboard' replace />} />
              </Routes>
            </Suspense>
          </BrowserRouter>
        </QueryProvider>
      </ThemeProvider>
    </ErrorBoundary>
  )
}

export default App
