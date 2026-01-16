import { useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type { PanInfo } from 'framer-motion';

interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  children: React.ReactNode;
  maxHeight?: string;
}

export function BottomSheet({
  isOpen,
  onClose,
  children,
  maxHeight = '90vh',
}: BottomSheetProps) {
  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // Prevent body scroll when open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  // Drag to close handler
  const handleDragEnd = useCallback(
    (_event: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
      // Close if dragged down more than 100px or with high velocity
      if (info.offset.y > 100 || info.velocity.y > 500) {
        onClose();
      }
    },
    [onClose]
  );

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/50 z-[100]"
          />

          {/* Sheet Container */}
          <div className="fixed inset-0 z-[101] pointer-events-none flex items-end md:items-center md:justify-center md:p-4">
            <motion.div
              initial={{ y: '100%' }}
              animate={{ y: 0 }}
              exit={{ y: '100%' }}
              transition={{ type: 'spring', damping: 25, stiffness: 300 }}
              drag="y"
              dragConstraints={{ top: 0, bottom: 0 }}
              dragElastic={{ top: 0, bottom: 0.5 }}
              onDragEnd={handleDragEnd}
              style={{ maxHeight }}
              className="pointer-events-auto w-full md:max-w-xl bg-[var(--bg-primary)] rounded-t-2xl md:rounded-2xl overflow-hidden flex flex-col shadow-xl"
            >
              {/* Drag Handle */}
              <div className="flex justify-center pt-3 pb-2 cursor-grab active:cursor-grabbing shrink-0">
                <div className="w-10 h-1 bg-[var(--text-muted)] rounded-full opacity-50" />
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto overscroll-contain">
                {children}
              </div>
            </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>
  );
}
