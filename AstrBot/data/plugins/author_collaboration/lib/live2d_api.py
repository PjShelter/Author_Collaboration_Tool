"""Shelter Live2D mirror helpers."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


BASE_URL = "https://live2d.shelter.net.cn"
API_BASE = f"{BASE_URL}/mirror/bestdori-api"
ASSETS_BASE = f"{BASE_URL}/mirror/bestdori-assets"
LANG_CN = 3
LANG_EN = 1
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass(frozen=True)
class ModelRecord:
    model_name: str
    character_id: int
    character_name: str
    description: str

    @property
    def label(self) -> str:
        return f"{self.model_name} {self.description}".strip()


@dataclass(frozen=True)
class CardRecord:
    card_id: str
    character_id: int
    character_name: str
    title: str
    rarity: int
    resource_set_name: str
    normal_url: str
    trained_url: str | None


def _pick_text(values: Any) -> str:
    if isinstance(values, list):
        for idx in (LANG_CN, 2, LANG_EN, 0):
            if idx < len(values) and values[idx]:
                return str(values[idx]).strip()
    if values:
        return str(values).strip()
    return ""


def _normalize_token(text: str) -> str:
    return "".join(ch.lower() for ch in text if not ch.isspace())


def _text_candidates(values: Any) -> list[str]:
    if not isinstance(values, list):
        return [str(values).strip()] if values else []
    candidates: list[str] = []
    for value in values:
        text = str(value).strip() if value else ""
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def normalize_file_name(file_name: str, kind: str) -> str:
    if kind in {"model", "motion"}:
        return file_name.removesuffix(".bytes")
    if kind == "texture":
        if file_name.endswith(".bytes"):
            return file_name.removesuffix(".bytes") + ".png"
        return file_name if "." in file_name else f"{file_name}.png"
    return file_name


def asset_url(file_info: dict[str, Any], kind: str) -> str:
    bundle = str(file_info["bundleName"])
    file_name = normalize_file_name(str(file_info["fileName"]), kind)
    return f"{ASSETS_BASE}/jp/{quote(bundle)}_rip/{quote(file_name)}"


class ShelterLive2DClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._characters: dict[str, Any] | None = None
        self._costumes: dict[str, Any] | None = None
        self._cards: dict[str, Any] | None = None
        self._explorer_info: dict[str, Any] | None = None

    async def _get_json(self, url: str) -> Any:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def _head_size(self, url: str) -> int | None:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            try:
                response = await client.head(url)
                response.raise_for_status()
                value = response.headers.get("content-length")
                return int(value) if value and value.isdigit() else None
            except Exception:
                return None

    async def _download_bytes(self, url: str) -> bytes:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def characters(self) -> dict[str, Any]:
        if self._characters is None:
            self._characters = await self._get_json(f"{API_BASE}/characters/all.2.json")
        return self._characters

    async def costumes(self) -> dict[str, Any]:
        if self._costumes is None:
            self._costumes = await self._get_json(f"{API_BASE}/costumes/all.5.json")
        return self._costumes

    async def cards(self) -> dict[str, Any]:
        if self._cards is None:
            self._cards = await self._get_json(f"{API_BASE}/cards/all.5.json")
        return self._cards

    async def explorer_info(self) -> dict[str, Any]:
        if self._explorer_info is None:
            self._explorer_info = await self._get_json(f"{API_BASE}/explorer/jp/assets/_info.json")
        return self._explorer_info

    async def character_name(self, character_id: int) -> str:
        chars = await self.characters()
        data = chars.get(str(character_id), {})
        return _pick_text(data.get("characterName")) or str(character_id)

    async def character_aliases(self, character_id: int) -> list[str]:
        chars = await self.characters()
        data = chars.get(str(character_id), {})
        aliases = _text_candidates(data.get("characterName"))
        aliases.extend(_text_candidates(data.get("nickname")))
        return [item for index, item in enumerate(aliases) if item and item not in aliases[:index]]

    async def _model_records(self) -> list[ModelRecord]:
        costumes = await self.costumes()
        records_by_name: dict[str, ModelRecord] = {}
        records: list[ModelRecord] = []
        for item in costumes.values():
            model_name = str(item.get("assetBundleName") or "")
            if not model_name:
                continue
            character_id = int(item.get("characterId") or 0)
            records.append(
                ModelRecord(
                    model_name=model_name,
                    character_id=character_id,
                    character_name=await self.character_name(character_id),
                    description=_pick_text(item.get("description")),
                )
            )
            records_by_name[records[-1].model_name] = records[-1]
        info = await self.explorer_info()
        live2d_chara = ((info.get("live2d") or {}).get("chara") or {})
        for model_name in live2d_chara:
            if model_name in records_by_name:
                continue
            try:
                character_id = int(str(model_name).split("_", 1)[0])
            except (TypeError, ValueError):
                character_id = 0
            records.append(
                ModelRecord(
                    model_name=str(model_name),
                    character_id=character_id,
                    character_name=await self.character_name(character_id) if character_id else "",
                    description="",
                )
            )
        return records

    def _model_match_score(self, token: str, record: ModelRecord) -> int | None:
        if not token:
            return None
        model_token = _normalize_token(record.model_name)
        desc_token = _normalize_token(record.description)
        character_token = _normalize_token(record.character_name)
        if token == model_token:
            return 0
        if token in model_token:
            return 10
        if token == character_token:
            return 20
        if token in character_token:
            return 30
        if token == desc_token:
            return 40
        if token in desc_token:
            return 50
        return None

    async def search_models_with_total(
        self,
        query: str,
        limit: int = 8,
        offset: int = 0,
    ) -> tuple[list[ModelRecord], int]:
        token = _normalize_token(query)
        scored: list[tuple[int, str, ModelRecord]] = []
        for record in await self._model_records():
            score = self._model_match_score(token, record)
            if score is None:
                continue
            scored.append((score, record.model_name, record))
        scored.sort(key=lambda item: (item[0], item[1]))
        total = len(scored)
        return [record for _, _, record in scored[offset : offset + limit]], total

    async def search_models(self, query: str, limit: int = 8) -> list[ModelRecord]:
        results, _ = await self.search_models_with_total(query, limit=limit, offset=0)
        return results

    async def exact_model(self, model_name: str) -> ModelRecord | None:
        token = _normalize_token(model_name)
        for record in await self._model_records():
            if _normalize_token(record.model_name) == token:
                return record
        return None

    def _card_match_score(
        self,
        token: str,
        card_id: str,
        resource_set_name: str,
        title: str,
        character_aliases: list[str],
    ) -> int | None:
        if not token:
            return None
        norm_card_id = _normalize_token(card_id)
        norm_resource = _normalize_token(resource_set_name)
        norm_title = _normalize_token(title)
        norm_aliases = [_normalize_token(alias) for alias in character_aliases]
        if token in {norm_card_id, norm_resource}:
            return 0
        if any(token == alias for alias in norm_aliases):
            return 10
        if any(token in alias for alias in norm_aliases):
            return 20
        if token == norm_title:
            return 30
        if token in norm_title:
            return 40
        if token in norm_resource or token in norm_card_id:
            return 50
        haystack = _normalize_token(" ".join([card_id, resource_set_name, title, *character_aliases]))
        if token in haystack:
            return 90
        return None

    async def search_cards_with_total(
        self,
        query: str,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[CardRecord], int]:
        token = _normalize_token(query)
        exact_identifier_query = bool(
            re.fullmatch(r"\d+|res\d+", query.strip(), flags=re.IGNORECASE)
        )
        cards = await self.cards()
        scored: list[tuple[int, int, CardRecord]] = []
        for card_id, item in cards.items():
            character_id = int(item.get("characterId") or 0)
            character_name = await self.character_name(character_id)
            character_aliases = await self.character_aliases(character_id)
            title = _pick_text(item.get("prefix"))
            resource_set_name = str(item.get("resourceSetName") or "")
            score = self._card_match_score(
                token,
                str(card_id),
                resource_set_name,
                title,
                character_aliases,
            )
            if score is None:
                continue
            if exact_identifier_query and score != 0:
                continue
            base = f"{ASSETS_BASE}/jp/characters/resourceset/{resource_set_name}_rip"
            trained = None
            stat = item.get("stat") or {}
            if isinstance(stat, dict) and stat.get("training"):
                trained = f"{base}/card_after_training.png"
            record = CardRecord(
                card_id=str(card_id),
                character_id=character_id,
                character_name=character_name,
                title=title,
                rarity=int(item.get("rarity") or 0),
                resource_set_name=resource_set_name,
                normal_url=f"{base}/card_normal.png",
                trained_url=trained,
            )
            sort_id = int(card_id) if str(card_id).isdigit() else 0
            scored.append((score, sort_id, record))
        scored.sort(key=lambda item: (item[0], item[1]))
        total = len(scored)
        return [record for _, _, record in scored[offset : offset + limit]], total

    async def search_cards(self, query: str, limit: int = 5) -> list[CardRecord]:
        results, _ = await self.search_cards_with_total(query, limit=limit, offset=0)
        return results

    async def build_data(self, model_name: str) -> dict[str, Any]:
        url = f"{ASSETS_BASE}/jp/live2d/chara/{quote(model_name)}_rip/buildData.asset"
        data = await self._get_json(url)
        return data["Base"]

    async def model_files(self, model_name: str) -> list[tuple[str, str]]:
        data = await self.build_data(model_name)
        files: list[tuple[str, str]] = []
        files.append(("model", asset_url(data["model"], "model")))
        if data.get("physics"):
            files.append(("physics", asset_url(data["physics"], "physics")))
        for file_info in data.get("textures") or []:
            files.append(("texture", asset_url(file_info, "texture")))
        for file_info in data.get("motions") or []:
            files.append(("motion", asset_url(file_info, "motion")))
        for file_info in data.get("expressions") or []:
            files.append(("expression", asset_url(file_info, "expression")))
        return files

    async def estimate_model_size(self, model_name: str) -> int:
        files = await self.model_files(model_name)
        sizes = await asyncio.gather(*(self._head_size(url) for _, url in files))
        return sum(size for size in sizes if size is not None)

    async def package_model(self, model_name: str, target_dir: Path) -> Path:
        files = await self.model_files(model_name)
        target_dir.mkdir(parents=True, exist_ok=True)
        zip_path = target_dir / f"{model_name}.zip"
        manifest = {"modelName": model_name, "files": [url for _, url in files]}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            model_json: dict[str, Any] = {
                "type": "Live2D Model Setting",
                "name": model_name,
                "model": "data/model.moc",
                "textures": [],
                "motions": {"idle": []},
                "expressions": [],
            }
            download_jobs: list[tuple[str, str, str]] = []
            for index, (kind, url) in enumerate(files, start=1):
                name = os.path.basename(url.split("?", 1)[0]) or f"{kind}_{index}"
                if kind == "model":
                    local_name = "data/model.moc"
                elif kind == "physics":
                    local_name = f"data/{name}"
                    model_json["physics"] = local_name
                elif kind == "texture":
                    local_name = f"data/textures/{name}"
                    model_json["textures"].append(local_name)
                elif kind == "motion":
                    local_name = f"data/motions/{name}"
                    model_json["motions"]["idle"].append({"file": local_name})
                elif kind == "expression":
                    local_name = f"data/expressions/{name}"
                    model_json["expressions"].append(
                        {"name": os.path.splitext(name)[0], "file": local_name}
                    )
                else:
                    local_name = f"data/{index:03d}_{kind}_{name}"
                download_jobs.append((url, local_name, kind))

            semaphore = asyncio.Semaphore(8)

            async def download_one(client: httpx.AsyncClient, url: str, local_name: str) -> tuple[Path, str]:
                async with semaphore:
                    response = await client.get(url)
                    response.raise_for_status()
                    local_path = tmp_path / local_name
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(response.content)
                    return local_path, local_name

            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                downloaded = await asyncio.gather(
                    *(download_one(client, url, local_name) for url, local_name, _ in download_jobs)
                )
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                zf.writestr("model.json", json.dumps(model_json, ensure_ascii=False, indent=2))
                for local_path, local_name in downloaded:
                    zf.write(local_path, local_name)
        return zip_path


def format_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f}MB"
    if value >= 1024:
        return f"{value / 1024:.1f}KB"
    return f"{value}B"
