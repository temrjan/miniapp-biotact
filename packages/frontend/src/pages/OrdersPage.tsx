import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Package, ChevronRight, ShoppingBag, Clock, CheckCircle, XCircle, Truck } from 'lucide-react';
import { Card } from '@components/ui/Card';
import { useBackButton } from '@hooks/useBackButton';
import { useHaptic } from '@hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { api, ApiError } from '@/api/client';
import WebApp from '@twa-dev/sdk';
import { usePendingOrderStore } from '@/stores/pendingOrderStore';

interface OrderItem {
  id: number;
  product_id: number | null;
  product_name: string;
  product_price: number;
  quantity: number;
  total: number;
  size?: string | null;
}

interface Order {
  id: number;
  order_number: string;
  status: string;
  payment_status?: string;
  subtotal: number;
  delivery_fee: number;
  total: number;
  delivery_address: string | null;
  delivery_phone: string | null;
  created_at: string;
  items: OrderItem[];
}

interface OrdersResponse {
  items: Order[];
  total: number;
  page: number;
  size: number;
}

const statusIcons: Record<string, typeof Clock> = {
  pending: Clock,
  confirmed: CheckCircle,
  preparing: Package,
  delivering: Truck,
  completed: CheckCircle,
  cancelled: XCircle,
};

const statusColors: Record<string, string> = {
  pending: 'text-yellow-500',
  confirmed: 'text-blue-500',
  preparing: 'text-orange-500',
  delivering: 'text-purple-500',
  completed: 'text-green-500',
  cancelled: 'text-red-500',
};

