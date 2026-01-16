import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Package, ChevronRight, ShoppingBag, Clock, CheckCircle, XCircle, Truck } from 'lucide-react';
import { Card } from '@components/ui/Card';
import { useBackButton } from '@hooks/useBackButton';
import { useHaptic } from '@hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { api } from '@/api/client';

interface OrderItem {
  id: number;
  product_id: number;
  product_name: string;
  product_price: number;
  quantity: number;
  total: number;
}

interface Order {
  id: number;
  order_number: string;
  status: string;
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
      <div className="p-4 flex items-center justify-center min-h-[50vh] max-w-2xl mx-auto">
        <div className="animate-spin w-8 h-8 border-2 border-[var(--primary)] border-t-transparent rounded-full" />
      </div>
    );
  }

  // Show empty state when no orders
  if (orders.length === 0) {
    return (
      <div className="p-4 flex flex-col items-center justify-center min-h-[60vh] max-w-2xl mx-auto">
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
    <div className="p-4 space-y-4 max-w-2xl mx-auto">
      <h1 className="text-xl font-bold">{t('orders.title')}</h1>

      {orders.map((order, index) => {
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
                {order.items.slice(0, 2).map((item) => (
                  <div key={item.id} className="flex justify-between text-sm">
                    <span className="text-[var(--text-secondary)] truncate flex-1 mr-2">
                      {item.product_name} x{item.quantity}
                    </span>
                    <span>{formatPrice(item.total)}</span>
                  </div>
                ))}
                {order.items.length > 2 && (
                  <p className="text-sm text-[var(--text-muted)]">
                    {t('orders.andMore', { count: order.items.length - 2 })}
                  </p>
                )}
              </div>

              <div className="flex items-center justify-between pt-3 border-t border-[var(--border)]">
                <span className="font-bold text-lg">{formatPrice(order.total)}</span>
                <ChevronRight size={20} className="text-[var(--text-muted)]" />
              </div>
            </Card>
          </motion.div>
        );
      })}
    </div>
  );
}
