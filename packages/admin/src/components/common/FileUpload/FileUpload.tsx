import { useRef, type ChangeEvent } from 'react';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface FileUploadProps {
  onUpload: (file: File) => void | Promise<void>;
  accept?: string;
  preview?: string | null;
  uploading?: boolean;
  buttonText?: string;
  uploadingText?: string;
  replaceText?: string;
}

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function FileUpload({
  onUpload,
  accept = 'image/jpeg,image/png,image/webp,image/gif',
  preview,
  uploading = false,
  buttonText = 'Выбрать фото',
  uploadingText = 'Загрузка...',
  replaceText = 'Заменить фото',
}: FileUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) {
      return;
    }

    await onUpload(file);

    // Reset input
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      {preview && (
        <img
          src={preview}
          alt="Preview"
          style={{
            width: 80,
            height: 80,
            objectFit: 'cover',
            borderRadius: 8,
            border: '1px solid #ddd',
          }}
        />
      )}
      <div>
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileChange}
          accept={accept}
          style={{ display: 'none' }}
        />
        <button
          type="button"
          className="btn"
          onClick={handleButtonClick}
          disabled={uploading}
        >
          {uploading ? uploadingText : preview ? replaceText : buttonText}
        </button>
      </div>
    </div>
  );
}
