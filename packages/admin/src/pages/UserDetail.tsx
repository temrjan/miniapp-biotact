import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api, UserDetail, UserOrder } from '../services/api';

const statusLabels: Record<string, string> = {
  pending: 'Ожидает',
  confirmed: 'Подтверждён',
  preparing: 'Готовится',
  ready: 'Готов',
  delivering: 'Доставляется',
  completed: 'Завершён',
  cancelled: 'Отменён',
};

const statusColors: Record<string, string> = {
  pending: '#ffc107',
  confirmed: '#2196f3',
  preparing: '#9c27b0',
  ready: '#00bcd4',
  delivering: '#ff9800',
  completed: '#4caf50',
  cancelled: '#f44336',
};

export function UserDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const userId = Number(id);

  const [user, setUser] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);

  useEffect(() => {
    fetchUser();
  }, [userId]);

  async function fetchUser() {
    try {
      setLoading(true);
      const data = await api.getUser(userId);
      setUser(data);
    } catch (err) {
      setError('Пользователь не найден');
    } finally {
      setLoading(false);
    }
  }

  async function handleToggleActive() {
    if (!user) return;
    try {
      setUpdating(true);
      await api.updateUser(user.id, { is_active: !user.is_active });
      setSuccess('Обновлено');
      setTimeout(() => setSuccess(null), 1500);
      fetchUser();
    } catch (err) {
      setError('Ошибка обновления');
    } finally {
      setUpdating(false);
    }
  }

  async function handleToggleAdmin() {
    if (!user) return;
    try {
      setUpdating(true);
      await api.updateUser(user.id, { is_admin: !user.is_admin });
      setSuccess('Обновлено');
      setTimeout(() => setSuccess(null), 1500);
      fetchUser();
    } catch (err) {
      setError('Ошибка обновления');
    } finally {
      setUpdating(false);
    }
  }

  function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    });
  }

  function formatDateTime(dateStr: string) {
    return new Date(dateStr).toLocaleDateString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function formatCurrency(amount: string) {
    return new Intl.NumberFormat('ru-RU').format(Number(amount)) + ' UZS';
  }

  function getDisplayName() {
    if (!user) return '';
    if (user.first_name || user.last_name) {
      return [user.first_name, user.last_name].filter(Boolean).join(' ');
    }
    return user.username || 'Без имени';
  }

  function getFullAddress() {
    if (!user) return null;
    const parts = [user.address_street, user.address_house, user.address_apartment].filter(Boolean);
    return parts.length > 0 ? parts.join(', ') : null;
  }

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div>;
  }

  if (error || !user) {
    return (
      <div style={{ padding: 24 }}>
        <div className="alert alert-error" style={{ marginBottom: 16 }}>
          {error || 'Пользователь не найден'}
        </div>
        <button className="btn" onClick={() => navigate('/users')}>
          ← Назад к списку
        </button>
      </div>
    );
  }

  const cardStyle = {
    background: '#fff',
    border: '1px solid #e0e0e0',
    borderRadius: 8,
    padding: 20,
    marginBottom: 16,
  };

  const labelStyle = { color: '#666', fontSize: 13, marginBottom: 4 };
  const valueStyle = { fontWeight: 500 };

  return (
    <div style={{ padding: 24 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <button className="btn" onClick={() => navigate('/users')}>←</button>
        <div style={{
          width: 48,
          height: 48,
          borderRadius: '50%',
          background: user.is_admin ? '#e3f2fd' : '#f5f5f5',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 24,
        }}>
          {user.is_admin ? '👑' : '👤'}
        </div>
        <div>
          <h1 style={{ margin: 0 }}>{getDisplayName()}</h1>
          {user.username && <div style={{ color: '#666' }}>@{user.username}</div>}
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
      {success && <div className="alert alert-success" style={{ marginBottom: 16 }}>{success}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* User Info */}
        <div style={cardStyle}>
          <h3 style={{ margin: '0 0 16px 0' }}>Информация о пользователе</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div>
              <div style={labelStyle}>ID</div>
              <div style={{ ...valueStyle, fontFamily: 'monospace' }}>#{user.id}</div>
            </div>
            <div>
              <div style={labelStyle}>Telegram ID</div>
              <div style={{ ...valueStyle, fontFamily: 'monospace' }}>{user.telegram_id}</div>
            </div>
          </div>
          {user.phone && (
            <div style={{ marginTop: 16 }}>
              <div style={labelStyle}>Телефон</div>
              <div style={valueStyle}>{user.phone}</div>
            </div>
          )}
          <div style={{ marginTop: 16 }}>
            <div style={labelStyle}>Регистрация</div>
            <div style={valueStyle}>{formatDate(user.created_at)}</div>
          </div>
          {getFullAddress() && (
            <div style={{ marginTop: 16 }}>
              <div style={labelStyle}>Адрес</div>
              <div style={valueStyle}>{getFullAddress()}</div>
            </div>
          )}
          <div style={{ marginTop: 16 }}>
            <div style={labelStyle}>Язык</div>
            <div style={valueStyle}>{user.language_code}</div>
          </div>
        </div>

        {/* Status & Stats */}
        <div style={cardStyle}>
          <h3 style={{ margin: '0 0 16px 0' }}>Статус и статистика</h3>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span>Активен</span>
            <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={user.is_active}
                onChange={handleToggleActive}
                disabled={updating}
                style={{ width: 20, height: 20 }}
              />
            </label>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span>Администратор</span>
            <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={user.is_admin}
                onChange={handleToggleAdmin}
                disabled={updating}
                style={{ width: 20, height: 20 }}
              />
            </label>
          </div>
          <div style={{ borderTop: '1px solid #e0e0e0', paddingTop: 16, marginTop: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <div style={{ textAlign: 'center', padding: 16, background: '#f5f5f5', borderRadius: 8 }}>
                <div style={{ fontSize: 24, fontWeight: 'bold' }}>{user.orders_count}</div>
                <div style={{ color: '#666', fontSize: 13 }}>Заказов</div>
              </div>
              <div style={{ textAlign: 'center', padding: 16, background: '#f5f5f5', borderRadius: 8 }}>
                <div style={{ fontSize: 18, fontWeight: 'bold' }}>{formatCurrency(user.total_spent)}</div>
                <div style={{ color: '#666', fontSize: 13 }}>Потрачено</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Orders History */}
      <div style={cardStyle}>
        <h3 style={{ margin: '0 0 16px 0' }}>История заказов</h3>
        {user.recent_orders.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#666' }}>
            Заказов пока нет
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Номер</th>
                <th>Дата</th>
                <th>Статус</th>
                <th>Оплата</th>
                <th style={{ textAlign: 'right' }}>Сумма</th>
              </tr>
            </thead>
            <tbody>
              {user.recent_orders.map((order: UserOrder) => (
                <tr key={order.id}>
                  <td style={{ fontFamily: 'monospace' }}>{order.order_number}</td>
                  <td style={{ color: '#666' }}>{formatDateTime(order.created_at)}</td>
                  <td>
                    <span style={{
                      background: statusColors[order.status] || '#9e9e9e',
                      color: '#fff',
                      padding: '2px 8px',
                      borderRadius: 4,
                      fontSize: 12,
                    }}>
                      {statusLabels[order.status] || order.status}
                    </span>
                  </td>
                  <td>
                    {order.payment_status ? (
                      <span style={{
                        background: order.payment_status === 'paid' ? '#4caf50' : '#9e9e9e',
                        color: '#fff',
                        padding: '2px 8px',
                        borderRadius: 4,
                        fontSize: 12,
                      }}>
                        {order.payment_status === 'paid' ? 'Оплачен' : order.payment_status}
                      </span>
                    ) : (
                      <span style={{ color: '#999' }}>-</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 500 }}>
                    {formatCurrency(order.total)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
