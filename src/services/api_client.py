"""
범용 HTTP 클라이언트
- httpx.AsyncClient 기반
- retry, timeout, 지수 백오프
- Rate limit 대응
"""
import asyncio
import time
from typing import Optional

import httpx

from src.utils.logger import logger


class APIClient:
    """범용 비동기 HTTP 클라이언트 (재시도 + 지수 백오프)"""

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 10.0,
        max_retries: int = 3,
        headers: Optional[dict] = None,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            headers=headers or {},
        )

    async def close(self):
        """HTTP 클라이언트 종료"""
        await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """
        HTTP 요청 (자동 재시도 포함)
        - 429: Retry-After 헤더를 존중하여 대기 후 재시도
        - 500+: 지수 백오프로 재시도
        - 400/401/403/404: 재시도 없이 즉시 반환
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(method, url, **kwargs)

                # 성공 응답
                if response.status_code < 400:
                    return response

                # 클라이언트 에러 -- 재시도 불필요
                if response.status_code in (400, 401, 403, 404):
                    return response

                # Rate limit (429) -- Retry-After 존중
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        wait_time = float(retry_after)
                    else:
                        wait_time = 2 ** attempt
                    logger.warning(
                        f"Rate limited (429). 대기 {wait_time}초 후 재시도 "
                        f"(시도 {attempt + 1}/{self.max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                    continue

                # 서버 에러 (500+) -- 지수 백오프 재시도
                if response.status_code >= 500:
                    wait_time = 2 ** attempt
                    logger.warning(
                        f"서버 에러 ({response.status_code}). "
                        f"대기 {wait_time}초 후 재시도 "
                        f"(시도 {attempt + 1}/{self.max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                    continue

                # 기타 에러 -- 그대로 반환
                return response

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exception = e
                wait_time = 2 ** attempt
                logger.warning(
                    f"요청 실패: {e}. 대기 {wait_time}초 후 재시도 "
                    f"(시도 {attempt + 1}/{self.max_retries})"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(wait_time)

        # 모든 재시도 실패
        if last_exception:
            raise last_exception
        # 마지막 응답 반환 (500+ 등)
        return response  # type: ignore[possibly-undefined]

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)


class RateLimitedClient(APIClient):
    """초당 요청 수 제한이 있는 HTTP 클라이언트"""

    def __init__(
        self,
        requests_per_second: float = 5.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.min_interval = 1.0 / requests_per_second
        self._last_request_time: float = 0.0
        self._lock = asyncio.Lock()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Rate limit을 적용한 HTTP 요청"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                await asyncio.sleep(wait_time)
            self._last_request_time = time.monotonic()

        return await super().request(method, url, **kwargs)
