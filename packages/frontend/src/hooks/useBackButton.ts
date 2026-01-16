import { useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import WebApp from '@twa-dev/sdk';
import { useHaptic } from './useTelegram';

interface BackButtonOptions {
  onClick?: () => void;
  isVisible?: boolean;
}

interface BackButtonControls {
  show: () => void;
  hide: () => void;
}

export function useBackButton(options: BackButtonOptions = {}): BackButtonControls {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { onClick, isVisible = true } = options;

  const handleClick = useCallback(() => {
    haptic.light();
    
    if (onClick) {
      onClick();
    } else {
      navigate(-1);
    }
  }, [onClick, navigate, haptic]);

  useEffect(() => {
    const BackButton = WebApp.BackButton;
    
    if (isVisible) {
      BackButton.show();
      BackButton.onClick(handleClick);
    } else {
      BackButton.hide();
    }
    
    return () => {
      BackButton.offClick(handleClick);
      BackButton.hide();
    };
  }, [isVisible, handleClick]);

  return {
    show: () => WebApp.BackButton.show(),
    hide: () => WebApp.BackButton.hide(),
  };
}
