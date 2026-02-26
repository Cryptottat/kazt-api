import uuid
import time
import random
from typing import Optional
from src.models.rules import (
    RuleBlock, RuleBlockType, ValidateResponse, SimulateRequest,
    SimulationTxResult, SimulationReport, RuleSetCreate
)
from src.utils.logger import logger


class RuleService:
    """ACE 규칙 검증 및 시뮬레이션 엔진"""

    def __init__(self):
        # In-memory storage (Phase 5에서 PostgreSQL로 교체)
        self.saved_rules: dict[str, dict] = {}

    def validate(self, blocks: list[RuleBlock]) -> ValidateResponse:
        """규칙 블록 세트의 유효성을 검증한다."""
        conflicts = []
        warnings = []
        cycle_detected = False

        if not blocks:
            return ValidateResponse(
                valid=False,
                conflicts=["No blocks provided"],
                warnings=[],
                cycle_detected=False,
            )

        # 1. 순환 참조 검사
        graph = {b.id: b.connections for b in blocks}
        block_ids = {b.id for b in blocks}

        # 존재하지 않는 연결 검사
        for block in blocks:
            for conn_id in block.connections:
                if conn_id not in block_ids:
                    conflicts.append(
                        f"Block '{block.id}' connects to non-existent block '{conn_id}'"
                    )

        # DFS 순환 검사
        visited = set()
        rec_stack = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for block_id in block_ids:
            if block_id not in visited:
                if has_cycle(block_id):
                    cycle_detected = True
                    conflicts.append("Circular dependency detected in rule chain")
                    break

        # 2. 규칙 충돌 검사
        block_types = [b.type for b in blocks]

        # 동일 타입 블록 중복 경고
        from collections import Counter
        type_counts = Counter(block_types)
        for btype, count in type_counts.items():
            if count > 1:
                warnings.append(
                    f"Multiple '{btype.value}' blocks ({count}). "
                    f"Ensure they are in sequence, not parallel."
                )

        # ordering + matching 충돌 검사
        ordering_blocks = [b for b in blocks if b.type == RuleBlockType.ORDERING]
        matching_blocks = [b for b in blocks if b.type == RuleBlockType.MATCHING]

        for ob in ordering_blocks:
            method = ob.params.get("method", "FIFO")
            for mb in matching_blocks:
                engine = mb.params.get("engine", "clob")
                if method == "pro_rata" and engine == "clob":
                    conflicts.append(
                        "Pro-rata ordering is incompatible with CLOB matching engine. "
                        "Use 'amm' or 'rfq' matching instead."
                    )

        # filter 블록의 blacklist/whitelist 동시 사용 경고
        for block in blocks:
            if block.type == RuleBlockType.FILTER:
                bl = block.params.get("blacklist", [])
                wl = block.params.get("whitelist", [])
                if bl and wl:
                    overlap = set(bl) & set(wl)
                    if overlap:
                        conflicts.append(
                            f"Filter block '{block.id}': addresses {overlap} "
                            f"appear in both blacklist and whitelist"
                        )
                    else:
                        warnings.append(
                            f"Filter block '{block.id}': using both blacklist and whitelist. "
                            f"Whitelist takes priority."
                        )

        # batching 파라미터 검증
        for block in blocks:
            if block.type == RuleBlockType.BATCHING:
                interval = block.params.get("interval_ms", 100)
                max_batch = block.params.get("max_batch", 50)
                min_batch = block.params.get("min_batch", 1)
                if min_batch > max_batch:
                    conflicts.append(
                        f"Batching block '{block.id}': min_batch ({min_batch}) > max_batch ({max_batch})"
                    )
                if interval < 50:
                    warnings.append(
                        f"Batching block '{block.id}': interval {interval}ms is very low. "
                        f"May cause high load."
                    )

        valid = len(conflicts) == 0 and not cycle_detected
        return ValidateResponse(
            valid=valid,
            conflicts=conflicts,
            warnings=warnings,
            cycle_detected=cycle_detected,
        )

    def simulate(self, request: SimulateRequest) -> SimulationReport:
        """규칙 세트에 대해 샘플 TX를 시뮬레이션한다."""
        blocks = request.blocks
        num_txs = request.sample_txs

        # 먼저 검증
        validation = self.validate(blocks)
        if not validation.valid:
            return SimulationReport(
                results=[],
                total_txs=num_txs,
                processed=0,
                filtered=0,
                conflicts=validation.conflicts,
            )

        # 샘플 TX 생성
        sample_txs = []
        for i in range(num_txs):
            sample_txs.append({
                "tx_id": f"tx_{uuid.uuid4().hex[:8]}",
                "sender": f"sender_{uuid.uuid4().hex[:8]}",
                "amount": round(random.uniform(0.1, 1000.0), 2),
                "fee": round(random.uniform(0.001, 0.1), 4),
                "timestamp": int(time.time()) - random.randint(0, 60),
            })

        results = []
        filtered_count = 0
        batch_counter = 0

        # 규칙 적용 시뮬레이션
        for tx in sample_txs:
            outcome = "included"
            position = None
            batch_id = None
            reason = None

            # Filter 블록 적용
            for block in blocks:
                if block.type == RuleBlockType.FILTER:
                    bl = block.params.get("blacklist", [])
                    wl = block.params.get("whitelist", [])
                    max_size = block.params.get("max_size")
                    min_size = block.params.get("min_size")

                    if wl and tx["sender"] not in wl:
                        outcome = "filtered"
                        reason = "Not in whitelist"
                        break
                    if bl and tx["sender"] in bl:
                        outcome = "filtered"
                        reason = "In blacklist"
                        break
                    if max_size and tx["amount"] > max_size:
                        outcome = "filtered"
                        reason = f"Amount {tx['amount']} exceeds max_size {max_size}"
                        break
                    if min_size and tx["amount"] < min_size:
                        outcome = "filtered"
                        reason = f"Amount {tx['amount']} below min_size {min_size}"
                        break

            if outcome == "filtered":
                filtered_count += 1
            else:
                # Ordering 블록 적용
                ordering_blocks = [b for b in blocks if b.type == RuleBlockType.ORDERING]
                if ordering_blocks:
                    method = ordering_blocks[0].params.get("method", "FIFO")
                    if method == "FIFO":
                        position = sample_txs.index(tx) + 1
                    elif method == "price_time":
                        # fee 기준 정렬 (높은 fee = 높은 우선순위)
                        sorted_txs = sorted(
                            [t for t in sample_txs],
                            key=lambda t: t["fee"],
                            reverse=True
                        )
                        position = sorted_txs.index(tx) + 1
                    elif method == "pro_rata":
                        position = sample_txs.index(tx) + 1

                # Batching 블록 적용
                batching_blocks = [b for b in blocks if b.type == RuleBlockType.BATCHING]
                if batching_blocks:
                    max_batch = batching_blocks[0].params.get("max_batch", 50)
                    current_pos = position or (sample_txs.index(tx) + 1)
                    batch_id = (current_pos - 1) // max_batch + 1
                    outcome = "batched"

            results.append(SimulationTxResult(
                tx_id=tx["tx_id"],
                outcome=outcome,
                position=position,
                batch_id=batch_id,
                reason=reason,
            ))

        return SimulationReport(
            results=results,
            total_txs=num_txs,
            processed=num_txs - filtered_count,
            filtered=filtered_count,
            conflicts=[],
        )

    def save_rule_set(self, rule_set: RuleSetCreate, owner: str) -> dict:
        """규칙 세트를 저장한다 (인메모리)."""
        rule_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        data = {
            "id": rule_id,
            "name": rule_set.name,
            "description": rule_set.description,
            "blocks": [b.model_dump() for b in rule_set.blocks],
            "owner": owner,
            "created_at": now,
            "updated_at": now,
        }
        self.saved_rules[rule_id] = data
        return data

    def get_rule_set(self, rule_id: str) -> Optional[dict]:
        """규칙 세트를 조회한다."""
        return self.saved_rules.get(rule_id)

    def get_user_rules(self, owner: str) -> list[dict]:
        """사용자의 규칙 목록을 조회한다."""
        return [r for r in self.saved_rules.values() if r["owner"] == owner]

    def export_rules(self, blocks: list[RuleBlock], fmt: str = "json") -> dict:
        """규칙을 JSON 또는 Anchor 코드로 내보낸다."""
        if fmt == "json":
            return {
                "format": "json",
                "data": {
                    "version": "1.0",
                    "ace_rules": [b.model_dump() for b in blocks],
                    "metadata": {
                        "generated_by": "kazt",
                        "generated_at": int(time.time()),
                        "block_count": len(blocks),
                    }
                }
            }
        elif fmt == "anchor":
            # Anchor IDL 스타일 코드 생성
            anchor_code = self._generate_anchor_code(blocks)
            return {
                "format": "anchor",
                "data": anchor_code,
            }
        return {"format": fmt, "data": None}

    def _generate_anchor_code(self, blocks: list[RuleBlock]) -> str:
        """Anchor 프로그램 코드 스니펫 생성"""
        lines = [
            "use anchor_lang::prelude::*;",
            "",
            "#[program]",
            "pub mod ace_rules {",
            "    use super::*;",
            "",
            "    pub fn initialize_rules(ctx: Context<InitializeRules>, rules_data: Vec<u8>) -> Result<()> {",
            "        let rules_account = &mut ctx.accounts.rules_account;",
            "        rules_account.authority = ctx.accounts.authority.key();",
            "        rules_account.rules_data = rules_data;",
            "        rules_account.block_count = {} as u32;".format(len(blocks)),
            "        rules_account.created_at = Clock::get()?.unix_timestamp;",
            "        Ok(())",
            "    }",
        ]

        # 각 블록 타입별 instruction 생성
        for block in blocks:
            if block.type == RuleBlockType.ORDERING:
                method = block.params.get("method", "FIFO")
                lines.extend([
                    "",
                    f"    // Ordering: {method}",
                    f"    pub fn set_ordering(ctx: Context<UpdateRules>, method: OrderingMethod) -> Result<()> {{",
                    f"        let rules = &mut ctx.accounts.rules_account;",
                    f"        rules.ordering_method = method;",
                    f"        Ok(())",
                    f"    }}",
                ])
            elif block.type == RuleBlockType.BATCHING:
                lines.extend([
                    "",
                    f"    // Batching: interval={block.params.get('interval_ms', 100)}ms",
                    f"    pub fn set_batching(ctx: Context<UpdateRules>, interval_ms: u32, max_batch: u32) -> Result<()> {{",
                    f"        let rules = &mut ctx.accounts.rules_account;",
                    f"        rules.batch_interval = interval_ms;",
                    f"        rules.max_batch_size = max_batch;",
                    f"        Ok(())",
                    f"    }}",
                ])
            elif block.type == RuleBlockType.FILTER:
                lines.extend([
                    "",
                    f"    // Filter block",
                    f"    pub fn set_filter(ctx: Context<UpdateRules>, blacklist: Vec<Pubkey>, whitelist: Vec<Pubkey>) -> Result<()> {{",
                    f"        let rules = &mut ctx.accounts.rules_account;",
                    f"        rules.blacklist = blacklist;",
                    f"        rules.whitelist = whitelist;",
                    f"        Ok(())",
                    f"    }}",
                ])

        lines.extend([
            "}",
            "",
            "#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq)]",
            "pub enum OrderingMethod {",
            "    Fifo,",
            "    PriceTime,",
            "    ProRata,",
            "}",
            "",
            "#[account]",
            "pub struct RulesAccount {",
            "    pub authority: Pubkey,",
            "    pub rules_data: Vec<u8>,",
            "    pub block_count: u32,",
            "    pub ordering_method: OrderingMethod,",
            "    pub batch_interval: u32,",
            "    pub max_batch_size: u32,",
            "    pub blacklist: Vec<Pubkey>,",
            "    pub whitelist: Vec<Pubkey>,",
            "    pub created_at: i64,",
            "}",
        ])
        return "\n".join(lines)


# Singleton
rule_service = RuleService()
