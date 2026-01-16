import { useState } from 'react';
import { Routes, Route, NavLink, useNavigate } from 'react-router-dom';
import { useAuthStore } from './stores/authStore';
import { PrivateRoute } from './components/PrivateRoute';
import { LoginPage } from './pages/LoginPage';
import { RegisterPage } from './pages/RegisterPage';
import { VerifyEmailPage } from './pages/VerifyEmailPage';
import Dashboard from './pages/Dashboard';
import Categories from './pages/Categories';
import { Products } from './pages/Products';
import Banners from './pages/Banners';
import { Users } from './pages/Users';
import { UserDetailPage } from './pages/UserDetail';
import { Orders } from './pages/Orders';
import { OrderDetailPage } from './pages/OrderDetail';
import { SettingsPage } from './pages/Settings';

function AdminLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();
  const { user, logout } = useAuthStore();

  const handleNavClick = () => setMenuOpen(false);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <div className="admin-layout">
      <header className="mobile-header">
        <button className="hamburger" onClick={() => setMenuOpen(!menuOpen)}>
          {menuOpen ? '\u2715' : '\u2630'}
        </button>
        <span className="mobile-title">Biotact Admin</span>
      </header>

      <aside className={'sidebar ' + (menuOpen ? 'open' : '')}>
        <h1>Biotact Admin</h1>
        <nav>
          <NavLink to="/" end onClick={handleNavClick}>Dashboard</NavLink>
          <NavLink to="/orders" onClick={handleNavClick}>Заказы</NavLink>
          <NavLink to="/categories" onClick={handleNavClick}>Категории</NavLink>
          <NavLink to="/products" onClick={handleNavClick}>Товары</NavLink>
          <NavLink to="/banners" onClick={handleNavClick}>Баннеры</NavLink>
          <NavLink to="/users" onClick={handleNavClick}>Пользователи</NavLink>
          <NavLink to="/settings" onClick={handleNavClick}>Настройки</NavLink>
        </nav>

        <div className="sidebar-footer">
          <div className="user-info">
            <span className="user-name">
              {user?.first_name || user?.email}
            </span>
            {user?.email && user?.first_name && (
              <span className="user-email">{user.email}</span>
            )}
          </div>
          <button className="logout-btn" onClick={handleLogout}>
            Выйти
          </button>
        </div>
      </aside>

      {menuOpen && <div className="sidebar-overlay" onClick={() => setMenuOpen(false)} />}

      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/orders/:id" element={<OrderDetailPage />} />
          <Route path="/categories" element={<Categories />} />
          <Route path="/products" element={<Products />} />
          <Route path="/banners" element={<Banners />} />
          <Route path="/users" element={<Users />} />
          <Route path="/users/:id" element={<UserDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route
        path="/*"
        element={
          <PrivateRoute>
            <AdminLayout />
          </PrivateRoute>
        }
      />
    </Routes>
  );
}

export default App;
