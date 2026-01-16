import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, Order, OrderStatus } from '../services/api';
import { Pagination } from '../components/common';

const PAGE_SIZE = 20;

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

export function Orders() {
  const navigate = useNavigate();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [statusFilter, setStatusFilter] = useState<OrderStatus | ''>('');
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [updating, setUpdating] = useState<number | null>(null);

  useEffect(() => {
    fetchOrders();
  }, [currentPage, statusFilter]);

  async function fetchOrders() {
    try {
      setLoading(true);
      const res = await api.getOrders({
        page: currentPage,
        size: PAGE_SIZE,
        status: statusFilter || undefined,
        search: searchInput || undefined,
      });
      setOrders(res.items);
      setTotalPages(res.pages || Math.ceil(res.total / PAGE_SIZE));
      setTotal(res.total);
    } catch (err) {
      setError('Ошибка загрузки заказов');
    } finally {
      setLoading(false);
    }
  }

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setCurrentPage(1);
    fetchOrders();
  }

  async function handleStatusChange(orderId: number, newStatus: OrderStatus) {
    try {
      setUpdating(orderId);
      await api.updateOrderStatus(orderId, newStatus);
      setSuccess('Статус обновлён');
      setTimeout(() => setSuccess(null), 1500);
      fetchOrders();
    } catch (err) {
      setError('Ошибка обновления статуса');
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
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function formatCurrency(amount: string) {
    return new Intl.NumberFormat('ru-RU').format(Number(amount)) + ' UZS';
  }

  if (loading && orders.length === 0) {
    return <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div>;
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h1 style={{ margin: 0 }}>Заказы</h1>
        <span style={{ color: '#666' }}>Всего: {total}</span>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
      {success && <div className="alert alert-success" style={{ marginBottom: 16 }}>{success}</div>}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
        <form onSubmit={handleSearch} style={{ display: 'flex', gap: 8, flex: 1, minWidth: 200, maxWidth: 400 }}>
          <input
            type="text"
            placeholder="Поиск по номеру, имени, телефону..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            style={{ flex: 1 }}
          />
          <button type="submit" className="btn">Поиск</button>
        </form>

        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value as OrderStatus | ''); setCurrentPage(1); }}
          style={{ minWidth: 150 }}
        >
          <option value="">Все статусы</option>
          {Object.entries(statusLabels).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>Номер</th>
              <th>Клиент</th>
              <th>Сумма</th>
              <th>Оплата</th>
              <th style={{ width: 160 }}>Статус</th>
              <th>Дата</th>
              <th style={{ width: 80 }}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: 40, color: '#666' }}>
                  Заказы не найдены
                </td>
              </tr>
            ) : (
              orders.map((order) => (
                <tr key={order.id}>
                  <td style={{ fontFamily: 'monospace', fontWeight: 500 }}>
                    {order.order_number}
                  </td>
                  <td>
                    <div>
                      <div style={{ fontWeight: 500 }}>{order.delivery_name || 'Без имени'}</div>
                      {order.delivery_phone && (
                        <div style={{ fontSize: 12, color: '#666' }}>{order.delivery_phone}</div>
                      )}
                    </div>
                  </td>
                  <td style={{ fontWeight: 500 }}>
                    {formatCurrency(order.total)}
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
                        {paymentLabels[order.payment_status] || order.payment_status}
                      </span>
                    ) : (
                      <span style={{ color: '#999' }}>-</span>
                    )}
                  </td>
                  <td>
                    <select
                      value={order.status}
                      onChange={(e) => handleStatusChange(order.id, e.target.value as OrderStatus)}
                      disabled={updating === order.id}
                      style={{
                        background: statusColors[order.status],
                        color: '#fff',
                        border: 'none',
                        padding: '4px 8px',
                        borderRadius: 4,
                        cursor: 'pointer',
                        fontSize: 13,
                      }}
                    >
                      {Object.entries(statusLabels).map(([value, label]) => (
                        <option key={value} value={value} style={{ background: '#fff', color: '#333' }}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td style={{ fontSize: 13, color: '#666' }}>
                    {formatDate(order.created_at)}
                  </td>
                  <td>
                    <button
                      className="btn btn-small"
                      onClick={() => navigate(`/orders/${order.id}`)}
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
