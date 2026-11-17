"""Polite HTTP layer for the CHRIST site scraper.

- identifying User-Agent, ~4 requests/second overall (global rate limiter)
- low concurrency, retries with backoff, per-host failure circuit breaker
- respects robots.txt when a real one exists (this site serves HTML for /robots.txt => no rules)
- everything is cached on disk, so re-runs and re-extraction never re-fetch
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

UA = "Mozilla/5.0 (compatible; ChristAdmissionsAssistantBot/1.0; public-info indexer)"


@dataclass
class Result:
    status: int
    body: bytes
    content_type: str
    final_url: str
    cached: bool = False

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, cache_dir: Path, concurrency: int = 4, min_interval: float = 0.25, timeout: float = 40.0):
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.sem = asyncio.Semaphore(concurrency)
        self.min_interval = min_interval
        self._next = 0.0
        self._lock = asyncio.Lock()
        self.client = httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=timeout)
        self.fail: dict[str, int] = {}
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests = 0

    async def close(self):
        await self.client.aclose()

    # ------------------------------------------------------------ cache
    def _key(self, method: str, url: str, data: dict | None) -> str:
        raw = f"{method} {url} {json.dumps(data, sort_keys=True) if data else ''}"
        return hashlib.sha1(raw.encode()).hexdigest()

    def _load(self, key: str) -> Result | None:
        meta, body = self.cache / f"{key}.json", self.cache / f"{key}.gz"
        if meta.exists() and body.exists():
            m = json.loads(meta.read_text("utf-8"))
            return Result(m["status"], gzip.decompress(body.read_bytes()), m["ct"], m["url"], cached=True)
        return None

    def _save(self, key: str, r: Result):
        (self.cache / f"{key}.gz").write_bytes(gzip.compress(r.body))
        (self.cache / f"{key}.json").write_text(json.dumps({"status": r.status, "ct": r.content_type, "url": r.final_url}), "utf-8")

    # ------------------------------------------------------------ robots
    async def allowed(self, url: str) -> bool:
        host = urlsplit(url).netloc
        if host not in self.robots:
            rp = None
            try:
                r = await self._raw("GET", f"https://{host}/robots.txt", None, retries=1)
                if r and r.status == 200 and "text/plain" in r.content_type:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(r.text.splitlines())
            except Exception:  # noqa: BLE001
                rp = None
            self.robots[host] = rp
        rp = self.robots[host]
        return True if rp is None else rp.can_fetch(UA, url)

    # ------------------------------------------------------------ fetch
    async def _throttle(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self.min_interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def _raw(self, method: str, url: str, data: dict | None, retries: int = 3) -> Result | None:
        host = urlsplit(url).netloc
        if self.fail.get(host, 0) >= 25:
            return None
        for attempt in range(retries):
            async with self.sem:
                await self._throttle()
                try:
                    self.requests += 1
                    resp = await (self.client.post(url, data=data) if method == "POST" else self.client.get(url))
                except httpx.HTTPError:
                    self.fail[host] = self.fail.get(host, 0) + 1
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
            if resp.status_code in (429, 500, 502, 503, 504):
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            self.fail[host] = 0
            return Result(resp.status_code, resp.content, resp.headers.get("content-type", ""), str(resp.url))
        return None

    async def request(self, method: str, url: str, data: dict | None = None, *, max_bytes: int = 30_000_000) -> Result | None:
        key = self._key(method, url, data)
        hit = self._load(key)
        if hit:
            return hit
        if not await self.allowed(url):
            return None
        r = await self._raw(method, url, data)
        if r is None or len(r.body) > max_bytes:
            return None
        if r.status < 400:  # only cache successes
            self._save(key, r)
        return r

    async def get(self, url: str, **kw) -> Result | None:
        return await self.request("GET", url, None, **kw)

    async def post(self, url: str, data: dict) -> Result | None:
        return await self.request("POST", url, data)
