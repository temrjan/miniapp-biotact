import { motion } from 'framer-motion';
import { cn } from '@utils/cn';

interface LoadingProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function Loading({ size = 'md', className }: LoadingProps) {
  const sizeClasses = {
    sm: 'h-4 w-4',
    md: 'h-8 w-8',
    lg: 'h-12 w-12',
  };

  return (
    <div
      className={cn(
        'flex items-center justify-center min-h-[200px]',
        className
      )}
    >
      <motion.div
        className={cn(
          'rounded-full border-2 border-t-transparent',
          sizeClasses[size]
        )}
        style={{
          borderColor: 'var(--primary)',
          borderTopColor: 'transparent',
        }}
        animate={{ rotate: 360 }}
        transition={{
          duration: 1,
          repeat: Infinity,
          ease: 'linear',
        }}
      />
    </div>
  );
}
