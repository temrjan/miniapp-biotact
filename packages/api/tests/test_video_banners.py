"""Class-1 API and upload tests for video banners PR-1 (B-02…B-06, U-01, U-02)."""

import asyncio
import struct
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.banners.models import Banner
from src.uploads import router as uploads

WIDE_IMG = "https://app.biotact.uz/uploads/banners/wide.jpg"
PHONE_IMG = "https://app.biotact.uz/uploads/banners/phone.jpg"
WIDE_CLIP = "https://app.biotact.uz/uploads/banners/wide.mp4"
PHONE_CLIP = "https://app.biotact.uz/uploads/banners/phone.mp4"


def _make_mp4(major: bytes = b"isom", compat: tuple[bytes, ...] = (), tail: bytes = b"") -> bytes:
    box_size = 16 + 4 * len(compat)
    return (
        struct.pack(">I", box_size)
        + b"ftyp"
        + major
        + b"\x00\x00\x02\x00"
        + b"".join(compat)
        + tail
    )


async def _seed_banner(db: AsyncSession, **kwargs: object) -> Banner:
    defaults: dict[str, object] = {
        "title": "RU Banner",
        "title_uz": "UZ Banner",
        "image_url": WIDE_IMG,
        "is_active": True,
    }
    defaults.update(kwargs)
    banner = Banner(**defaults)
    db.add(banner)
    await db.commit()
    await db.refresh(banner)
    return banner


def _assert_validation_field(response_json: dict[str, object], field: str) -> None:
    detail = response_json["detail"]
    assert isinstance(detail, list)
    assert any(field in err.get("loc", []) for err in detail)


