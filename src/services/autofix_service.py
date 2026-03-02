"""
Auto-fix service.
Build/Test 실패 시 에러를 분석하고 Claude에게 수정을 요청하여
자동으로 코드를 고치고 재검증하는 루프를 SSE로 스트리밍.
최대 MAX_ATTEMPTS회 반복.
"""

import json
from typing import AsyncGenerator

from src.utils.logger import logger
from src.services.generate_service import _extract_json
from src.services.validate_service import validate_code


MAX_ATTEMPTS = 5

AUTOFIX_PROMPT = """You are a Solana/Anchor code fixer. You are given:
1. The current source files of an Anchor program
2. Validation errors (build errors, test failures, security issues)

Your task: fix the code so that all validation checks pass.

Rules:
- Only modify files that need changes
- Preserve the program's intended logic
- Fix all build errors first, then test failures, then security issues
- Use Anchor v0.30+ syntax
- Include proper account validation and constraints

Respond ONLY with valid JSON in this exact format:
{
  "files": [
    {"path": "programs/name/src/lib.rs", "content": "full file content...", "language": "rust"},
    {"path": "tests/name.ts", "content": "full file content...", "language": "typescript"}
  ],
  "changes": ["description of change 1", "description of change 2"]
}

Return ALL files (modified and unmodified) with their full content.
The "changes" array should describe what you fixed."""


def _collect_errors(validate_result: dict) -> str:
    """검증 결과에서 에러/실패 정보를 텍스트로 수집."""
    parts = []

    build = validate_result.get("build", {})
    if build.get("status") == "fail":
        parts.append("BUILD ERRORS:")
        for e in build.get("errors", []):
            parts.append(f"  - {e}")

    if build.get("warnings"):
        parts.append("BUILD WARNINGS:")
        for w in build.get("warnings", []):
            parts.append(f"  - {w}")

    tests = validate_result.get("tests", [])
    failed_tests = [t for t in tests if t.get("status") == "fail"]
    if failed_tests:
        parts.append("TEST FAILURES:")
        for t in failed_tests:
            parts.append(f"  - {t.get('name', 'unknown')}: {t.get('message', '')}")

    security = validate_result.get("security", [])
    if security:
        parts.append("SECURITY ISSUES:")
        for s in security:
            sev = s.get("severity", "unknown")
            msg = s.get("message", "")
            line = s.get("line", 0)
            parts.append(f"  - [{sev.upper()}] {msg}" + (f" (line {line})" if line else ""))

    return "\n".join(parts) if parts else "No specific errors found."


def _count_issues(validate_result: dict) -> int:
    """검증 결과에서 총 이슈 수를 카운트."""
    count = 0
    build = validate_result.get("build", {})
    count += len(build.get("errors", []))
    tests = validate_result.get("tests", [])
    count += sum(1 for t in tests if t.get("status") == "fail")
    count += len(validate_result.get("security", []))
    return count


def _is_all_pass(validate_result: dict) -> bool:
    """모든 검증이 통과했는지 확인."""
    build = validate_result.get("build", {})
    if build.get("status") != "pass":
        return False
    tests = validate_result.get("tests", [])
    if any(t.get("status") == "fail" for t in tests):
        return False
    if validate_result.get("security"):
        return False
    return True


async def autofix_stream(
    files: list[dict], validate_result: dict
) -> AsyncGenerator[dict, None]:
    """
    Auto-fix loop as async generator.
    Yields SSE events for real-time progress.
    """
    current_files = list(files)
    current_result = validate_result
    best_files = list(files)
    best_issue_count = _count_issues(validate_result)

    yield {
        "type": "start",
        "message": f"Starting auto-fix. {best_issue_count} issues to resolve.",
        "maxAttempts": MAX_ATTEMPTS,
    }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        issue_count = _count_issues(current_result)
        error_text = _collect_errors(current_result)

        yield {
            "type": "fixing",
            "attempt": attempt,
            "maxAttempts": MAX_ATTEMPTS,
            "message": f"Analyzing {issue_count} issue{'s' if issue_count != 1 else ''}...",
        }

        # Build file content for Claude
        file_contents = []
        for f in current_files:
            file_contents.append(
                f"--- {f['path']} ({f.get('language', 'unknown')}) ---\n{f['content']}"
            )

        user_message = (
            f"Fix the following issues in this Anchor program:\n\n"
            f"{error_text}\n\n"
            f"Current files:\n\n" + "\n\n".join(file_contents)
        )

        try:
            from src.services.ai_client import chat
            content = await chat(system=AUTOFIX_PROMPT, user_message=user_message, max_tokens=32000)
            fix_result = _extract_json(content)

            fixed_files = fix_result.get("files", [])
            changes = fix_result.get("changes", [])

            if not fixed_files:
                yield {
                    "type": "error",
                    "attempt": attempt,
                    "message": "AI returned no files. Stopping.",
                }
                break

            # Normalize file fields
            for f in fixed_files:
                if "filename" in f and "path" not in f:
                    f["path"] = f.pop("filename")
                if "path" not in f:
                    f["path"] = "unknown"

            files_changed = [f["path"] for f in fixed_files]

            yield {
                "type": "fixed",
                "attempt": attempt,
                "maxAttempts": MAX_ATTEMPTS,
                "message": f"Fix applied: {len(changes)} change{'s' if len(changes) != 1 else ''}",
                "changes": changes,
                "filesChanged": files_changed,
            }

            current_files = fixed_files

            # Re-validate
            yield {
                "type": "validating",
                "attempt": attempt,
                "maxAttempts": MAX_ATTEMPTS,
                "message": "Re-validating...",
            }

            current_result = await validate_code(
                [{"path": f["path"], "content": f["content"], "language": f.get("language", "unknown")} for f in current_files]
            )

            new_issue_count = _count_issues(current_result)
            if new_issue_count < best_issue_count:
                best_files = list(current_files)
                best_issue_count = new_issue_count

            all_pass = _is_all_pass(current_result)

            yield {
                "type": "attempt_result",
                "attempt": attempt,
                "maxAttempts": MAX_ATTEMPTS,
                "message": f"Build {'PASS' if current_result.get('build', {}).get('status') == 'pass' else 'FAIL'}"
                           + (f" - {new_issue_count} issue{'s' if new_issue_count != 1 else ''} remaining" if not all_pass else ""),
                "result": current_result,
            }

            if all_pass:
                yield {
                    "type": "complete",
                    "attempt": attempt,
                    "maxAttempts": MAX_ATTEMPTS,
                    "message": f"All checks passed after {attempt} attempt{'s' if attempt != 1 else ''}!",
                    "files": current_files,
                    "result": current_result,
                }
                return

        except Exception as e:
            logger.error(f"Auto-fix attempt {attempt} failed: {e}", exc_info=True)
            yield {
                "type": "error",
                "attempt": attempt,
                "maxAttempts": MAX_ATTEMPTS,
                "message": f"Attempt {attempt} failed: {str(e)}",
            }
            # Continue to next attempt
            continue

    # max attempts reached
    yield {
        "type": "max_attempts",
        "attempt": MAX_ATTEMPTS,
        "maxAttempts": MAX_ATTEMPTS,
        "message": f"Could not fix all issues after {MAX_ATTEMPTS} attempts. Applied best result ({best_issue_count} issues remaining).",
        "files": best_files,
        "result": current_result,
    }
