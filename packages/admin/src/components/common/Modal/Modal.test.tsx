import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Modal } from './Modal';

describe('Modal', () => {
  it('renders nothing when isOpen is false', () => {
    const { container } = render(
      <Modal isOpen={false} onClose={() => {}}>
        <div>Modal content</div>
      </Modal>
    );

    expect(container.firstChild).toBeNull();
  });

  it('renders modal when isOpen is true', () => {
    render(
      <Modal isOpen={true} onClose={() => {}}>
        <div>Modal content</div>
      </Modal>
    );

    expect(screen.getByText('Modal content')).toBeInTheDocument();
  });

  it('renders title when provided', () => {
    render(
      <Modal isOpen={true} onClose={() => {}} title="Test Title">
        <div>Modal content</div>
      </Modal>
    );

    expect(screen.getByText('Test Title')).toBeInTheDocument();
  });

  it('calls onClose when overlay is clicked', () => {
    const handleClose = vi.fn();
    render(
      <Modal isOpen={true} onClose={handleClose}>
        <div>Modal content</div>
      </Modal>
    );

    const overlay = document.querySelector('.modal-overlay');
    expect(overlay).toBeInTheDocument();
    
    if (overlay) {
      fireEvent.click(overlay);
    }
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('does not close when modal content is clicked', () => {
    const handleClose = vi.fn();
    render(
      <Modal isOpen={true} onClose={handleClose}>
        <div>Modal content</div>
      </Modal>
    );

    const modalContent = document.querySelector('.modal');
    expect(modalContent).toBeInTheDocument();
    
    if (modalContent) {
      fireEvent.click(modalContent);
    }
    expect(handleClose).not.toHaveBeenCalled();
  });
});
