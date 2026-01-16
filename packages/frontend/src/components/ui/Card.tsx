import type { ReactNode, HTMLAttributes } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/utils/cn';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'raised' | 'flat' | 'inset';
  children: ReactNode;
  animate?: boolean;
}

export function Card({
  variant = 'raised',
  children,
  animate = false,
  className,
  onClick,
  ...props
}: CardProps) {
  const variantClasses = {
    raised: 'neumorphic',
    flat: 'neumorphic-flat',
    inset: 'neumorphic-inset',
  };

  if (animate) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className={cn(variantClasses[variant], 'p-2', className)}
        onClick={onClick}
      >
        {children}
      </motion.div>
    );
  }

  return (
    <div
      className={cn(variantClasses[variant], 'p-2', className)}
      onClick={onClick}
      {...props}
    >
      {children}
    </div>
  );
}
