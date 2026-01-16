import { useRef, useEffect, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Home, Grid3X3, ShoppingCart, User } from 'lucide-react';
import { useHaptic } from '@hooks/useTelegram';
import { useCartTotalItems } from '@stores/cartStore';
import { useTranslation } from '@/i18n';
import { ROUTES } from '@utils/constants';

type IconComponentType = React.ElementType<{ className?: string }>;

interface MenuItem {
  path: string;
  labelKey: 'nav.home' | 'nav.catalog' | 'nav.cart' | 'nav.profile';
  icon: IconComponentType;
}

const menuItems: MenuItem[] = [
  { path: ROUTES.HOME, labelKey: 'nav.home', icon: Home },
  { path: ROUTES.CATALOG, labelKey: 'nav.catalog', icon: Grid3X3 },
  { path: ROUTES.CART, labelKey: 'nav.cart', icon: ShoppingCart },
  { path: ROUTES.PROFILE, labelKey: 'nav.profile', icon: User },
];

export function InteractiveMenu() {
  const location = useLocation();
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();
  const cartItemsCount = useCartTotalItems();

  const textRefs = useRef<(HTMLElement | null)[]>([]);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Определяем активный индекс по текущему пути
  const activeIndex = useMemo(() => {
    const index = menuItems.findIndex((item) => {
      if (item.path === ROUTES.CATALOG) {
        return location.pathname === item.path || location.pathname.startsWith('/catalog/');
      }
      if (item.path === ROUTES.CART) {
        return location.pathname === item.path || location.pathname.startsWith('/cart');
      }
      return location.pathname === item.path;
    });
    return index >= 0 ? index : 0;
  }, [location.pathname]);

  // Устанавливаем ширину линии под активным элементом
  useEffect(() => {
    const setLineWidth = () => {
      const activeItemElement = itemRefs.current[activeIndex];
      const activeTextElement = textRefs.current[activeIndex];

      if (activeItemElement && activeTextElement) {
        const textWidth = activeTextElement.offsetWidth;
        activeItemElement.style.setProperty('--lineWidth', `${textWidth}px`);
      }
    };

    setLineWidth();

    window.addEventListener('resize', setLineWidth);
    return () => {
      window.removeEventListener('resize', setLineWidth);
    };
  }, [activeIndex]);

  const handleItemClick = (path: string) => {
    haptic.selection();
    navigate(path);
  };

  return (
    <nav className="menu" role="navigation">
      {menuItems.map((item, index) => {
        const isActive = index === activeIndex;
        const IconComponent = item.icon;
        const showBadge = item.path === ROUTES.CART && cartItemsCount > 0;

        return (
          <button
            key={item.path}
            className={`menu__item ${isActive ? 'active' : ''}`}
            onClick={() => handleItemClick(item.path)}
            ref={(el) => { itemRefs.current[index] = el; }}
            style={{ '--lineWidth': '0px' } as React.CSSProperties}
          >
            <div className="menu__icon">
              <IconComponent className="icon" />
            </div>
            {showBadge && (
              <span className="menu__badge">
                {cartItemsCount > 99 ? '99+' : cartItemsCount}
              </span>
            )}
            <strong
              className={`menu__text ${isActive ? 'active' : ''}`}
              ref={(el) => { textRefs.current[index] = el; }}
            >
              {t(item.labelKey)}
            </strong>
          </button>
        );
      })}
    </nav>
  );
}
