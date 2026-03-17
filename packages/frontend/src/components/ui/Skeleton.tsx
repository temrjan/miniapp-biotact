import type { HTMLAttributes } from 'react';
import { cn } from '@utils/cn';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'text' | 'circular' | 'rectangular';
  width?: string | number;
  height?: string | number;
  animation?: 'pulse' | 'wave' | 'none';
}

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function Skeleton({
  className,
  variant = 'rectangular',
  width,
  height,
  animation = 'pulse',
  ...props
}: SkeletonProps) {
  const variantClasses = {
    text: 'rounded h-4',
    circular: 'rounded-full',
    rectangular: 'rounded-[var(--radius-md)]',
  };

  const animationClasses = {
    pulse: 'animate-pulse',
    wave: 'skeleton-wave',
    none: '',
  };

  return (
    <div
      className={cn(
        'bg-[var(--bg-tertiary)]',
        variantClasses[variant],
        animationClasses[animation],
        className
      )}
      style={{
        width: typeof width === 'number' ? `${width}px` : width,
        height: typeof height === 'number' ? `${height}px` : height,
      }}
      {...props}
    />
  );
}

// ═══════════════════════════════════════════════════════════════════
// Preset Components
// ═══════════════════════════════════════════════════════════════════

export function ProductCardSkeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('neumorphic overflow-hidden', className)} {...props}>
      <Skeleton className="aspect-square w-full" />
      <div className="p-3 space-y-2">
        <Skeleton variant="text" className="w-3/4 h-4" />
        <Skeleton variant="text" className="w-1/2 h-4" />
        <div className="flex items-center justify-between pt-1">
          <Skeleton variant="text" className="w-1/3 h-5" />
          <Skeleton variant="circular" width={32} height={32} />
        </div>
      </div>
    </div>
  );
}

export function ProductDetailSkeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('p-4 space-y-4', className)} {...props}>
      <Skeleton className="aspect-square w-full rounded-[var(--radius-lg)]" />

      <div className="neumorphic p-4 space-y-4">
        <div className="space-y-2">
          <Skeleton variant="text" className="w-3/4 h-6" />
          <Skeleton variant="text" className="w-1/3 h-8" />
        </div>

        <div className="space-y-2">
          <Skeleton variant="text" className="w-full h-4" />
          <Skeleton variant="text" className="w-full h-4" />
          <Skeleton variant="text" className="w-2/3 h-4" />
        </div>

        <div className="flex items-center justify-between pt-2">
          <Skeleton variant="text" className="w-24 h-5" />
          <div className="flex items-center gap-3">
            <Skeleton variant="circular" width={40} height={40} />
            <Skeleton variant="text" className="w-8 h-6" />
            <Skeleton variant="circular" width={40} height={40} />
          </div>
        </div>
      </div>
    </div>
  );
}

interface CategorySkeletonProps {
  count?: number;
}

export function CategorySkeleton({ count = 4 }: CategorySkeletonProps) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="neumorphic p-4">
          <Skeleton variant="rectangular" className="w-12 h-12 mb-2" />
          <Skeleton variant="text" className="w-2/3 h-4" />
        </div>
      ))}
    </div>
  );
}
