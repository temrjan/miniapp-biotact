import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { ImageUploader } from './ImageUploader';

// Mock react-dropzone to control file input behavior
vi.mock('react-dropzone', () => ({
  useDropzone: (options: { disabled?: boolean }) => ({
    getRootProps: () => ({ role: 'presentation' }),
    getInputProps: () => ({ type: 'file', 'data-testid': 'file-input', disabled: options.disabled }),
    isDragActive: false,
  }),
}));

// Mock apiClient
vi.mock('@/shared/api', () => ({
  apiClient: {
    post: vi.fn(),
  },
}));

describe('ImageUploader', () => {
  it('renders dropzone when no images', () => {
    render(<ImageUploader />);
    expect(screen.getByText('Перетащите изображения сюда')).toBeInTheDocument();
  });

  it('shows file limit info', () => {
    render(<ImageUploader maxFiles={3} maxSize={2} />);
    expect(screen.getByText('До 3 файлов, макс. 2MB каждый')).toBeInTheDocument();
  });

  it('renders image previews when value provided', () => {
    const urls = ['https://example.com/img1.jpg', 'https://example.com/img2.jpg'];
    render(<ImageUploader value={urls} />);

    const images = screen.getAllByRole('img');
    expect(images).toHaveLength(2);
    expect(images[0]).toHaveAttribute('src', urls[0]);
    expect(images[1]).toHaveAttribute('src', urls[1]);
  });

  it('marks first image as primary', () => {
    render(<ImageUploader value={['https://example.com/img1.jpg']} />);
    expect(screen.getByText('Основное')).toBeInTheDocument();
  });

  it('shows max limit message when full', () => {
    const urls = Array.from({ length: 5 }, (_, i) => `https://example.com/img${i}.jpg`);
    render(<ImageUploader value={urls} maxFiles={5} />);
    expect(screen.getByText('Достигнуто максимальное количество изображений (5)')).toBeInTheDocument();
  });

  it('hides dropzone when max files reached', () => {
    const urls = Array.from({ length: 5 }, (_, i) => `https://example.com/img${i}.jpg`);
    render(<ImageUploader value={urls} maxFiles={5} />);
    expect(screen.queryByText('Перетащите изображения сюда')).not.toBeInTheDocument();
  });

  it('hides dropzone when disabled', () => {
    render(<ImageUploader disabled />);
    expect(screen.queryByText('Перетащите изображения сюда')).not.toBeInTheDocument();
  });

  it('calls onChange when image removed', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const urls = ['https://example.com/img1.jpg', 'https://example.com/img2.jpg'];

    render(<ImageUploader value={urls} onChange={onChange} />);

    const removeButtons = screen.getAllByRole('button');
    const firstButton = removeButtons[0];
    if (!firstButton) throw new Error('Remove button not found');
    await user.click(firstButton);

    expect(onChange).toHaveBeenCalledWith(['https://example.com/img2.jpg']);
  });

  it('does not show remove buttons when disabled', () => {
    render(
      <ImageUploader
        value={['https://example.com/img1.jpg']}
        disabled
      />
    );

    const buttons = screen.queryAllByRole('button');
    expect(buttons).toHaveLength(0);
  });
});
