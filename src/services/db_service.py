"""
DB CRUD 헬퍼 -- PostgreSQL asyncpg 기반
pool이 None이면 None 또는 빈 리스트를 반환하여 DB 없이도 동작
"""
import json
from typing import Optional
from src.database import get_pool
from src.utils.logger import logger


# ---- Users ----

async def upsert_user(
    wallet: str,
    api_key: Optional[str] = None,
    tier: str = "free",
) -> Optional[dict]:
    """유저 upsert -- wallet 기준으로 존재하면 업데이트, 없으면 삽입"""
    pool = get_pool()
    if pool is None:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (wallet, api_key, tier, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (wallet)
                DO UPDATE SET
                    api_key = COALESCE($2, users.api_key),
                    tier = $3,
                    updated_at = NOW()
                RETURNING id, wallet, api_key, tier, created_at, updated_at
                """,
                wallet,
                api_key,
                tier,
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"유저 upsert 실패 (wallet={wallet[:8]}...): {e}")
        return None


async def get_user_by_wallet(wallet: str) -> Optional[dict]:
    """지갑 주소로 유저 조회"""
    pool = get_pool()
    if pool is None:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, wallet, api_key, tier, created_at, updated_at FROM users WHERE wallet = $1",
                wallet,
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"유저 조회 실패 (wallet={wallet[:8]}...): {e}")
        return None


async def get_user_by_api_key(api_key: str) -> Optional[dict]:
    """API 키로 유저 조회"""
    pool = get_pool()
    if pool is None:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, wallet, api_key, tier, created_at, updated_at FROM users WHERE api_key = $1",
                api_key,
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"유저 조회 실패 (api_key): {e}")
        return None


# ---- Rule Sets ----

async def save_rule_set(
    id: str,
    name: str,
    description: str,
    blocks: list,
    owner: str,
) -> Optional[dict]:
    """규칙 세트 저장 (upsert)"""
    pool = get_pool()
    if pool is None:
        return None

    try:
        blocks_json = json.dumps(blocks, ensure_ascii=False, default=str)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO rule_sets (id, name, description, blocks, owner, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, NOW())
                ON CONFLICT (id)
                DO UPDATE SET
                    name = $2,
                    description = $3,
                    blocks = $4::jsonb,
                    updated_at = NOW()
                RETURNING id, name, description, blocks, owner, is_template,
                          template_category, use_count, created_at, updated_at
                """,
                id,
                name,
                description,
                blocks_json,
                owner,
            )
            if row:
                result = dict(row)
                # JSONB 필드를 파이썬 객체로 변환
                if isinstance(result.get("blocks"), str):
                    result["blocks"] = json.loads(result["blocks"])
                return result
            return None
    except Exception as e:
        logger.error(f"규칙 세트 저장 실패 (id={id}): {e}")
        return None


async def get_rule_set(id: str) -> Optional[dict]:
    """규칙 세트 단건 조회"""
    pool = get_pool()
    if pool is None:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, description, blocks, owner, is_template,
                       template_category, use_count, created_at, updated_at
                FROM rule_sets WHERE id = $1
                """,
                id,
            )
            if row:
                result = dict(row)
                if isinstance(result.get("blocks"), str):
                    result["blocks"] = json.loads(result["blocks"])
                return result
            return None
    except Exception as e:
        logger.error(f"규칙 세트 조회 실패 (id={id}): {e}")
        return None


async def get_user_rule_sets(owner: str) -> list[dict]:
    """사용자의 규칙 세트 목록 조회"""
    pool = get_pool()
    if pool is None:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, description, blocks, owner, is_template,
                       template_category, use_count, created_at, updated_at
                FROM rule_sets
                WHERE owner = $1
                ORDER BY updated_at DESC
                """,
                owner,
            )
            results = []
            for row in rows:
                r = dict(row)
                if isinstance(r.get("blocks"), str):
                    r["blocks"] = json.loads(r["blocks"])
                results.append(r)
            return results
    except Exception as e:
        logger.error(f"유저 규칙 목록 조회 실패 (owner={owner[:8]}...): {e}")
        return []


# ---- Simulation Logs ----

async def log_simulation(
    rule_set_id: Optional[str],
    owner: str,
    total_txs: int,
    processed: int,
    filtered: int,
    results: list,
) -> Optional[dict]:
    """시뮬레이션 결과 기록"""
    pool = get_pool()
    if pool is None:
        return None

    try:
        results_json = json.dumps(results, ensure_ascii=False, default=str)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO simulation_logs
                    (rule_set_id, owner, total_txs, processed, filtered, results)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id, rule_set_id, owner, total_txs, processed, filtered, results, created_at
                """,
                rule_set_id,
                owner,
                total_txs,
                processed,
                filtered,
                results_json,
            )
            if row:
                result = dict(row)
                if isinstance(result.get("results"), str):
                    result["results"] = json.loads(result["results"])
                return result
            return None
    except Exception as e:
        logger.error(f"시뮬레이션 로그 저장 실패: {e}")
        return None


async def get_user_simulations(owner: str, limit: int = 20) -> list[dict]:
    """사용자의 시뮬레이션 기록 조회"""
    pool = get_pool()
    if pool is None:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, rule_set_id, owner, total_txs, processed, filtered, results, created_at
                FROM simulation_logs
                WHERE owner = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                owner,
                limit,
            )
            results = []
            for row in rows:
                r = dict(row)
                if isinstance(r.get("results"), str):
                    r["results"] = json.loads(r["results"])
                results.append(r)
            return results
    except Exception as e:
        logger.error(f"시뮬레이션 기록 조회 실패 (owner={owner[:8]}...): {e}")
        return []
