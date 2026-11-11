import asyncio
import json
import logging
from pathlib import Path

import aiohttp

from config import API_BASE_URL, API_TOKEN, API_TIMEOUT_SECONDS

log = logging.getLogger("diskwala-api")


class DiskwalaAPIError(Exception):
    pass


class DiskwalaFileTooLarge(DiskwalaAPIError):
    pass


class DiskwalaAPI:
    def __init__(self):
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS, connect=20)
        self.timeout = timeout
        self.session = None
        self._semaphore = asyncio.Semaphore(5)

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {API_TOKEN}"},
            )
        return self.session

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _request_json(self, url, params=None):
        async with self._semaphore:
            for attempt in range(3):
                try:
                    session = await self._get_session()
                    async with session.get(url, params=params) as response:
                        if response.status == 429:
                            retry_after = int(response.headers.get("Retry-After", "2"))
                            if attempt < 2:
                                await asyncio.sleep(min(max(retry_after, 1), 15))
                                continue
                            raise DiskwalaAPIError("API rate limit reached. Try again shortly.")
                        if response.status == 401:
                            raise DiskwalaAPIError("Invalid API token. Check DISKWALA_API_TOKEN.")
                        if response.status == 403:
                            raise DiskwalaAPIError("API plan inactive or API usage limit reached.")
                        if response.status >= 400:
                            body = (await response.text())[:300]
                            raise DiskwalaAPIError(
                                f"API returned HTTP {response.status}: {body}"
                            )
                        try:
                            return await response.json(content_type=None)
                        except (aiohttp.ContentTypeError, json.JSONDecodeError):
                            raise DiskwalaAPIError("API returned an invalid JSON response.")
                except asyncio.TimeoutError:
                    if attempt == 2:
                        raise DiskwalaAPIError("API request timed out.")
                    await asyncio.sleep(1 + attempt)
                except aiohttp.ClientError as exc:
                    if attempt == 2:
                        raise DiskwalaAPIError(f"API connection error: {exc}")
                    await asyncio.sleep(1 + attempt)
        raise DiskwalaAPIError("API request failed.")

    async def list_files(self, share_url: str):
        data = await self._request_json(
            API_BASE_URL.rstrip("/") + "/",
            params={"url": share_url}
        )
        if isinstance(data, dict):
            # Accept common wrapped response formats as well as the documented array.
            data = data.get("files") or data.get("data") or [data]
        if not isinstance(data, list):
            raise DiskwalaAPIError("Unexpected file-list response format.")
        return [entry for entry in data if isinstance(entry, dict)]

    async def health(self):
        data = await self._request_json(API_BASE_URL.rstrip("/") + "/health")
        return isinstance(data, dict) and data.get("status") == "healthy"

    async def download_file(self, file_id: str, destination: Path, max_bytes: int):
        url = API_BASE_URL.rstrip("/") + "/fast-download"
        async with self._semaphore:
            for attempt in range(3):
                try:
                    session = await self._get_session()
                    async with session.get(
                        url, params={"file_id": file_id}
                    ) as response:
                        if response.status == 429:
                            retry_after = int(response.headers.get("Retry-After", "2"))
                            if attempt < 2:
                                await asyncio.sleep(min(max(retry_after, 1), 15))
                                continue
                            raise DiskwalaAPIError("API rate limit reached. Try again shortly.")
                        if response.status == 401:
                            raise DiskwalaAPIError("Invalid API token. Check DISKWALA_API_TOKEN.")
                        if response.status == 403:
                            raise DiskwalaAPIError("API plan inactive or API usage limit reached.")
                        if response.status >= 400:
                            body = (await response.text())[:300]
                            raise DiskwalaAPIError(
                                f"Download endpoint returned HTTP {response.status}: {body}"
                            )

                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) > max_bytes:
                            raise DiskwalaFileTooLarge("File exceeds the Telegram upload size limit.")

                        total = 0
                        with destination.open("wb") as output:
                            async for chunk in response.content.iter_chunked(1024 * 256):
                                if not chunk:
                                    continue
                                total += len(chunk)
                                if total > max_bytes:
                                    raise DiskwalaFileTooLarge("File exceeds the Telegram upload size limit.")
                                output.write(chunk)
                        return total
                except asyncio.TimeoutError:
                    if attempt == 2:
                        raise DiskwalaAPIError("Download timed out.")
                    await asyncio.sleep(1 + attempt)
                except aiohttp.ClientError as exc:
                    if attempt == 2:
                        raise DiskwalaAPIError(f"Download connection error: {exc}")
                    await asyncio.sleep(1 + attempt)
        raise DiskwalaAPIError("File download failed.")
