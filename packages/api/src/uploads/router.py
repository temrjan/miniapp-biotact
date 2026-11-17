"""File upload endpoints (admin-only, JWT-protected)."""

import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.auth.dependencies import verify_admin_jwt

router = APIRouter()

# Configuration
UPLOAD_DIR = Path("/var/www/biotact/api/shared/uploads")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4"}
ALLOWED_MP4_BRANDS = {b"isom", b"iso2", b"mp41", b"mp42"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
BASE_URL = "https://app.biotact.uz/uploads"


class UploadResponse(BaseModel):
    """Response for file upload."""

    url: str
    filename: str


def validate_file(file: UploadFile, allowed_extensions: set[str] = ALLOWED_EXTENSIONS) -> None:
    """Validate uploaded file extension."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {', '.join(sorted(allowed_extensions))}",
        )


def validate_mp4_content(content: bytes) -> None:
    """Verify the leading ISO BMFF ftyp box carries an allowed MP4 brand."""
    if len(content) < 16 or content[4:8] != b"ftyp":
        raise HTTPException(status_code=400, detail="File content is not an MP4 video")
    box_size = int.from_bytes(content[:4], "big")
    if box_size < 16 or box_size > len(content) or (box_size - 16) % 4 != 0:
        raise HTTPException(status_code=400, detail="File content is not an MP4 video")
    major_brand = content[8:12]
    if major_brand in ALLOWED_MP4_BRANDS:
        return
    if any(content[offset : offset + 4] in ALLOWED_MP4_BRANDS for offset in range(16, box_size, 4)):
        return
    raise HTTPException(status_code=400, detail="File content is not an MP4 video")


async def save_upload(
    file: UploadFile,
    subfolder: str,
    *,
    allowed_extensions: set[str] = ALLOWED_EXTENSIONS,
    content_validator: Callable[[bytes], None] | None = None,
) -> UploadResponse:
    """Validate size and content, save file to subfolder, return response."""
    validate_file(file, allowed_extensions=allowed_extensions)

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

    if content_validator is not None:
        content_validator(content)

    ext = Path(file.filename or "file.jpg").suffix.lower()
    filename = f"{uuid.uuid4().hex}{ext}"
    upload_path = UPLOAD_DIR / subfolder / filename
    upload_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with upload_path.open("wb") as buffer:
            buffer.write(content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}") from exc

    return UploadResponse(
        url=f"{BASE_URL}/{subfolder}/{filename}",
        filename=filename,
    )


@router.post(
    "/products",
    response_model=UploadResponse,
    dependencies=[Depends(verify_admin_jwt)],
)
async def upload_product_image(
    file: UploadFile = File(..., description="Product image file"),
) -> UploadResponse:
    """Upload product image (admin-only)."""
    return await save_upload(file, "products")


@router.post(
    "/categories",
    response_model=UploadResponse,
    dependencies=[Depends(verify_admin_jwt)],
)
async def upload_category_image(
    file: UploadFile = File(..., description="Category image file"),
) -> UploadResponse:
    """Upload category image (admin-only)."""
    return await save_upload(file, "categories")


@router.post(
    "/banners",
    response_model=UploadResponse,
    dependencies=[Depends(verify_admin_jwt)],
)
async def upload_banner_image(
    file: UploadFile = File(..., description="Banner image file"),
) -> UploadResponse:
    """Upload banner image (admin-only)."""
    return await save_upload(file, "banners")


@router.post(
    "/banners/video",
    response_model=UploadResponse,
    dependencies=[Depends(verify_admin_jwt)],
)
async def upload_banner_video(
    file: UploadFile = File(..., description="Banner video file"),
) -> UploadResponse:
    """Upload banner MP4 clip (admin-only)."""
    return await save_upload(
        file,
        "banners",
        allowed_extensions=ALLOWED_VIDEO_EXTENSIONS,
        content_validator=validate_mp4_content,
    )
