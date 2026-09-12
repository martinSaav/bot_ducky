"""Tests para src/storage.py

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.storage import JsonStore


class TestJsonStore:

    # -----------------------------------------------------------------------
    # get / all
    # -----------------------------------------------------------------------

    def test_JsonStore_get_WhenKeyMissing_ReturnsDefault(self, tmp_path: Path):
        store = JsonStore(tmp_path / "s.json")
        assert store.get("missing") is None
        assert store.get("missing", "default") == "default"

    def test_JsonStore_get_WhenFileDoesNotExist_ReturnsEmpty(self, tmp_path: Path):
        store = JsonStore(tmp_path / "new.json")
        assert store.all() == {}

    def test_JsonStore_all_ReturnsACopy(self, tmp_path: Path):
        store = JsonStore(tmp_path / "s.json")
        snapshot = store.all()
        snapshot["injected"] = True
        # La mutación del snapshot no debe afectar el store interno
        assert "injected" not in store.all()

    # -----------------------------------------------------------------------
    # set (async)
    # -----------------------------------------------------------------------

    async def test_JsonStore_set_WithNewKey_PersistsToFile(self, tmp_path: Path):
        path = tmp_path / "s.json"
        store = JsonStore(path)
        await store.set("name", "BotDucky")

        # Verificar que el archivo realmente tiene el dato
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["name"] == "BotDucky"

    async def test_JsonStore_set_OverExistingKey_OverwritesValue(self, tmp_path: Path):
        store = JsonStore(tmp_path / "s.json")
        await store.set("count", 1)
        await store.set("count", 99)
        assert store.get("count") == 99

    async def test_JsonStore_set_WithComplexValue_RoundTrips(self, tmp_path: Path):
        path = tmp_path / "s.json"
        store = JsonStore(path)
        payload = {"nested": {"a": [1, 2, 3]}, "unicode": "ñoño"}
        await store.set("data", payload)

        reloaded = JsonStore(path)
        assert reloaded.get("data") == payload

    # -----------------------------------------------------------------------
    # update (async)
    # -----------------------------------------------------------------------

    async def test_JsonStore_update_WithNewPatch_CreatesDictKey(self, tmp_path: Path):
        store = JsonStore(tmp_path / "s.json")
        result = await store.update("cache", {"lol": {"id": "1", "name": "League of Legends"}})
        assert result["lol"]["name"] == "League of Legends"

    async def test_JsonStore_update_WithExistingKey_MergesWithoutLosingOtherKeys(self, tmp_path: Path):
        store = JsonStore(tmp_path / "s.json")
        await store.update("cache", {"key1": "value1"})
        await store.update("cache", {"key2": "value2"})
        cache = store.get("cache")
        assert cache["key1"] == "value1"
        assert cache["key2"] == "value2"

    async def test_JsonStore_update_WithOverlappingKey_OverwritesIt(self, tmp_path: Path):
        store = JsonStore(tmp_path / "s.json")
        await store.update("cache", {"game": "LoL"})
        await store.update("cache", {"game": "VALORANT"})
        assert store.get("cache")["game"] == "VALORANT"

    # -----------------------------------------------------------------------
    # set_sync
    # -----------------------------------------------------------------------

    def test_JsonStore_setSync_WithoutEventLoop_PersistsToFile(self, tmp_path: Path):
        path = tmp_path / "s.json"
        store = JsonStore(path)
        store.set_sync("token", "abc123")
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["token"] == "abc123"

    # -----------------------------------------------------------------------
    # corrupción / recuperación
    # -----------------------------------------------------------------------

    def test_JsonStore_read_WithCorruptFile_ReturnsEmptyDict(self, tmp_path: Path):
        path = tmp_path / "corrupt.json"
        path.write_text("{this is not valid json}", encoding="utf-8")
        store = JsonStore(path)
        assert store.all() == {}

    def test_JsonStore_read_WithCorruptFile_RenamesFileToBak(self, tmp_path: Path):
        path = tmp_path / "corrupt.json"
        path.write_text("{invalid}", encoding="utf-8")
        JsonStore(path)
        bak = path.with_suffix(".json.bak")
        assert bak.exists(), "Debería haberse creado el .bak del archivo corrupto"

    def test_JsonStore_read_WithEmptyFile_ReturnsEmptyDict(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        store = JsonStore(path)
        assert store.all() == {}

    # -----------------------------------------------------------------------
    # atomicidad
    # -----------------------------------------------------------------------

    async def test_JsonStore_set_Atomic_TmpFileRemovedAfterSuccess(self, tmp_path: Path):
        """El archivo .tmp no debe quedar huérfano después de una escritura exitosa."""
        store = JsonStore(tmp_path / "s.json")
        await store.set("x", 1)
        tmps = list(tmp_path.glob("*.tmp"))
        assert tmps == [], f"Quedaron archivos temporales: {tmps}"

    async def test_JsonStore_set_Concurrent_AllValuesPresent(self, tmp_path: Path):
        """Escrituras concurrentes no corrompen el archivo."""
        store = JsonStore(tmp_path / "s.json")
        keys = [f"key{i}" for i in range(20)]
        await asyncio.gather(*(store.set(k, i) for i, k in enumerate(keys)))
        for i, k in enumerate(keys):
            assert store.get(k) == i
