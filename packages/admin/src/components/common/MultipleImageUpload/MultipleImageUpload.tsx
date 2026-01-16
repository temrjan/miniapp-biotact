import { useRef, useState, type ChangeEvent } from "react";

interface MultipleImageUploadProps {
  images: string[];
  onChange: (images: string[]) => void;
  maxImages?: number;
  uploading?: boolean;
  onUploadStart?: () => void;
  onUploadEnd?: () => void;
}

export function MultipleImageUpload({
  images,
  onChange,
  maxImages = 3,
  uploading = false,
  onUploadStart,
  onUploadEnd,
}: MultipleImageUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || images.length >= maxImages) return;

    setIsUploading(true);
    onUploadStart?.();

    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/v1/uploads/products", { method: "POST", body: fd });
      if (!res.ok) throw new Error("Upload failed");
      const data = await res.json();
      onChange([...images, data.url]);
    } catch (err) {
      console.error("Upload error:", err);
    } finally {
      setIsUploading(false);
      onUploadEnd?.();
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleRemove = (index: number) => {
    onChange(images.filter((_, i) => i !== index));
  };

  const handleDragStart = (index: number) => {
    setDragIndex(index);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (dragIndex === null || dragIndex === index) return;

    const newImages = [...images];
    const [draggedItem] = newImages.splice(dragIndex, 1);
    newImages.splice(index, 0, draggedItem);
    onChange(newImages);
    setDragIndex(index);
  };

  const handleDragEnd = () => {
    setDragIndex(null);
  };

  const canAddMore = images.length < maxImages;
  const loading = uploading || isUploading;

  return (
    <div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {images.map((url, index) => (
          <div
            key={url + index}
            draggable
            onDragStart={() => handleDragStart(index)}
            onDragOver={(e) => handleDragOver(e, index)}
            onDragEnd={handleDragEnd}
            style={{
              position: "relative",
              width: 100,
              height: 100,
              borderRadius: 8,
              overflow: "hidden",
              border: dragIndex === index ? "2px dashed #2196f3" : "1px solid #ddd",
              cursor: "grab",
              opacity: dragIndex === index ? 0.5 : 1,
            }}
          >
            <img
              src={url}
              alt={`Image ${index + 1}`}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
            <button
              type="button"
              onClick={() => handleRemove(index)}
              style={{
                position: "absolute",
                top: 4,
                right: 4,
                width: 24,
                height: 24,
                borderRadius: "50%",
                border: "none",
                background: "rgba(0,0,0,0.6)",
                color: "#fff",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 14,
              }}
            >
              ✕
            </button>
            <div
              style={{
                position: "absolute",
                bottom: 4,
                left: 4,
                background: "rgba(0,0,0,0.6)",
                color: "#fff",
                borderRadius: 4,
                padding: "2px 6px",
                fontSize: 11,
              }}
            >
              {index + 1}
            </div>
          </div>
        ))}

        {canAddMore && (
          <div
            onClick={() => !loading && fileInputRef.current?.click()}
            style={{
              width: 100,
              height: 100,
              borderRadius: 8,
              border: "2px dashed #ccc",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              cursor: loading ? "not-allowed" : "pointer",
              background: "#f9f9f9",
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? (
              <span style={{ fontSize: 12, color: "#666" }}>Загрузка...</span>
            ) : (
              <>
                <span style={{ fontSize: 24, color: "#999" }}>+</span>
                <span style={{ fontSize: 11, color: "#999", marginTop: 4 }}>
                  {images.length}/{maxImages}
                </span>
              </>
            )}
          </div>
        )}
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        onChange={handleFileChange}
        style={{ display: "none" }}
      />

      {images.length > 1 && (
        <p style={{ fontSize: 12, color: "#666", marginTop: 8 }}>
          Перетащите для изменения порядка. Первое фото — главное.
        </p>
      )}
    </div>
  );
}
