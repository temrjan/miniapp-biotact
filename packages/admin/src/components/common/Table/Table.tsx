import type { ReactNode } from 'react';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface Column<T> {
  header: string;
  accessor?: keyof T;
  render?: (item: T) => ReactNode;
  width?: string | number;
}

interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  keyExtractor: (item: T) => string | number;
  onEdit?: (item: T) => void;
  onDelete?: (item: T) => void;
  emptyMessage?: string;
  editButtonText?: string;
  deleteButtonText?: string;
}

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function Table<T>({
  columns,
  data,
  keyExtractor,
  onEdit,
  onDelete,
  emptyMessage = 'Нет данных',
  editButtonText = 'Edit',
  deleteButtonText = 'Del',
}: TableProps<T>) {
  const hasActions = onEdit || onDelete;

  return (
    <>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #dee2e6' }}>
            {columns.map((column, idx) => (
              <th
                key={idx}
                style={{
                  padding: '12px 8px',
                  textAlign: 'left',
                  fontWeight: 600,
                  width: column.width,
                }}
              >
                {column.header}
              </th>
            ))}
            {hasActions && (
              <th
                style={{
                  padding: '12px 8px',
                  textAlign: 'left',
                  fontWeight: 600,
                }}
              >
                Действия
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {data.map((item) => {
            const key = keyExtractor(item);

            return (
              <tr key={key} style={{ borderBottom: '1px solid #dee2e6' }}>
                {columns.map((column, idx) => (
                  <td
                    key={idx}
                    style={{
                      padding: '12px 8px',
                      verticalAlign: 'top',
                    }}
                  >
                    {column.render
                      ? column.render(item)
                      : column.accessor
                      ? String(item[column.accessor] ?? '')
                      : ''}
                  </td>
                ))}
                {hasActions && (
                  <td style={{ padding: '12px 8px' }}>
                    {onEdit && (
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={() => onEdit(item)}
                        style={{ marginRight: 5 }}
                      >
                        {editButtonText}
                      </button>
                    )}
                    {onDelete && (
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => onDelete(item)}
                      >
                        {deleteButtonText}
                      </button>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      {data.length === 0 && (
        <p style={{ textAlign: 'center', padding: 40, color: '#666' }}>
          {emptyMessage}
        </p>
      )}
    </>
  );
}
