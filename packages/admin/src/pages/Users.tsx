import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, User } from '../services/api';
import { Pagination } from '../components/common';

const PAGE_SIZE = 20;

export function Users() {
  const navigate = useNavigate();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [updating, setUpdating] = useState<number | null>(null);

  useEffect(() => {
    fetchUsers(currentPage, searchInput);
  }, [currentPage]);

  async function fetchUsers(page = 1, search?: string) {
    try {
      setLoading(true);
      const res = await api.getUsers(page, PAGE_SIZE, search || undefined);
      setUsers(res.items);
      setTotalPages(res.pages || Math.ceil(res.total / PAGE_SIZE));
      setTotal(res.total);
    } catch (err) {
      setError('Ошибка загрузки пользователей');
    } finally {
      setLoading(false);
    }
  }

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setCurrentPage(1);
    fetchUsers(1, searchInput);
  }

  async function handleToggleActive(user: User) {
    try {
      setUpdating(user.id);
      await api.updateUser(user.id, { is_active: !user.is_active });
      setSuccess('Обновлено');
      setTimeout(() => setSuccess(null), 1500);
      fetchUsers(currentPage, searchInput);
    } catch (err) {
      setError('Ошибка обновления');
    } finally {
      setUpdating(null);
    }
  }

  async function handleToggleAdmin(user: User) {
    try {
      setUpdating(user.id);
      await api.updateUser(user.id, { is_admin: !user.is_admin });
      setSuccess('Обновлено');
      setTimeout(() => setSuccess(null), 1500);
      fetchUsers(currentPage, searchInput);
    } catch (err) {
      setError('Ошибка обновления');
    } finally {
      setUpdating(null);
    }
  }

  function handlePageChange(page: number) {
    if (page >= 1 && page <= totalPages) {
      setCurrentPage(page);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }

  function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    });
  }

  function formatCurrency(amount: string) {
    return new Intl.NumberFormat('ru-RU').format(Number(amount)) + ' UZS';
  }

  function getDisplayName(user: User) {
    if (user.first_name || user.last_name) {
      return [user.first_name, user.last_name].filter(Boolean).join(' ');
    }
    return user.username || 'Без имени';
  }

  if (loading && users.length === 0) {
    return <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div>;
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h1 style={{ margin: 0 }}>Пользователи</h1>
        <span style={{ color: '#666' }}>Всего: {total}</span>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
      {success && <div className="alert alert-success" style={{ marginBottom: 16 }}>{success}</div>}

      {/* Search */}
      <form onSubmit={handleSearch} style={{ marginBottom: 16, display: 'flex', gap: 8, maxWidth: 400 }}>
        <input
          type="text"
          placeholder="Поиск по имени, username, телефону..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" className="btn">Поиск</button>
      </form>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 60 }}>ID</th>
              <th>Пользователь</th>
              <th>Телефон</th>
              <th style={{ width: 80 }}>Заказов</th>
              <th>Потрачено</th>
              <th style={{ width: 80 }}>Активен</th>
              <th style={{ width: 80 }}>Админ</th>
              <th>Регистрация</th>
              <th style={{ width: 100 }}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {users.length === 0 ? (
              <tr>
                <td colSpan={9} style={{ textAlign: 'center', padding: 40, color: '#666' }}>
                  Пользователи не найдены
                </td>
              </tr>
            ) : (
              users.map((user) => (
                <tr key={user.id}>
                  <td style={{ fontFamily: 'monospace' }}>#{user.id}</td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{
                        width: 32,
                        height: 32,
                        borderRadius: '50%',
                        background: user.is_admin ? '#e3f2fd' : '#f5f5f5',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 14,
                      }}>
                        {user.is_admin ? '👑' : '👤'}
                      </div>
                      <div>
                        <div style={{ fontWeight: 500 }}>{getDisplayName(user)}</div>
                        {user.username && (
                          <div style={{ fontSize: 12, color: '#666' }}>@{user.username}</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td>{user.phone || <span style={{ color: '#999' }}>-</span>}</td>
                  <td>
                    <span style={{
                      background: '#f0f0f0',
                      padding: '2px 8px',
                      borderRadius: 4,
                      fontSize: 12,
                    }}>
                      {user.orders_count}
                    </span>
                  </td>
                  <td style={{ fontWeight: 500 }}>{formatCurrency(user.total_spent)}</td>
                  <td>
                    <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={user.is_active}
                        onChange={() => handleToggleActive(user)}
                        disabled={updating === user.id}
                        style={{ width: 18, height: 18 }}
                      />
                    </label>
                  </td>
                  <td>
                    <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={user.is_admin}
                        onChange={() => handleToggleAdmin(user)}
                        disabled={updating === user.id}
                        style={{ width: 18, height: 18 }}
                      />
                    </label>
                  </td>
                  <td style={{ fontSize: 13, color: '#666' }}>{formatDate(user.created_at)}</td>
                  <td>
                    <button
                      className="btn btn-small"
                      onClick={() => navigate(`/users/${user.id}`)}
                      title="Подробнее"
                    >
                      👁
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        onPageChange={handlePageChange}
        disabled={loading}
      />
    </div>
  );
}
