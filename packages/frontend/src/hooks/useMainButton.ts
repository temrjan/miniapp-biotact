import { useEffect, useCallback } from 'react';
import WebApp from '@twa-dev/sdk';
import { useHaptic } from './useTelegram';

interface MainButtonOptions {
  text: string;
  onClick: () => void | Promise<void>;
  color?: string;
  textColor?: string;
  isVisible?: boolean;
  isActive?: boolean;
  isProgressVisible?: boolean;
}

interface MainButtonControls {
  show: () => void;
  hide: () => void;
  enable: () => void;
  disable: () => void;
  showProgress: () => void;
  hideProgress: () => void;
  setText: (text: string) => void;
}

export function useMainButton(options: MainButtonOptions): MainButtonControls {
  const {
    text,
    onClick,
    color,
    textColor,
    isVisible = true,
    isActive = true,
    isProgressVisible = false,
  } = options;

  const haptic = useHaptic();

  const handleClick = useCallback(async () => {
    haptic.medium();
    await onClick();
  }, [onClick, haptic]);

  useEffect(() => {
    const MainButton = WebApp.MainButton;
    
    MainButton.setText(text);
    
    if (color) {
      MainButton.color = color as `#${string}`;
    }
    if (textColor) {
      MainButton.textColor = textColor as `#${string}`;
    }
    
    MainButton.onClick(handleClick);
    
    if (isVisible) {
      MainButton.show();
    } else {
      MainButton.hide();
    }
    
    if (isActive) {
      MainButton.enable();
    } else {
      MainButton.disable();
    }
    
    if (isProgressVisible) {
      MainButton.showProgress();
    } else {
      MainButton.hideProgress();
    }
    
    return () => {
      MainButton.offClick(handleClick);
      MainButton.hide();
    };
  }, [text, color, textColor, isVisible, isActive, isProgressVisible, handleClick]);

  return {
    show: () => WebApp.MainButton.show(),
    hide: () => WebApp.MainButton.hide(),
    enable: () => WebApp.MainButton.enable(),
    disable: () => WebApp.MainButton.disable(),
    showProgress: () => WebApp.MainButton.showProgress(),
    hideProgress: () => WebApp.MainButton.hideProgress(),
    setText: (newText: string) => WebApp.MainButton.setText(newText),
  };
}
