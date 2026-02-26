"""
PostgreSQL 연결 + 테이블 생성 모듈
asyncpg 기반 Raw SQL, ORM 미사용
"""
import os
from typing import Optional
from src.utils.logger import logger

# asyncpg는 선택적 의존성 -- 설치되지 않은 환경에서도 import 에러 방지
try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore

_pool: Optional["asyncpg.Pool"] = None  # type: ignore


# ---- DDL ----

_CREATE_TABLES_SQL = """
-- 유저
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    wallet TEXT UNIQUE NOT NULL,
    api_key TEXT UNIQUE,
    tier TEXT NOT NULL DEFAULT 'free',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 규칙 세트
CREATE TABLE IF NOT EXISTS rule_sets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    blocks JSONB NOT NULL DEFAULT '[]',
    owner TEXT NOT NULL,
    is_template BOOLEAN DEFAULT FALSE,
    template_category TEXT,
    use_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 시뮬레이션 기록
CREATE TABLE IF NOT EXISTS simulation_logs (
    id SERIAL PRIMARY KEY,
    rule_set_id TEXT REFERENCES rule_sets(id) ON DELETE SET NULL,
    owner TEXT NOT NULL,
    total_txs INTEGER,
    processed INTEGER,
    filtered INTEGER,
    results JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 이벤트 로그
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 설정
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_users_wallet ON users(wallet);
CREATE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key);
CREATE INDEX IF NOT EXISTS idx_rule_sets_owner ON rule_sets(owner);
CREATE INDEX IF NOT EXISTS idx_rule_sets_template ON rule_sets(is_template) WHERE is_template = TRUE;
CREATE INDEX IF NOT EXISTS idx_simulation_logs_owner ON simulation_logs(owner);
CREATE INDEX IF NOT EXISTS idx_simulation_logs_created ON simulation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


async def init_database() -> None:
    """커넥션 풀 생성 + 테이블/인덱스 생성"""
    global _pool

    if asyncpg is None:
        logger.warning("asyncpg 미설치 -- DB 기능 비활성화 (pip install asyncpg)")
        return

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.warning("DATABASE_URL 환경변수 미설정 -- DB 없이 in-memory 모드로 동작")
        return

    try:
        _pool = await asyncpg.create_pool(
            database_url,
            min_size=2,
            max_size=10,
        )
        logger.info("PostgreSQL 커넥션 풀 생성 완료")

        # 테이블 + 인덱스 생성
        async with _pool.acquire() as conn:
            await conn.execute(_CREATE_TABLES_SQL)
            await conn.execute(_CREATE_INDEXES_SQL)
        logger.info("데이터베이스 테이블/인덱스 초기화 완료")

    except Exception as e:
        logger.error(f"PostgreSQL 연결 실패: {e}")
        _pool = None


async def close_database() -> None:
    """커넥션 풀 종료"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL 커넥션 풀 종료")


def get_pool() -> Optional["asyncpg.Pool"]:  # type: ignore
    """현재 커넥션 풀 반환 (없으면 None)"""
    return _pool
