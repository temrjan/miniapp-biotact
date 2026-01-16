import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Skeleton, ProductCardSkeleton, ProductDetailSkeleton, CategorySkeleton } from './Skeleton';

describe('Skeleton', () => {
  it('renders with default props', () => {
    const { container } = render(<Skeleton />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toBeInTheDocument();
    expect(skeleton).toHaveClass('animate-pulse');
  });

  it('applies text variant class', () => {
    const { container } = render(<Skeleton variant="text" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveClass('rounded');
  });

  it('applies circular variant class', () => {
    const { container } = render(<Skeleton variant="circular" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveClass('rounded-full');
  });

  it('applies rectangular variant class', () => {
    const { container } = render(<Skeleton variant="rectangular" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveClass('rounded-[var(--radius-md)]');
  });

  it('applies custom width and height as numbers', () => {
    const { container } = render(<Skeleton width={100} height={50} />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveStyle({ width: '100px', height: '50px' });
  });

  it('applies custom width and height as strings', () => {
    const { container } = render(<Skeleton width="50%" height="auto" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveStyle({ width: '50%', height: 'auto' });
  });

  it('applies custom className', () => {
    const { container } = render(<Skeleton className="custom-class" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveClass('custom-class');
  });

  it('applies wave animation class', () => {
    const { container } = render(<Skeleton animation="wave" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveClass('skeleton-wave');
  });

  it('applies no animation when animation is none', () => {
    const { container } = render(<Skeleton animation="none" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).not.toHaveClass('animate-pulse');
    expect(skeleton).not.toHaveClass('skeleton-wave');
  });
});

describe('ProductCardSkeleton', () => {
  it('renders product card skeleton structure', () => {
    const { container } = render(<ProductCardSkeleton />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toBeInTheDocument();
    expect(skeleton).toHaveClass('neumorphic');
  });

  it('applies custom className', () => {
    const { container } = render(<ProductCardSkeleton className="custom-class" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveClass('custom-class');
  });
});

describe('ProductDetailSkeleton', () => {
  it('renders product detail skeleton structure', () => {
    const { container } = render(<ProductDetailSkeleton />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toBeInTheDocument();
    expect(skeleton).toHaveClass('p-4', 'space-y-4');
  });

  it('applies custom className', () => {
    const { container } = render(<ProductDetailSkeleton className="custom-class" />);
    const skeleton = container.firstChild as HTMLElement;
    expect(skeleton).toHaveClass('custom-class');
  });
});

describe('CategorySkeleton', () => {
  it('renders default 4 category skeletons', () => {
    const { container } = render(<CategorySkeleton />);
    const skeletons = container.querySelectorAll('.neumorphic');
    expect(skeletons).toHaveLength(4);
  });

  it('renders custom count of category skeletons', () => {
    const { container } = render(<CategorySkeleton count={6} />);
    const skeletons = container.querySelectorAll('.neumorphic');
    expect(skeletons).toHaveLength(6);
  });

  it('renders grid layout', () => {
    const { container } = render(<CategorySkeleton />);
    const grid = container.firstChild as HTMLElement;
    expect(grid).toHaveClass('grid', 'grid-cols-2', 'gap-3');
  });
});
