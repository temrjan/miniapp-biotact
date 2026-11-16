"""Bounded parser regressions and compatibility of all mounted image uploads."""

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient
from python_multipart import MultipartParser, QuerystringParser
from python_multipart.exceptions import MultipartParseError
from starlette.formparsers import MultiPartException
from starlette.requests import Request
from starlette.types import Message

from src.uploads import router as uploads

PATHS = ["/api/v1/uploads/products", "/api/v1/uploads/categories", "/api/v1/uploads/banners"]
# U-03: parser-bound tests that do not rely on image extensions also cover the video upload route.
ALL_UPLOAD_PATHS = [*PATHS, "/api/v1/uploads/banners/video"]
IMAGE = b"\x89PNG\r\n\x1a\nsynthetic-upload-test"
BAD_HEADERS = [b"X-Probe: " + b"x" * 5000 + b"\r\n", b"X-Probe: x\r\n" * 9]


@pytest.mark.parametrize("path", ALL_UPLOAD_PATHS)
@pytest.mark.parametrize("authorized", [False, True])
async def test_upload_bounds_urlencoded_field_count(
    client: AsyncClient, admin_headers: dict[str, str], path: str, authorized: bool
) -> None:
    headers = dict(admin_headers) if authorized else {}
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    response = await client.post(path, content=b"f=x&" * 1100, headers=headers)
    assert response.status_code == 400
    assert "Too many fields" in response.json()["detail"]


@pytest.mark.parametrize("path", ALL_UPLOAD_PATHS)
@pytest.mark.parametrize("authorized", [False, True])
async def test_upload_bounds_urlencoded_field_size(
    client: AsyncClient, admin_headers: dict[str, str], path: str, authorized: bool
) -> None:
    headers = dict(admin_headers) if authorized else {}
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    response = await client.post(path, content=b"f=" + b"x" * (1024 * 1024 + 1), headers=headers)
    assert response.status_code == 400
    assert "maximum size" in response.json()["detail"]


async def test_urlencoded_explicit_part_limit_is_enforced() -> None:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        assert not sent
        sent = True
        return {"type": "http.request", "body": b"f=" + b"x" * 33, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        },
        receive,
    )
    with pytest.raises(MultiPartException, match="maximum size"):
        await request.form(max_fields=10, max_part_size=32)


def test_semicolons_are_not_querystring_separators() -> None:
    fields: list[bool] = []
    parser = QuerystringParser({"on_field_end": lambda: fields.append(True)})
    parser.write(b"f=x;" * 1100)
    parser.finalize()
    assert len(fields) == 1


@pytest.mark.parametrize("header", BAD_HEADERS, ids=["oversized-header", "excess-headers"])
def test_multipart_parser_bounds_part_headers(header: bytes) -> None:
    parser = MultipartParser(b"upload-probe")
    with pytest.raises(MultipartParseError, match="Maximum header"):
        parser.write(b"--upload-probe\r\n" + header + b"\r\nx\r\n--upload-probe--\r\n")


@pytest.mark.parametrize("path", ALL_UPLOAD_PATHS)
@pytest.mark.parametrize("header", BAD_HEADERS, ids=["oversized-header", "excess-headers"])
async def test_upload_rejects_unbounded_part_headers(
    client: AsyncClient, path: str, header: bytes
) -> None:
    response = await client.post(
        path,
        content=b"--upload-probe\r\n" + header + b"\r\nx\r\n--upload-probe--\r\n",
        headers={"Content-Type": "multipart/form-data; boundary=upload-probe"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "There was an error parsing the body"}


@pytest.mark.parametrize("path", PATHS)
async def test_normal_upload_requires_admin_and_preserves_response(
    client: AsyncClient,
    admin_headers: dict[str, str],
    path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    refused = await client.post(path, files={"file": ("image.png", IMAGE, "image/png")})
    assert refused.status_code == 401
    assert await asyncio.to_thread(lambda: list(tmp_path.rglob("*"))) == []
    response = await client.post(
        path, files={"file": ("image.png", IMAGE, "image/png")}, headers=admin_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"url", "filename"}
    folder = path.rsplit("/", 1)[-1]
    assert (tmp_path / folder / data["filename"]).read_bytes() == IMAGE
    assert data["url"] == f"{uploads.BASE_URL}/{folder}/{data['filename']}"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("extra_bytes", "status"), [(0, 200), (1, 413)])
async def test_upload_preserves_five_mib_file_limit(
    client: AsyncClient,
    admin_headers: dict[str, str],
    path: str,
    extra_bytes: int,
    status: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    body = b"x" * (5 * 1024 * 1024 + extra_bytes)
    response = await client.post(
        path, files={"file": ("image.png", body, "image/png")}, headers=admin_headers
    )
    assert response.status_code == status
    if status == 200:
        folder = path.rsplit("/", 1)[-1]
        assert (tmp_path / folder / response.json()["filename"]).read_bytes() == body
    else:
        assert await asyncio.to_thread(lambda: list(tmp_path.rglob("*"))) == []


@pytest.mark.parametrize("path", PATHS)
async def test_upload_still_rejects_unsupported_extension(
    client: AsyncClient,
    admin_headers: dict[str, str],
    path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    response = await client.post(
        path, files={"file": ("image.txt", IMAGE, "text/plain")}, headers=admin_headers
    )
    assert response.status_code == 400
    assert "File type not allowed" in response.json()["detail"]
    assert await asyncio.to_thread(lambda: list(tmp_path.rglob("*"))) == []
