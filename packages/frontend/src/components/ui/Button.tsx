import React from 'react';
import { motion, type MotionProps } from 'framer-motion';
import { cn } from '@utils/cn';
import { useHaptic } from '@hooks/useTelegram';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
  fullWidth?: boolean;
  onClick?: () => void | Promise<void>;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary: 'bg-[var(--primary)] text-white hover:bg-[var(--primary-dark)]',
  secondary: 'bg-[var(--bg-tertiary)] text-[var(--text-primary)] hover:bg-[var(--bg-secondary)]',
  ghost: 'bg-transparent text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]',
  danger: 'bg-[var(--error)] text-white hover:opacity-90',
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-9 px-4 text-sm',
  md: 'h-11 px-5 text-base',
  lg: 'h-12 px-6 text-lg',
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = 'primary',
      size = 'md',
      isLoading = false,
      leftIcon,
      rightIcon,
      fullWidth = false,
      disabled,
      className,
      children,
      onClick,
      ...props
    },
    ref
  ) {
    const haptic = useHaptic();

    const handleClick = async () => {
      if (disabled || isLoading) return;
      haptic.light();
      await onClick?.();
    };

    return (
      <motion.button
        ref={ref}
        whileTap={{ scale: 0.93 }}
        disabled={disabled || isLoading}
        onClick={handleClick}
        className={cn(
          'flex items-center justify-center gap-2 rounded-lg font-medium transition-colors',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          variantClasses[variant],
          sizeClasses[size],
          fullWidth && 'w-full',
          className
        )}
        {...(props as MotionProps)}
      >
        {isLoading ? (
          <span className="animate-spin h-5 w-5 border-2 border-current border-t-transparent rounded-full" />
        ) : (
          <>
            {leftIcon}
            {children}
            {rightIcon}
          </>
        )}
      </motion.button>
    );
  }
);
