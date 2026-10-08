"""Shop bridge trust/bootstrap and actual K2/notice delivery entrypoints."""

import asyncio
import os
from pathlib import Path
from typing import Literal, cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.employee_referrals import maintenance, worker
from src.employee_referrals.transport import LaneConfig


@pytest.fixture
def isolated_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Model the root-owned installation parent at the filesystem IO boundary.
    original_lstat = Path.lstat

    def installed_parent_lstat(path: Path) -> os.stat_result:
        info = original_lstat(path)
        if path == tmp_path:
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        return info

    monkeypatch.setattr(Path, "lstat", installed_parent_lstat)
    marker = tmp_path / "state.json"
    monkeypatch.setattr(maintenance, "_loaded", None)
    monkeypatch.setattr(maintenance, "LIBRARY", tmp_path / "absent.py")
    monkeypatch.setattr(maintenance, "Path", lambda _path: marker)
    return marker


async def test_bootstrap_admits_without_library_or_state(isolated_bridge: Path) -> None:
    assert not await asyncio.to_thread(isolated_bridge.exists)
    async with maintenance.admission("shop") as allowed:
        assert allowed is True
    assert maintenance.observation() == ("none", "")


@pytest.mark.parametrize("kind", ["file", "dangling"])
async def test_missing_library_denies_if_state_exists(isolated_bridge: Path, kind: str) -> None:
    if kind == "file":
        await asyncio.to_thread(isolated_bridge.write_text, "{}")
    else:
        await asyncio.to_thread(
            isolated_bridge.symlink_to, isolated_bridge.with_name("absent-state")
        )
    async with maintenance.admission("shop") as allowed:
        assert allowed is False
    assert maintenance.observation() == ("failed", "")


async def test_dangling_library_denies_without_state(
    isolated_bridge: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = isolated_bridge.with_name("library.py")
    library.symlink_to(library.with_name("absent-library"))
    monkeypatch.setattr(maintenance, "LIBRARY", library)
    async with maintenance.admission("shop") as allowed:
        assert allowed is False


@pytest.mark.parametrize("kind", ["writable", "symlink"])
async def test_untrusted_library_is_not_executed(
    isolated_bridge: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    library = isolated_bridge.with_name("library.py")
    library.write_text("raise RuntimeError('untrusted file executed')")
    if kind == "writable":
        library.chmod(0o666)
    else:
        original = library.with_name("original.py")
        library.rename(original)
        library.symlink_to(original)
    monkeypatch.setattr(maintenance, "LIBRARY", library)
    async with maintenance.admission("shop") as allowed:
        assert allowed is False


class StorageBoundary:
    def __call__(self) -> None:
        raise RuntimeError("storage was reached")

    def begin(self) -> None:
        raise RuntimeError("storage was reached")


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_real_delivery_denies_before_claim(
    isolated_bridge: Path, lane: Literal["k2", "notice"]
) -> None:
    await asyncio.to_thread(isolated_bridge.write_text, "{}")
    config = (
        LaneConfig("true", "https://portal.example", "0" * 64)
        if lane == "k2"
        else LaneConfig("true", token="12345:synthetic", chat="-12345")
    )
    async with httpx.AsyncClient() as client:
        assert (
            await worker.deliver_one(
                cast(async_sessionmaker[AsyncSession], StorageBoundary()), client, lane, config
            )
            is False
        )


@pytest.mark.parametrize("lane", ["k2", "notice"])
async def test_real_delivery_reaches_claim_when_bootstrapping(
    isolated_bridge: Path, lane: Literal["k2", "notice"]
) -> None:
    assert not await asyncio.to_thread(isolated_bridge.exists)
    config = (
        LaneConfig("true", "https://portal.example", "0" * 64)
        if lane == "k2"
        else LaneConfig("true", token="12345:synthetic", chat="-12345")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="storage was reached"):
            await worker.deliver_one(
                cast(async_sessionmaker[AsyncSession], StorageBoundary()), client, lane, config
            )
