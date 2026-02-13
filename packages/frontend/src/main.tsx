import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import WebApp from '@twa-dev/sdk';

import { App } from './App';
import './styles/global.css';

// ═══════════════════════════════════════════════════════════════════
// Viewport Height Fix for Telegram WebApp
// ═══════════════════════════════════════════════════════════════════

function setAppHeight() {
  // Use Telegram viewport height if available, otherwise window.innerHeight
  const height = WebApp.viewportStableHeight || WebApp.viewportHeight || window.innerHeight;
  document.documentElement.style.setProperty('--app-height', `${height}px`);
}

// Set initial height
setAppHeight();

// Update on viewport changes (Telegram)
WebApp.onEvent('viewportChanged', setAppHeight);

// Update on resize (fallback for web)
window.addEventListener('resize', setAppHeight);

// ═══════════════════════════════════════════════════════════════════
// Telegram WebApp Initialization
// ═══════════════════════════════════════════════════════════════════

// Вызвать ready() как можно раньше
WebApp.ready();

// Раскрыть на максимальную высоту
WebApp.expand();

// Отключить вертикальные свайпы (закрытие жестом)
if (typeof WebApp.disableVerticalSwipes === 'function') {
  WebApp.disableVerticalSwipes();
}

// Настройка цветов
const bgColor = '#f5f7fa';

WebApp.setHeaderColor(bgColor);
WebApp.setBackgroundColor(bgColor);

if (typeof WebApp.setBottomBarColor === 'function') {
  WebApp.setBottomBarColor(bgColor);
}

// ═══════════════════════════════════════════════════════════════════
// React App Mount
// ═══════════════════════════════════════════════════════════════════

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Root element not found');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
