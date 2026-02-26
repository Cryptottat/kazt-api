"""
Redis 연결 모듈
redis.asyncio 기반
"""
import os
import asyncio
from typing import Optional
from src.utils.logger import logger

# redis는 선택적 의존성
try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore

_redis: Optional["aioredis.Redis"] = None  # type: ignore


async def init_redis() -> None:
    """Redis 연결 초기화"""
    global _redis

    if aioredis is None:
        logger.warning("redis 패키지 미설치 -- 캐시 기능 비활성화 (pip install redis)")
        return

    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        logger.warning("REDIS_URL 환경변수 미설정 -- 캐시 없이 동작")
        return

    try:
        _redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=10,
        )
        # 연결 테스트 (타임아웃 포함)
        await asyncio.wait_for(_redis.ping(), timeout=10)
        logger.info("Redis 연결 완료")
    except asyncio.TimeoutError:
        logger.error("Redis 연결 타임아웃 (10초) -- 캐시 없이 동작")
        _redis = None
    except Exception as e:
        logger.error(f"Redis 연결 실패: {e}")
        _redis = None


async def close_redis() -> None:
    """Redis 연결 종료"""
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
        logger.info("Redis 연결 종료")


def get_redis() -> Optional["aioredis.Redis"]:  # type: ignore
    """현재 Redis 클라이언트 반환 (없으면 None)"""
    return _redis
