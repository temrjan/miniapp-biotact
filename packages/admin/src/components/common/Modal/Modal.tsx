import type { ReactNode, MouseEvent } from 'react';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  maxHeight?: string;
}

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function Modal({ isOpen, onClose, title, children, maxHeight = '90vh' }: ModalProps) {
  if (!isOpen) {
    return null;
  }

  const handleOverlayClick = () => {
    onClose();
  };

  const handleModalClick = (e: MouseEvent<HTMLDivElement>) => {
    e.stopPropagation();
  };

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div
        className="modal"
        onClick={handleModalClick}
        style={{ maxHeight, overflowY: 'auto' }}
      >
        {title && <h2>{title}</h2>}
        {children}
      </div>
    </div>
  );
}
