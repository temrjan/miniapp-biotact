"""File upload endpoints (admin-only, JWT-protected)."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from src.config import settings

router = APIRouter()
security = HTTPBearer()

# Configuration
UPLOAD_DIR = Path("/var/www/biotact/api/shared/uploads")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
BASE_URL = "https://app.biotact.uz/uploads"


class UploadResponse(BaseModel):
    """Response for file upload."""

    url: str
    filename: str


async def verify_admin_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    """Verify JWT token and return user_id. Lightweight — no DB lookup."""
    token = credentials.credentials
    for secret, allowed_type in [
        (settings.secret_key.get_secret_value(), "admin"),
        (settings.jwt_secret.get_secret_value(), "access"),
    ]:
        try:
            payload = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
            if payload.get("type") == allowed_type:
                return int(payload.get("sub"))
        except (JWTError, ValueError, TypeError):
            continue
    raise HTTPException(status_code=401, detail="Invalid token")


def validate_file(file: UploadFile) -> None:
    """Validate uploaded file."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )


async def save_upload(file: UploadFile, subfolder: str) -> UploadResponse:
    """Validate size, save file to subfolder, return response."""
    validate_file(file)

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

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
