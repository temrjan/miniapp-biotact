"""Class-1 API and upload tests for video banners PR-1 (B-02…B-06, U-01, U-02)."""

import asyncio
import struct
from pathlib import Path

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.banners.models import Banner
from src.uploads import router as uploads

WIDE_IMG = "https://app.biotact.uz/uploads/banners/wide.jpg"
PHONE_IMG = "https://app.biotact.uz/uploads/banners/phone.jpg"
WIDE_CLIP = "https://app.biotact.uz/uploads/banners/wide.mp4"
PHONE_CLIP = "https://app.biotact.uz/uploads/banners/phone.mp4"
BANNERS_URL = "/api/v1/banners"
VIDEO_UPLOAD_URL = "/api/v1/uploads/banners/video"
FIVE_MIB = 5 * 1024 * 1024
_MP4_ERR = "File content is not an MP4 video"
_ALL_MEDIA = {
    "image_url": WIDE_IMG,
    "image_mobile_url": PHONE_IMG,
    "video_url": WIDE_CLIP,
    "video_mobile_url": PHONE_CLIP,
}


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


def _assert_rule_error(res: Response, code: str, fields: list[str] | None) -> None:
    assert res.status_code == 422
    assert res.json() == {"detail": {"code": code, "fields": fields}}


@pytest.mark.asyncio
async def test_b02_create_read_and_patch_media_fields(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Prove B-02: new fields on create/read/patch, defaults, null clearing, and cache headers."""
    plain = await client.post(
        BANNERS_URL, json={"title": "Plain banner", "image_url": WIDE_IMG}, headers=admin_headers
    )
    assert plain.status_code == 201
    plain_data = plain.json()
    assert plain_data["is_active"] is True
    assert plain_data["image_mobile_url"] is None
    assert plain_data["video_url"] is None
    assert plain_data["video_mobile_url"] is None

    full = await client.post(
        BANNERS_URL,
        json={"title": "Video banner", "title_uz": "Video banner UZ", **_ALL_MEDIA},
        headers=admin_headers,
    )
    assert full.status_code == 201
    full_id = full.json()["id"]

    by_id = await client.get(f"{BANNERS_URL}/{full_id}")
    assert by_id.status_code == 200
    assert by_id.headers["cache-control"] == "no-store"
    assert by_id.json()["video_url"] == WIDE_CLIP
    assert by_id.json()["image_mobile_url"] == PHONE_IMG
    assert by_id.json()["video_mobile_url"] == PHONE_CLIP

    listing = await client.get(BANNERS_URL)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "no-store"
    assert {item["id"] for item in listing.json()["items"]} == {plain_data["id"], full_id}

    cleared = await client.patch(
        f"{BANNERS_URL}/{full_id}",
        json={"video_url": None, "video_mobile_url": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200
    cleared_data = cleared.json()
    assert cleared_data["video_url"] is None
    assert cleared_data["video_mobile_url"] is None
    assert cleared_data["image_mobile_url"] == PHONE_IMG

    set_again = await client.patch(
        f"{BANNERS_URL}/{full_id}", json={"video_url": WIDE_CLIP}, headers=admin_headers
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
    create_res = await client.post(BANNERS_URL, json=create_payload, headers=admin_headers)
    assert create_res.status_code == 422
    _assert_validation_field(create_res.json(), field)

    patch_res = await client.patch(
        f"{BANNERS_URL}/{banner.id}", json={field: bad_url}, headers=admin_headers
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
        BANNERS_URL, json={"title": "Max URL", "image_url": url_500}, headers=admin_headers
    )
    assert res.status_code == 201
    assert res.json()["image_url"] == url_500

    legacy = await _seed_banner(test_db, image_url="/uploads/legacy.jpg")
    patch_res = await client.patch(
        f"{BANNERS_URL}/{legacy.id}", json={"sort_order": 5}, headers=admin_headers
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["image_url"] == "/uploads/legacy.jpg"
    assert patch_res.json()["sort_order"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "missing_fields"),
    [
        (
            {
                "title": "RU",
                "title_uz": "UZ",
                "image_url": WIDE_IMG,
                "image_mobile_url": PHONE_IMG,
                "video_mobile_url": PHONE_CLIP,
            },
            ["video_url"],
        ),
        ({"title": "RU", "title_uz": "UZ", "video_url": WIDE_CLIP}, ["image_url"]),
        (
            {"title": "RU", "video_url": WIDE_CLIP, "video_mobile_url": PHONE_CLIP},
            ["image_url", "image_mobile_url"],
        ),
    ],
    ids=["phone-clip-no-wide-clip", "wide-clip-no-image", "order-and-precedence-over-b03"],
)
async def test_b05_create_rejects_invalid_media_combination(
    client: AsyncClient,
    test_db: AsyncSession,
    admin_headers: dict[str, str],
    payload: dict[str, object],
    missing_fields: list[str],
) -> None:
    """Prove B-05: create validates media combinations and refusal order before saving."""
    res = await client.post(BANNERS_URL, json=payload, headers=admin_headers)
    _assert_rule_error(res, "banner_media_combination", missing_fields)
    assert (await test_db.execute(select(Banner))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("patch_payload", "expected_status", "missing_fields"),
    [
        ({"video_url": None}, 422, ["video_url"]),
        ({"image_mobile_url": None}, 422, ["image_mobile_url"]),
        ({"video_url": None, "video_mobile_url": None}, 200, None),
    ],
    ids=["clear-wide-clip", "clear-phone-image", "clear-both-clips"],
)
async def test_b05_patch_evaluates_resulting_media_combination(
    client: AsyncClient,
    test_db: AsyncSession,
    admin_headers: dict[str, str],
    patch_payload: dict[str, object],
    expected_status: int,
    missing_fields: list[str] | None,
) -> None:
    """Prove B-05: PATCH evaluates stored row merged with payload and leaves row intact on 422."""
    stored = await _seed_banner(test_db, **_ALL_MEDIA)
    res = await client.patch(
        f"{BANNERS_URL}/{stored.id}", json=patch_payload, headers=admin_headers
    )
    if expected_status == 422:
        _assert_rule_error(res, "banner_media_combination", missing_fields)
        await test_db.refresh(stored)
        assert (stored.video_url, stored.image_mobile_url) == (WIDE_CLIP, PHONE_IMG)
    else:
        assert res.status_code == 200
        assert (res.json()["video_url"], res.json()["video_mobile_url"]) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "missing_fields"),
    [
        ({"title": "RU", "image_url": WIDE_IMG, "video_url": WIDE_CLIP}, ["title_uz"]),
        (
            {
                "title": "RU",
                "title_uz": "UZ",
                "subtitle": "Sub RU",
                "subtitle_uz": "   ",
                "image_url": WIDE_IMG,
                "video_url": WIDE_CLIP,
            },
            ["subtitle_uz"],
        ),
    ],
    ids=["missing-title-uz", "subtitle-without-subtitle-uz"],
)
async def test_b03_create_rejects_missing_video_text(
    client: AsyncClient,
    test_db: AsyncSession,
    admin_headers: dict[str, str],
    payload: dict[str, object],
    missing_fields: list[str],
) -> None:
    """Prove B-03: creating a video banner requires Uzbek texts and saves nothing on 422."""
    res = await client.post(BANNERS_URL, json=payload, headers=admin_headers)
    _assert_rule_error(res, "video_banner_text_required", missing_fields)
    assert (await test_db.execute(select(Banner))).scalars().all() == []


@pytest.mark.asyncio
async def test_b03_refusal_order_and_image_only_exemption(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Prove B-03: B-04 runs before B-03, and image-only banners need only Russian title."""
    bad_url = await client.post(
        BANNERS_URL,
        json={"title": "RU", "image_url": "http://bad.example/w.jpg", "video_url": WIDE_CLIP},
        headers=admin_headers,
    )
    assert bad_url.status_code == 422
    _assert_validation_field(bad_url.json(), "image_url")
    ok = await client.post(
        BANNERS_URL, json={"title": "Image only", "image_url": WIDE_IMG}, headers=admin_headers
    )
    assert ok.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seed_kwargs", "patch_payload", "expected_status", "missing_fields"),
    [
        ({"video_url": WIDE_CLIP}, {"title_uz": "  "}, 422, ["title_uz"]),
        ({"title": "   ", "title_uz": None}, {"video_url": WIDE_CLIP}, 422, ["title", "title_uz"]),
        (
            {"title": "Legacy", "title_uz": None, **_ALL_MEDIA},
            {"video_url": None, "video_mobile_url": None},
            200,
            None,
        ),
    ],
    ids=["blank-title-uz", "stored-blank-title-attach-video", "clear-videos-no-uz"],
)
async def test_b03_patch_evaluates_resulting_video_text(
    client: AsyncClient,
    test_db: AsyncSession,
    admin_headers: dict[str, str],
    seed_kwargs: dict[str, object],
    patch_payload: dict[str, object],
    expected_status: int,
    missing_fields: list[str] | None,
) -> None:
    """Prove B-03: PATCH checks resulting video-banner text and allows clearing both clips."""
    banner = await _seed_banner(test_db, **seed_kwargs)
    original_title_uz = banner.title_uz
    res = await client.patch(
        f"{BANNERS_URL}/{banner.id}", json=patch_payload, headers=admin_headers
    )
    if expected_status == 422:
        _assert_rule_error(res, "video_banner_text_required", missing_fields)
        await test_db.refresh(banner)
        assert banner.title_uz == original_title_uz
    else:
        assert res.status_code == 200
        assert res.json()["video_url"] is None


