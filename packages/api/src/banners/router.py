"""Banner API router."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import verify_admin_jwt
from src.banners import schemas
from src.banners.models import Banner, Settings
from src.database import get_db

router = APIRouter(prefix="/banners", tags=["banners"])


@router.get("", response_model=schemas.BannerList)
async def get_banners(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db),
) -> schemas.BannerList:
    """Get all banners."""
    query = select(Banner).order_by(Banner.sort_order, Banner.id)

    if is_active is not None:
        query = query.where(Banner.is_active == is_active)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get items
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return schemas.BannerList(items=items, total=total)  # type: ignore[arg-type]


@router.get("/{banner_id}", response_model=schemas.Banner)
async def get_banner(
    banner_id: int,
    db: AsyncSession = Depends(get_db),
) -> Banner:
    """Get banner by ID."""
    result = await db.execute(select(Banner).where(Banner.id == banner_id))
    banner = result.scalar_one_or_none()

    if not banner:
        raise HTTPException(status_code=404, detail="Banner not found")

    return banner


@router.post(
    "", response_model=schemas.Banner, status_code=201, dependencies=[Depends(verify_admin_jwt)]
)
async def create_banner(
    data: schemas.BannerCreate,
    db: AsyncSession = Depends(get_db),
) -> Banner:
    """Create a new banner."""
    banner = Banner(**data.model_dump())
    db.add(banner)
    await db.commit()
    await db.refresh(banner)
    return banner


@router.patch(
    "/{banner_id}", response_model=schemas.Banner, dependencies=[Depends(verify_admin_jwt)]
)
async def update_banner(
    banner_id: int,
    data: schemas.BannerUpdate,
    db: AsyncSession = Depends(get_db),
) -> Banner:
    """Update a banner."""
    result = await db.execute(select(Banner).where(Banner.id == banner_id))
    banner = result.scalar_one_or_none()

    if not banner:
        raise HTTPException(status_code=404, detail="Banner not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(banner, field, value)

    await db.commit()
    await db.refresh(banner)
    return banner


@router.delete("/{banner_id}", status_code=204, dependencies=[Depends(verify_admin_jwt)])
async def delete_banner(
    banner_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a banner."""
    result = await db.execute(select(Banner).where(Banner.id == banner_id))
    banner = result.scalar_one_or_none()

    if not banner:
        raise HTTPException(status_code=404, detail="Banner not found")

    await db.delete(banner)
    await db.commit()


# Settings endpoints
settings_router = APIRouter(prefix="/settings", tags=["settings"])


@settings_router.get("", response_model=list[schemas.Settings])
async def get_all_settings(
    db: AsyncSession = Depends(get_db),
) -> list[Settings]:
    """Get all settings."""
    result = await db.execute(select(Settings))
    return list(result.scalars().all())


@settings_router.get("/{key}", response_model=schemas.Settings)
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
) -> Settings:
    """Get setting by key."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()

    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    return setting


@settings_router.put(
    "/{key}", response_model=schemas.Settings, dependencies=[Depends(verify_admin_jwt)]
)
async def update_setting(
    key: str,
    data: schemas.SettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> Settings:
    """Update or create a setting."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = data.value
    else:
        setting = Settings(key=key, value=data.value)
        db.add(setting)

    await db.commit()
    await db.refresh(setting)
    return setting
