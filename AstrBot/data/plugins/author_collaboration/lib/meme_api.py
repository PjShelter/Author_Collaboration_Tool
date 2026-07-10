"""meme-generator API helpers."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx


_log = logging.getLogger("author_collaboration.meme_api")

DEFAULT_BASE_URL = "http://meme-generator:2233"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Listing cache TTL: 1 hour. The meme-generator rarely adds new templates,
# so a longer TTL keeps the listing endpoint snappy.
_LIST_CACHE_TTL = 3600.0


@dataclass(frozen=True)
class MemeResult:
    content: bytes
    content_type: str
    suffix: str


def avatar_url(user_id: int | str) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"


def suffix_from_content_type(content_type: str) -> str:
    content_type = content_type.lower().split(";", 1)[0].strip()
    if content_type == "image/gif":
        return ".gif"
    if content_type == "image/png":
        return ".png"
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if content_type == "image/webp":
        return ".webp"
    return ".png"


class MemeGeneratorClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Cache: {key: info_dict} and {keyword_or_shortcut: key}
        self._templates: dict[str, dict[str, Any]] = {}
        self._keyword_index: dict[str, str] = {}
        self._cache_loaded_at: float = 0.0
        self._cache_lock = asyncio.Lock()
        self._loading = False

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/docs")
            return response.status_code < 500
        except Exception:
            return False

    async def warm_cache(self) -> bool:
        """Pre-load template listing in background; safe to call multiple times."""
        try:
            await self._ensure_cache(force=True)
            return bool(self._templates)
        except Exception as e:
            _log.warning(f"meme-generator cache warm failed: {e}")
            return False

    async def _ensure_cache(self, force: bool = False) -> None:
        async with self._cache_lock:
            now = time.monotonic()
            if (
                not force
                and self._templates
                and now - self._cache_loaded_at < _LIST_CACHE_TTL
            ):
                return
            await self._refresh_cache()

    async def _refresh_cache(self) -> None:
        # Step 1: fetch key list
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{self.base_url}/memes/keys")
            r.raise_for_status()
            keys = r.json()

        # Step 2: fetch /memes/{key}/info concurrently with bounded pool
        sem = asyncio.Semaphore(20)
        templates: dict[str, dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=10.0) as c:
            async def fetch_one(k: str) -> None:
                async with sem:
                    try:
                        rr = await c.get(f"{self.base_url}/memes/{k}/info")
                        if rr.status_code == 200:
                            templates[k] = rr.json()
                    except Exception:
                        pass

            await asyncio.gather(*(fetch_one(k) for k in keys))

        # Step 3: build keyword -> key index (first match wins on collision)
        keyword_index: dict[str, str] = {}
        for k, info in templates.items():
            for kw in list(info.get("keywords") or []) + list(info.get("shortcuts") or []):
                if isinstance(kw, str) and kw and kw not in keyword_index:
                    keyword_index[kw] = k

        self._templates = templates
        self._keyword_index = keyword_index
        self._cache_loaded_at = time.monotonic()
        _log.info(
            f"meme-generator cache refreshed: {len(templates)} templates, "
            f"{len(keyword_index)} aliases"
        )

    async def find_by_keyword(self, keyword: str) -> dict[str, Any] | None:
        """Find the first template whose keywords/shortcuts include keyword.

        Returns the full info dict, or None if keyword is unknown.
        Lazily warms the cache on first call.
        """
        if not keyword:
            return None
        await self._ensure_cache()
        key = self._keyword_index.get(keyword)
        if not key:
            return None
        info = self._templates.get(key)
        if info is None:
            return None
        return {"key": key, **info}

    async def template_info(self, key: str) -> dict[str, Any] | None:
        """Return cached template info by key."""
        if not key:
            return None
        await self._ensure_cache()
        info = self._templates.get(key)
        if info is None:
            return None
        return {"key": key, **info}

    async def list_keywords(self) -> list[tuple[str, str]]:
        """Return [(alias, template_key)] sorted by alias for stable pagination."""
        await self._ensure_cache()
        return sorted(self._keyword_index.items())

    async def list_templates(self) -> list[dict[str, Any]]:
        """Return all template info dicts, sorted by key."""
        await self._ensure_cache()
        return [self._templates[k] for k in sorted(self._templates.keys())]

    async def _download_image(self, url: str) -> tuple[str, bytes, str]:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "image/jpeg")
            suffix = suffix_from_content_type(content_type)
            return f"image{suffix}", response.content, content_type

    async def generate(
        self,
        key: str,
        image_urls: list[str],
        texts: list[str] | None = None,
        args: dict[str, Any] | None = None,
    ) -> MemeResult:
        files = []
        for url in image_urls:
            filename, content, content_type = await self._download_image(url)
            files.append(("images", (filename, content, content_type)))

        clean_texts = [text for text in texts or [] if text]
        data: dict[str, Any] = {"args": json.dumps(args or {}, ensure_ascii=False)}
        if clean_texts:
            data["texts"] = clean_texts

        response = await asyncio.to_thread(self._post_meme, key, files, data)

        if response.status_code >= 400:
            detail = response.text.strip()
            raise RuntimeError(f"HTTP {response.status_code}: {detail[:300]}")

        content_type = response.headers.get("content-type", "image/png")
        return MemeResult(
            content=response.content,
            content_type=content_type,
            suffix=suffix_from_content_type(content_type),
        )

    def _post_meme(
        self,
        key: str,
        files: list[tuple[str, tuple[str, bytes, str]]],
        data: dict[str, Any],
    ) -> httpx.Response:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            return client.post(
                f"{self.base_url}/memes/{key}/",
                files=files,
                data=data,
            )
