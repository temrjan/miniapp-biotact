import type { CSSProperties } from 'react';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  disabled?: boolean;
}

// ═══════════════════════════════════════════════════════════════════
// Helper Functions
// ═══════════════════════════════════════════════════════════════════

function getPageNumbers(currentPage: number, totalPages: number): (number | string)[] {
  const pages: (number | string)[] = [];
  const delta = 2;

  for (let i = 1; i <= totalPages; i++) {
    if (
      i === 1 ||
      i === totalPages ||
      (i >= currentPage - delta && i <= currentPage + delta)
    ) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== '...') {
      pages.push('...');
    }
  }

  return pages;
}

// ═══════════════════════════════════════════════════════════════════
// Styles
// ═══════════════════════════════════════════════════════════════════

const containerStyle: CSSProperties = {
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
  gap: 8,
  padding: '20px 0',
  borderTop: '1px solid #eee',
  marginTop: 16,
};

const buttonStyle: CSSProperties = {
  padding: '6px 12px',
  border: '1px solid #ddd',
  borderRadius: 4,
  background: '#fff',
  cursor: 'pointer',
};

const disabledButtonStyle: CSSProperties = {
  ...buttonStyle,
  background: '#f5f5f5',
  cursor: 'not-allowed',
};

const activeButtonStyle: CSSProperties = {
  ...buttonStyle,
  background: '#007bff',
  color: '#fff',
  fontWeight: 'bold',
};

const infoStyle: CSSProperties = {
  marginLeft: 16,
  color: '#666',
  fontSize: 14,
};

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function Pagination({
  currentPage,
  totalPages,
  onPageChange,
  disabled = false,
}: PaginationProps) {
  if (totalPages <= 1) {
    return null;
  }

  const pageNumbers = getPageNumbers(currentPage, totalPages);

  return (
    <div style={containerStyle}>
      <button
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage === 1 || disabled}
        style={currentPage === 1 || disabled ? disabledButtonStyle : buttonStyle}
      >
        ←
      </button>

      {pageNumbers.map((page, idx) =>
        typeof page === 'number' ? (
          <button
            key={idx}
            onClick={() => onPageChange(page)}
            disabled={disabled}
            style={page === currentPage ? activeButtonStyle : buttonStyle}
          >
            {page}
          </button>
        ) : (
          <span key={idx} style={{ padding: '6px 8px', color: '#999' }}>
            {page}
          </span>
        )
      )}

      <button
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage === totalPages || disabled}
        style={currentPage === totalPages || disabled ? disabledButtonStyle : buttonStyle}
      >
        →
      </button>

      <span style={infoStyle}>
        Стр. {currentPage} из {totalPages}
      </span>
    </div>
  );
}