export function OrdersPage() {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();
  const [orders, setOrders] = useState<Order[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [payingOrderId, setPayingOrderId] = useState<number | null>(null);
  const [paymentError, setPaymentError] = useState<string | null>(null);
  const creationUnknown = usePendingOrderStore((state) => state.creationUnknown);
  const pendingOrderId = usePendingOrderStore((state) => state.pending?.orderId);

  const resumePayment = async (order: Order) => {
    if (payingOrderId !== null) return;
    setPayingOrderId(order.id);
    setPaymentError(null);
    usePendingOrderStore.getState().rememberOrder(order.id, 'history');
    try {
      const payment = await api.post<{ payment_url: string }>('/payments/create', {
        order_id: order.id,
        return_url: window.location.origin + '/payment/success?order_id=' + order.order_number + '&id=' + order.id,
      });
      WebApp.openLink(payment.payment_url);
    } catch (error) {
      if (error instanceof ApiError && typeof error.data === 'object' && error.data !== null && 'detail' in error.data) {
        const detail = error.data.detail;
        if (typeof detail === 'object' && detail !== null && 'code' in detail && typeof detail.code === 'string' &&
          ['payment_processing', 'payment_outcome_unknown', 'payment_cancellation_pending', 'order_payment_closed'].includes(detail.code)) {
          setPaymentError(t(`checkout.${detail.code}`));
          if (detail.code === 'order_payment_closed') usePendingOrderStore.getState().forgetOrder();
          return;
        }
      }
      if (error instanceof ApiError && error.status === 400) {
        try {
          const current = await api.get<Order>(`/orders/${order.id}`);
          if (current.payment_status === 'paid') {
            navigate('/payment/success?order_id=' + current.order_number + '&id=' + current.id);
            return;
          }
        } catch { /* Preserve the saved order when its current result is unavailable. */ }
      }
      setPaymentError(t('checkout.payment_outcome_unknown'));
    } finally {
      setPayingOrderId(null);
    }
  };

  useBackButton({ isVisible: true });

  const formatPrice = (price: number): string => {
    return Math.floor(price).toLocaleString('ru-RU').replace(/,/g, ' ') + ' ' + t('currency.sum');
  };

  const formatDate = (dateStr: string): string => {
    const date = new Date(dateStr);
    return date.toLocaleDateString('ru-RU', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getStatusLabel = (status: string): string => {
    const statusMap: Record<string, string> = {
      pending: t('orders.status.pending'),
      confirmed: t('orders.status.confirmed'),
      preparing: t('orders.status.processing'),
      delivering: t('orders.status.shipped'),
      completed: t('orders.status.delivered'),
      cancelled: t('orders.status.cancelled'),
    };
    return statusMap[status] || status;
  };

  useEffect(() => {
    async function fetchOrders() {
      try {
        const data = await api.get<OrdersResponse>('/orders');
        setOrders(data.items || []);
      } catch {
        // On any error, show empty state
        setOrders([]);
      } finally {
        setIsLoading(false);
      }
    }
    fetchOrders();
  }, []);

  if (isLoading) {
    return (
      <div className="p-3 flex items-center justify-center min-h-[50vh] max-w-2xl mx-auto">
        <div className="animate-spin w-8 h-8 border-2 border-[var(--primary)] border-t-transparent rounded-full" />
      </div>
    );
  }

  // Show empty state when no orders
  if (orders.length === 0) {
    return (
      <div className="p-3 flex flex-col items-center justify-center min-h-[60vh] max-w-2xl mx-auto">
        <div className="neumorphic w-24 h-24 rounded-full flex items-center justify-center mb-4">
          <ShoppingBag size={40} className="text-[var(--text-muted)]" />
        </div>
        <h2 className="text-xl font-bold mb-2">Заказов пока нет</h2>
        <p className="text-[var(--text-muted)] text-center mb-4">Ваши заказы появятся здесь после оформления</p>
        <button
          onClick={() => navigate('/catalog')}
          className="px-6 py-3 bg-[var(--primary)] text-white rounded-xl font-semibold"
        >
          Перейти в каталог
        </button>
      </div>
    );
  }

  return (
    <div className="p-3 space-y-4 max-w-2xl mx-auto">
      <h1 className="text-xl font-bold">{t('orders.title')}</h1>
      {paymentError && <p role="alert">{paymentError}</p>}

      {orders.map((order, index) => {
        const sized = order.items.some((item) => item.size);
        const StatusIcon = statusIcons[order.status] || Clock;
        const statusColor = statusColors[order.status] || 'text-gray-500';

        return (
          <motion.div
            key={order.id}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.05 }}
          >
            <Card
              className="p-4 cursor-pointer"
              onClick={() => {
                haptic.light();
              }}
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="font-semibold">#{order.order_number}</p>
                  <p className="text-sm text-[var(--text-muted)]">{formatDate(order.created_at)}</p>
                </div>
                <div className={`flex items-center gap-1 ${statusColor}`}>
                  <StatusIcon size={16} />
                  <span className="text-sm font-medium">{getStatusLabel(order.status)}</span>
                </div>
              </div>

              <div className="space-y-2 mb-3">
                {(sized ? order.items : order.items.slice(0, 2)).map((item) => (
                  <div key={item.id} className="flex justify-between text-sm">
                    <span className="text-[var(--text-secondary)] truncate flex-1 mr-2">
                      {item.product_name}{item.size ? ` (${item.size})` : ''} x{item.quantity}
                    </span>
                    <span>{formatPrice(item.total)}</span>
                  </div>
                ))}
                {!sized && order.items.length > 2 && (
                  <p className="text-sm text-[var(--text-muted)]">
                    {t('orders.andMore', { count: order.items.length - 2 })}
                  </p>
                )}
              </div>

              <div className="flex items-center justify-between pt-3 border-t border-[var(--border)]">
                <span className="font-bold text-lg">{formatPrice(order.total)}</span>
                <ChevronRight size={20} className="text-[var(--text-muted)]" />
              </div>
              {sized && order.status !== 'cancelled' && (
                order.payment_status === 'paid' ?
                  ((creationUnknown || pendingOrderId === order.id) && <button onClick={() => {
                    usePendingOrderStore.getState().rememberOrder(order.id, 'history');
                    navigate('/payment/success?order_id=' + order.order_number + '&id=' + order.id);
                  }}>{t('orders.status.confirmed')}</button>) :
                  order.payment_status !== 'refunded' &&
                  <button disabled={payingOrderId !== null} onClick={() => void resumePayment(order)} className="mt-3 text-[var(--primary)]">
                    {t('orders.resumePayment')}
                  </button>
              )}
            </Card>
          </motion.div>
        );
      })}
    </div>
  );
}