@pytest.mark.asyncio
async def test_b06_title_non_blank_and_text_length_bounds(
    client: AsyncClient, test_db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Prove B-06: text length limits and non-blank sent title for every banner."""
    banner = await _seed_banner(test_db)

    long_uz = await client.patch(
        f"{BANNERS_URL}/{banner.id}", json={"title_uz": "x" * 201}, headers=admin_headers
    )
    assert long_uz.status_code == 422
    _assert_validation_field(long_uz.json(), "title_uz")

    long_sub = await client.patch(
        f"{BANNERS_URL}/{banner.id}", json={"subtitle_uz": "x" * 501}, headers=admin_headers
    )
    assert long_sub.status_code == 422
    _assert_validation_field(long_sub.json(), "subtitle_uz")

    for bad_title in (None, "", "  "):
        patch_res = await client.patch(
            f"{BANNERS_URL}/{banner.id}", json={"title": bad_title}, headers=admin_headers
        )
        assert patch_res.status_code == 422
        _assert_validation_field(patch_res.json(), "title")

    await test_db.refresh(banner)
    assert banner.title == "RU Banner"
    assert banner.title_uz == "UZ Banner"

    for bad_create_title in ("", "   "):
        create_res = await client.post(
            BANNERS_URL,
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

    unauth = await client.post(VIDEO_UPLOAD_URL, files={"file": ("clip.mp4", clip, "video/mp4")})
    assert unauth.status_code == 401
    assert await asyncio.to_thread(lambda: list(tmp_path.rglob("*"))) == []

    res = await client.post(
        VIDEO_UPLOAD_URL, files={"file": ("Clip.MP4", clip, "video/mp4")}, headers=admin_headers
    )
    assert res.status_code == 200
    data = res.json()
    assert set(data) == {"url", "filename"}
    assert data["filename"].endswith(".mp4")
    assert len(data["filename"]) == 36
    assert data["url"] == f"{uploads.BASE_URL}/banners/{data['filename']}"
    assert (tmp_path / "banners" / data["filename"]).read_bytes() == clip


@pytest.mark.asyncio
async def test_u02_video_upload_accepts_exact_five_mib_boundary(
    client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove U-02: exact 5 MiB MP4 upload succeeds (independent literal)."""
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    hdr = _make_mp4(b"iso5", (b"mp42",))
    exact_max = hdr + b"\x00" * (FIVE_MIB - len(hdr))
    res = await client.post(
        VIDEO_UPLOAD_URL,
        files={"file": ("five-mib.mp4", exact_max, "video/mp4")},
        headers=admin_headers,
    )
    assert res.status_code == 200
    assert (tmp_path / "banners" / res.json()["filename"]).read_bytes() == exact_max


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "payload", "content_type", "expected_status", "expected_detail"),
    [
        (
            "too-big.mp4",
            _make_mp4(b"isom") + b"\x00" * (FIVE_MIB + 1 - 16),
            "video/mp4",
            413,
            ("File too large",),
        ),
        ("clip.mov", _make_mp4(b"isom"), "video/quicktime", 400, ("File type not allowed", ".mp4")),
        ("fake.mp4", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "video/mp4", 400, _MP4_ERR),
        ("heic.mp4", _make_mp4(b"heic", (b"mif1",)), "video/mp4", 400, _MP4_ERR),
        ("qt.mp4", _make_mp4(b"qt  ", (b"mif1",)), "video/mp4", 400, _MP4_ERR),
        ("spoof.mp4", _make_mp4(b"heic", (), tail=b"isom"), "video/mp4", 400, _MP4_ERR),
    ],
    ids=["over-5mib", "bad-ext", "png-bytes", "heic-brand", "qt-brand", "isom-outside-box"],
)
async def test_u02_video_upload_refuses_invalid_inputs_without_writing(
    client: AsyncClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    payload: bytes,
    content_type: str,
    expected_status: int,
    expected_detail: str | tuple[str, ...],
) -> None:
    """Prove U-02: each size, extension, and ftyp brand refusal answers properly and stores nothing."""
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    res = await client.post(
        VIDEO_UPLOAD_URL, files={"file": (filename, payload, content_type)}, headers=admin_headers
    )
    assert res.status_code == expected_status
    detail = res.json()["detail"]
    if isinstance(expected_detail, str):
        assert detail == expected_detail
    else:
        assert all(text in detail for text in expected_detail)
    assert await asyncio.to_thread(lambda: [p for p in tmp_path.rglob("*") if p.is_file()]) == []