@pytest.mark.asyncio
async def test_b02_create_read_and_patch_media_fields(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Prove B-02: new fields on create/read/patch, defaults, null clearing, and cache headers."""
    plain = await client.post(
        "/api/v1/banners",
        json={"title": "Plain banner", "image_url": WIDE_IMG},
        headers=admin_headers,
    )
    assert plain.status_code == 201
    plain_data = plain.json()
    assert plain_data["is_active"] is True
    assert plain_data["image_mobile_url"] is None
    assert plain_data["video_url"] is None
    assert plain_data["video_mobile_url"] is None

    full = await client.post(
        "/api/v1/banners",
        json={
            "title": "Video banner",
            "title_uz": "Video banner UZ",
            "image_url": WIDE_IMG,
            "image_mobile_url": PHONE_IMG,
            "video_url": WIDE_CLIP,
            "video_mobile_url": PHONE_CLIP,
        },
        headers=admin_headers,
    )
    assert full.status_code == 201
    full_id = full.json()["id"]

    by_id = await client.get(f"/api/v1/banners/{full_id}")
    assert by_id.status_code == 200
    assert by_id.headers["cache-control"] == "no-store"
    assert by_id.json()["video_url"] == WIDE_CLIP
    assert by_id.json()["image_mobile_url"] == PHONE_IMG
    assert by_id.json()["video_mobile_url"] == PHONE_CLIP

    listing = await client.get("/api/v1/banners")
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "no-store"
    assert {item["id"] for item in listing.json()["items"]} == {plain_data["id"], full_id}

    cleared = await client.patch(
        f"/api/v1/banners/{full_id}",
        json={"video_url": None, "video_mobile_url": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200
    cleared_data = cleared.json()
    assert cleared_data["video_url"] is None
    assert cleared_data["video_mobile_url"] is None
    assert cleared_data["image_mobile_url"] == PHONE_IMG

    set_again = await client.patch(
        f"/api/v1/banners/{full_id}",
        json={"video_url": WIDE_CLIP},
        headers=admin_headers,
    )
    assert set_again.status_code == 200
    assert set_again.json()["video_url"] == WIDE_CLIP


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["image_url", "video_url"])
@pytest.mark.parametrize(
    "bad_url",
    [
        "/uploads/banners/clip.mp4",
        "//app.biotact.uz/uploads/banners/clip.mp4",
        "http://app.biotact.uz/uploads/banners/clip.mp4",
        "https://user:pass@app.biotact.uz/clip.mp4",
        "https://user@app.biotact.uz/clip.mp4",
        "",
        "not-a-url",
        "https://",
        "https://app.biotact.uz/" + "a" * (501 - len("https://app.biotact.uz/")),
    ],
)
async def test_b04_rejects_invalid_media_urls_on_create_and_update(
    client: AsyncClient,
    test_db: AsyncSession,
    admin_headers: dict[str, str],
    field: str,
    bad_url: str,
) -> None:
    """Prove B-04: non-null media fields require credential-free absolute https URLs <= 500 chars."""
    banner = await _seed_banner(test_db)
    create_payload: dict[str, object] = {
        "title": "RU",
        "title_uz": "UZ",
        "image_url": WIDE_IMG,
    }
    create_payload[field] = bad_url
    create_res = await client.post("/api/v1/banners", json=create_payload, headers=admin_headers)
    assert create_res.status_code == 422
    _assert_validation_field(create_res.json(), field)

    patch_res = await client.patch(
        f"/api/v1/banners/{banner.id}",
        json={field: bad_url},
        headers=admin_headers,
    )
    assert patch_res.status_code == 422
    _assert_validation_field(patch_res.json(), field)

    refreshed = (await test_db.execute(select(Banner).where(Banner.id == banner.id))).scalar_one()
    assert refreshed.image_url == WIDE_IMG
    assert refreshed.video_url is None


@pytest.mark.asyncio
async def test_b04_accepts_500_char_url_and_skips_unsent_stored_url(
    client: AsyncClient, test_db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Prove B-04: 500-char https URL accepted; unsent legacy relative stored URL not re-validated."""
    url_500 = "https://app.biotact.uz/" + "a" * (500 - len("https://app.biotact.uz/"))
    res = await client.post(
        "/api/v1/banners",
        json={"title": "Max URL", "image_url": url_500},
        headers=admin_headers,
    )
    assert res.status_code == 201
    assert res.json()["image_url"] == url_500

    legacy = await _seed_banner(test_db, image_url="/uploads/legacy.jpg")
    patch_res = await client.patch(
        f"/api/v1/banners/{legacy.id}",
        json={"sort_order": 5},
        headers=admin_headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["image_url"] == "/uploads/legacy.jpg"
    assert patch_res.json()["sort_order"] == 5


@pytest.mark.asyncio
async def test_b05_media_combination_rules_and_field_order(
    client: AsyncClient, test_db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Prove B-05: resulting-banner media combinations, error code/field order, and atomicity."""
    no_wide_clip = await client.post(
        "/api/v1/banners",
        json={
            "title": "RU",
            "title_uz": "UZ",
            "image_url": WIDE_IMG,
            "image_mobile_url": PHONE_IMG,
            "video_mobile_url": PHONE_CLIP,
        },
        headers=admin_headers,
    )
    assert no_wide_clip.status_code == 422
    assert no_wide_clip.json() == {
        "detail": {"code": "banner_media_combination", "fields": ["video_url"]}
    }

    no_wide_img = await client.post(
        "/api/v1/banners",
        json={"title": "RU", "title_uz": "UZ", "video_url": WIDE_CLIP},
        headers=admin_headers,
    )
    assert no_wide_img.status_code == 422
    assert no_wide_img.json() == {
        "detail": {"code": "banner_media_combination", "fields": ["image_url"]}
    }

    multi_missing = await client.post(
        "/api/v1/banners",
        json={"title": "RU", "video_url": WIDE_CLIP, "video_mobile_url": PHONE_CLIP},
        headers=admin_headers,
    )
    assert multi_missing.status_code == 422
    assert multi_missing.json() == {
        "detail": {
            "code": "banner_media_combination",
            "fields": ["image_url", "image_mobile_url"],
        }
    }

    stored = await _seed_banner(
        test_db,
        image_url=WIDE_IMG,
        image_mobile_url=PHONE_IMG,
        video_url=WIDE_CLIP,
        video_mobile_url=PHONE_CLIP,
    )

    clear_wide_only = await client.patch(
        f"/api/v1/banners/{stored.id}",
        json={"video_url": None},
        headers=admin_headers,
    )
    assert clear_wide_only.status_code == 422
    assert clear_wide_only.json() == {
        "detail": {"code": "banner_media_combination", "fields": ["video_url"]}
    }

    clear_phone_img = await client.patch(
        f"/api/v1/banners/{stored.id}",
        json={"image_mobile_url": None},
        headers=admin_headers,
    )
    assert clear_phone_img.status_code == 422
    assert clear_phone_img.json() == {
        "detail": {"code": "banner_media_combination", "fields": ["image_mobile_url"]}
    }

    await test_db.refresh(stored)
    assert stored.video_url == WIDE_CLIP
    assert stored.image_mobile_url == PHONE_IMG

    clear_both = await client.patch(
        f"/api/v1/banners/{stored.id}",
        json={"video_url": None, "video_mobile_url": None},
        headers=admin_headers,
    )
    assert clear_both.status_code == 200
    assert clear_both.json()["video_url"] is None
    assert clear_both.json()["video_mobile_url"] is None


@pytest.mark.asyncio
async def test_b03_video_banner_text_rule_and_refusal_order(
    client: AsyncClient, test_db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Prove B-03: text rules on resulting video banner, refusal order, and image exemption."""
    bad_url_first = await client.post(
        "/api/v1/banners",
        json={"title": "RU", "image_url": "http://bad.example/w.jpg", "video_url": WIDE_CLIP},
        headers=admin_headers,
    )
    assert bad_url_first.status_code == 422
    _assert_validation_field(bad_url_first.json(), "image_url")

    no_uz = await client.post(
        "/api/v1/banners",
        json={"title": "RU", "image_url": WIDE_IMG, "video_url": WIDE_CLIP},
        headers=admin_headers,
    )
    assert no_uz.status_code == 422
    assert no_uz.json() == {
        "detail": {"code": "video_banner_text_required", "fields": ["title_uz"]}
    }

    no_sub_uz = await client.post(
        "/api/v1/banners",
        json={
            "title": "RU",
            "title_uz": "UZ",
            "subtitle": "Sub RU",
            "subtitle_uz": "   ",
            "image_url": WIDE_IMG,
            "video_url": WIDE_CLIP,
        },
        headers=admin_headers,
    )
    assert no_sub_uz.status_code == 422
    assert no_sub_uz.json() == {
        "detail": {"code": "video_banner_text_required", "fields": ["subtitle_uz"]}
    }

    image_only = await client.post(
        "/api/v1/banners",
        json={"title": "Image only", "image_url": WIDE_IMG},
        headers=admin_headers,
    )
    assert image_only.status_code == 201

    video_banner = await _seed_banner(test_db, video_url=WIDE_CLIP)
    blank_uz_patch = await client.patch(
        f"/api/v1/banners/{video_banner.id}",
        json={"title_uz": "  "},
        headers=admin_headers,
    )
    assert blank_uz_patch.status_code == 422
    assert blank_uz_patch.json() == {
        "detail": {"code": "video_banner_text_required", "fields": ["title_uz"]}
    }
    await test_db.refresh(video_banner)
    assert video_banner.title_uz == "UZ Banner"

    blank_title_stored = await _seed_banner(test_db, title="   ", title_uz=None, image_url=WIDE_IMG)
    attach_video = await client.patch(
        f"/api/v1/banners/{blank_title_stored.id}",
        json={"video_url": WIDE_CLIP},
        headers=admin_headers,
    )
    assert attach_video.status_code == 422
    assert attach_video.json() == {
        "detail": {"code": "video_banner_text_required", "fields": ["title", "title_uz"]}
    }

    video_no_uz = await _seed_banner(
        test_db,
        title="Legacy",
        title_uz=None,
        image_url=WIDE_IMG,
        video_url=WIDE_CLIP,
        video_mobile_url=PHONE_CLIP,
        image_mobile_url=PHONE_IMG,
    )
    clear_videos = await client.patch(
        f"/api/v1/banners/{video_no_uz.id}",
        json={"video_url": None, "video_mobile_url": None},
        headers=admin_headers,
    )
    assert clear_videos.status_code == 200
    assert clear_videos.json()["video_url"] is None


@pytest.mark.asyncio
async def test_b06_title_non_blank_and_text_length_bounds(
    client: AsyncClient, test_db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Prove B-06: text length limits and non-blank sent title for every banner."""
    banner = await _seed_banner(test_db)

    long_uz = await client.patch(
        f"/api/v1/banners/{banner.id}",
        json={"title_uz": "x" * 201},
        headers=admin_headers,
    )
    assert long_uz.status_code == 422
    _assert_validation_field(long_uz.json(), "title_uz")

    long_sub = await client.patch(
        f"/api/v1/banners/{banner.id}",
        json={"subtitle_uz": "x" * 501},
        headers=admin_headers,
    )
    assert long_sub.status_code == 422
    _assert_validation_field(long_sub.json(), "subtitle_uz")

    for bad_title in (None, "", "  "):
        patch_res = await client.patch(
            f"/api/v1/banners/{banner.id}",
            json={"title": bad_title},
            headers=admin_headers,
        )
        assert patch_res.status_code == 422
        _assert_validation_field(patch_res.json(), "title")

    await test_db.refresh(banner)
    assert banner.title == "RU Banner"
    assert banner.title_uz == "UZ Banner"

    for bad_create_title in ("", "   "):
        create_res = await client.post(
            "/api/v1/banners",
            json={"title": bad_create_title, "image_url": WIDE_IMG},
            headers=admin_headers,
        )
        assert create_res.status_code == 422
        _assert_validation_field(create_res.json(), "title")

    count = len((await test_db.execute(select(Banner))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_u01_video_upload_auth_storage_and_response(
    client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove U-01: admin JWT, uuid .mp4 filename in banners dir, exact bytes, UploadResponse."""
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    clip = _make_mp4(b"isom", (b"iso2",), b"\x00" * 64)

    unauth = await client.post(
        "/api/v1/uploads/banners/video",
        files={"file": ("clip.mp4", clip, "video/mp4")},
    )
    assert unauth.status_code == 401
    assert await asyncio.to_thread(lambda: list(tmp_path.rglob("*"))) == []

    res = await client.post(
        "/api/v1/uploads/banners/video",
        files={"file": ("Clip.MP4", clip, "video/mp4")},
        headers=admin_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert set(data) == {"url", "filename"}
    assert data["filename"].endswith(".mp4")
    assert len(data["filename"]) == 36
    assert data["url"] == f"{uploads.BASE_URL}/banners/{data['filename']}"
    assert (tmp_path / "banners" / data["filename"]).read_bytes() == clip


@pytest.mark.asyncio
async def test_u02_video_upload_extension_size_and_ftyp_validation(
    client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove U-02: 5 MiB boundary, .mp4 extension, ftyp brand check, and clean refusals."""
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)

    max_header = _make_mp4(b"iso5", (b"mp42",))
    exact_max = max_header + b"\x00" * (uploads.MAX_FILE_SIZE - len(max_header))
    ok_max = await client.post(
        "/api/v1/uploads/banners/video",
        files={"file": ("five-mib.mp4", exact_max, "video/mp4")},
        headers=admin_headers,
    )
    assert ok_max.status_code == 200
    (tmp_path / "banners" / ok_max.json()["filename"]).unlink()

    too_big = await client.post(
        "/api/v1/uploads/banners/video",
        files={"file": ("too-big.mp4", exact_max + b"\x00", "video/mp4")},
        headers=admin_headers,
    )
    assert too_big.status_code == 413
    assert "File too large" in too_big.json()["detail"]

    bad_ext = await client.post(
        "/api/v1/uploads/banners/video",
        files={"file": ("clip.mov", _make_mp4(b"isom"), "video/quicktime")},
        headers=admin_headers,
    )
    assert bad_ext.status_code == 400
    assert "File type not allowed" in bad_ext.json()["detail"]
    assert ".mp4" in bad_ext.json()["detail"]

    png_bytes = await client.post(
        "/api/v1/uploads/banners/video",
        files={"file": ("fake.mp4", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "video/mp4")},
        headers=admin_headers,
    )
    assert png_bytes.status_code == 400
    assert png_bytes.json() == {"detail": "File content is not an MP4 video"}

    for bad_brand in (b"heic", b"qt  "):
        brand_res = await client.post(
            "/api/v1/uploads/banners/video",
            files={"file": ("wrong-brand.mp4", _make_mp4(bad_brand, (b"mif1",)), "video/mp4")},
            headers=admin_headers,
        )
        assert brand_res.status_code == 400
        assert brand_res.json() == {"detail": "File content is not an MP4 video"}

    spoofed_tail = await client.post(
        "/api/v1/uploads/banners/video",
        files={"file": ("spoof.mp4", _make_mp4(b"heic", (), tail=b"isom"), "video/mp4")},
        headers=admin_headers,
    )
    assert spoofed_tail.status_code == 400
    assert spoofed_tail.json() == {"detail": "File content is not an MP4 video"}

    remaining = await asyncio.to_thread(lambda: [p for p in tmp_path.rglob("*") if p.is_file()])
    assert remaining == []
