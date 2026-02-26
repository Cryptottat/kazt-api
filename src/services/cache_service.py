"""
캐시 서비스 -- Redis 기반 캐싱 레이어
Redis 미연결시 모든 메서드가 None/0을 반환하여 graceful degradation 보장
"""
import json
from typing import Any, Callable, Awaitable, Optional
from src.cache import get_redis
from src.utils.logger import logger


# TTL 가이드 (초)
TTL_RULE_SET = 300       # 규칙 세트: 5분
TTL_SIMULATION = 30      # 시뮬레이션 결과: 30초
TTL_SESSION = 86400      # 사용자 세션: 24시간
TTL_TEMPLATE = 600       # 템플릿: 10분


def _make_key(data_type: str, identifier: str) -> str:
    """캐시 키 생성 -- kazt:{데이터유형}:{식별자}"""
    return f"kazt:{data_type}:{identifier}"


async def get(key: str) -> Optional[Any]:
    """캐시에서 값 조회"""
    r = get_redis()
    if r is None:
        return None

    try:
        raw = await r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # JSON이 아닌 단순 문자열인 경우
        return raw
    except Exception as e:
        logger.warning(f"캐시 조회 실패 (key={key}): {e}")
        return None


async def set(key: str, value: Any, ttl: int = 300) -> bool:
    """캐시에 값 저장"""
    r = get_redis()
    if r is None:
        return False

    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        await r.set(key, serialized, ex=ttl)
        return True
    except Exception as e:
        logger.warning(f"캐시 저장 실패 (key={key}): {e}")
        return False


async def delete(key: str) -> bool:
    """캐시에서 키 삭제"""
    r = get_redis()
    if r is None:
        return False

    try:
        await r.delete(key)
        return True
    except Exception as e:
        logger.warning(f"캐시 삭제 실패 (key={key}): {e}")
        return False


async def get_or_fetch(
    key: str,
    fetch_fn: Callable[[], Awaitable[Any]],
    ttl: int = 300,
) -> Optional[Any]:
    """
    캐시 조회 후 미스이면 fetch_fn 실행하여 캐시에 저장
    Redis 미연결시에도 fetch_fn은 실행된다
    """
    # 캐시 히트 시도
    cached = await get(key)
    if cached is not None:
        return cached

    # 캐시 미스 -- fetch_fn 실행
    value = await fetch_fn()
    if value is not None:
        await set(key, value, ttl)
    return value


async def increment(key: str, ttl: int = 86400) -> int:
    """
    Rate limiting용 카운터 증가
    Redis 미연결시 0 반환 (in-memory 폴백은 호출부에서 처리)
    """
    r = get_redis()
    if r is None:
        return 0

    try:
        count = await r.incr(key)
        # 키가 새로 생성된 경우 TTL 설정
        if count == 1:
            await r.expire(key, ttl)
        return count
    except Exception as e:
        logger.warning(f"캐시 카운터 증가 실패 (key={key}): {e}")
        return 0
