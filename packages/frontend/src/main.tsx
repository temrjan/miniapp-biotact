import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import WebApp from '@twa-dev/sdk';

import { App } from './App';
import './styles/global.css';

// Fullscreen только если параметр mode=fullscreen (Start App button)
// Inline кнопки открываются без параметра — стандартный размер
const urlParams = new URLSearchParams(window.location.search);
const mode = urlParams.get('mode');

if (mode === 'fullscreen') {
    try {
        if (window.Telegram?.WebApp?.requestFullscreen) {
            window.Telegram.WebApp.requestFullscreen();
        }
    } catch (error) {
        console.warn('Failed to request fullscreen:', error);
    }
}

// Вызвать ready() как можно раньше
WebApp.ready();

// Раскрыть на максимальную высоту
// ВАЖНО: режим определяется в index.html ДО этого вызова
WebApp.expand();

// Настройка цветов
const bgColor = '#f8f6f2';

WebApp.setHeaderColor(bgColor);
WebApp.setBackgroundColor(bgColor);

if (typeof WebApp.setBottomBarColor === 'function') {
  WebApp.setBottomBarColor(bgColor);
}

// --tg-header-height устанавливается в index.html
// НЕ перезаписываем здесь!

// Root element
const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Root element not found');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
