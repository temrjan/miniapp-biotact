import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api, Order, OrderStatus, OrderItem } from '../services/api';

const statusLabels: Record<OrderStatus, string> = {
  pending: 'Ожидает',
  confirmed: 'Подтверждён',
  processing: 'В обработке',
  shipped: 'Отправлен',
  delivered: 'Доставлен',
  cancelled: 'Отменён',
};

const statusColors: Record<OrderStatus, string> = {
  pending: '#ffc107',
  confirmed: '#2196f3',
  processing: '#9c27b0',
  shipped: '#ff9800',
  delivered: '#4caf50',
  cancelled: '#f44336',
};

const paymentLabels: Record<string, string> = {
  unpaid: 'Не оплачен',
  paid: 'Оплачен',
  refunded: 'Возврат',
};

export function OrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const orderId = Number(id);

  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);

  useEffect(() => {
    fetchOrder();
  }, [orderId]);

  async function fetchOrder() {
    try {
      setLoading(true);
      const data = await api.getOrder(orderId);
      setOrder(data);
    } catch (err) {
      setError('Заказ не найден');
    } finally {
      setLoading(false);
    }
  }

  async function handleStatusChange(newStatus: OrderStatus) {
    if (!order) return;
    try {
      setUpdating(true);
      await api.updateOrderStatus(order.id, newStatus);
      setSuccess('Статус обновлён');
      setTimeout(() => setSuccess(null), 1500);
      fetchOrder();
    } catch (err) {
      setError('Ошибка обновления статуса');
    } finally {
      setUpdating(false);
    }
  }

  function formatDate(dateStr: string) {
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

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div>;
  }

  if (error || !order) {
    return (
      <div style={{ padding: 24 }}>
        <div className="alert alert-error" style={{ marginBottom: 16 }}>
          {error || 'Заказ не найден'}
        </div>
        <button className="btn" onClick={() => navigate('/orders')}>
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
        <button className="btn" onClick={() => navigate('/orders')}>←</button>
        <div style={{ flex: 1 }}>
          <h1 style={{ margin: 0 }}>Заказ {order.order_number}</h1>
          <span style={{ color: '#666' }}>от {formatDate(order.created_at)}</span>
        </div>
        <select
          value={order.status}
          onChange={(e) => handleStatusChange(e.target.value as OrderStatus)}
          disabled={updating}
          style={{
            background: statusColors[order.status],
            color: '#fff',
            border: 'none',
            padding: '8px 16px',
            borderRadius: 8,
            cursor: 'pointer',
            fontSize: 14,
            fontWeight: 500,
          }}
        >
          {Object.entries(statusLabels).map(([value, label]) => (
            <option key={value} value={value} style={{ background: '#fff', color: '#333' }}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
      {success && <div className="alert alert-success" style={{ marginBottom: 16 }}>{success}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
        {/* Left column */}
        <div>
          {/* Order Items */}
          <div style={cardStyle}>
            <h3 style={{ margin: '0 0 16px 0' }}>📦 Товары</h3>
            <table className="table" style={{ margin: 0 }}>
              <thead>
                <tr>
                  <th>Товар</th>
                  <th style={{ textAlign: 'right' }}>Цена</th>
                  <th style={{ textAlign: 'center' }}>Кол-во</th>
                  <th style={{ textAlign: 'right' }}>Сумма</th>
                </tr>
              </thead>
              <tbody>
                {order.items.map((item: OrderItem) => (
                  <tr key={item.id}>
                    <td>{item.product_name}</td>
                    <td style={{ textAlign: 'right' }}>{formatCurrency(item.product_price)}</td>
                    <td style={{ textAlign: 'center' }}>{item.quantity}</td>
                    <td style={{ textAlign: 'right', fontWeight: 500 }}>{formatCurrency(item.total)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3} style={{ textAlign: 'right', color: '#666' }}>Подитог:</td>
                  <td style={{ textAlign: 'right' }}>{formatCurrency(order.subtotal)}</td>
                </tr>
                <tr>
                  <td colSpan={3} style={{ textAlign: 'right', color: '#666' }}>Доставка:</td>
                  <td style={{ textAlign: 'right' }}>{formatCurrency(order.delivery_fee)}</td>
                </tr>
                <tr style={{ fontWeight: 'bold', fontSize: 16 }}>
                  <td colSpan={3} style={{ textAlign: 'right' }}>Итого:</td>
                  <td style={{ textAlign: 'right' }}>{formatCurrency(order.total)}</td>
                </tr>
              </tfoot>
            </table>
          </div>

          {/* Notes */}
          {(order.customer_note || order.admin_note) && (
            <div style={cardStyle}>
              <h3 style={{ margin: '0 0 16px 0' }}>📝 Заметки</h3>
              {order.customer_note && (
                <div style={{ marginBottom: 12 }}>
                  <div style={labelStyle}>От клиента:</div>
                  <div style={{ ...valueStyle, background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
                    {order.customer_note}
                  </div>
                </div>
              )}
              {order.admin_note && (
                <div>
                  <div style={labelStyle}>Администратор:</div>
                  <div style={{ ...valueStyle, background: '#fff3e0', padding: 12, borderRadius: 4 }}>
                    {order.admin_note}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right column */}
        <div>
          {/* Customer Info */}
          <div style={cardStyle}>
            <h3 style={{ margin: '0 0 16px 0' }}>👤 Клиент</h3>
            <div style={{ marginBottom: 12 }}>
              <div style={labelStyle}>Имя</div>
              <div style={valueStyle}>{order.delivery_name || 'Не указано'}</div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <div style={labelStyle}>Телефон</div>
              <div style={valueStyle}>{order.delivery_phone || 'Не указан'}</div>
            </div>
            <div>
              <div style={labelStyle}>Адрес доставки</div>
              <div style={valueStyle}>{order.delivery_address || 'Не указан'}</div>
            </div>
          </div>

          {/* Payment Info */}
          <div style={cardStyle}>
            <h3 style={{ margin: '0 0 16px 0' }}>💳 Оплата</h3>
            <div style={{ marginBottom: 12 }}>
              <div style={labelStyle}>Статус оплаты</div>
              <span style={{
                display: 'inline-block',
                background: order.payment_status === 'paid' ? '#4caf50' : '#9e9e9e',
                color: '#fff',
                padding: '4px 12px',
                borderRadius: 4,
                fontSize: 13,
              }}>
                {order.payment_status ? paymentLabels[order.payment_status] || order.payment_status : 'Не указан'}
              </span>
            </div>
            {order.paid_at && (
              <div style={{ marginBottom: 12 }}>
                <div style={labelStyle}>Оплачен</div>
                <div style={valueStyle}>{formatDate(order.paid_at)}</div>
              </div>
            )}
            {order.payment_uuid && (
              <div>
                <div style={labelStyle}>ID транзакции</div>
                <div style={{ ...valueStyle, fontFamily: 'monospace', fontSize: 12 }}>{order.payment_uuid}</div>
              </div>
            )}
          </div>

          {/* Timeline */}
          <div style={cardStyle}>
            <h3 style={{ margin: '0 0 16px 0' }}>🕐 История</h3>
            <div style={{ marginBottom: 12 }}>
              <div style={labelStyle}>Создан</div>
              <div style={valueStyle}>{formatDate(order.created_at)}</div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <div style={labelStyle}>Обновлён</div>
              <div style={valueStyle}>{formatDate(order.updated_at)}</div>
            </div>
            {order.completed_at && (
              <div>
                <div style={labelStyle}>Завершён</div>
                <div style={valueStyle}>{formatDate(order.completed_at)}</div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
