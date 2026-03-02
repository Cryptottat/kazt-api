"""
AI-powered code validation service.
Uses Claude API to analyze Anchor code for build errors, security issues, and test coverage.
"""

import os
import json
import re
from typing import Optional

from src.utils.logger import logger


VALIDATE_PROMPT = """You are a Solana/Anchor code reviewer. Analyze the provided Anchor program files thoroughly.

Check for:
1. **Build errors**: syntax errors, missing imports, type mismatches, incorrect Anchor attributes
2. **Security issues**: missing account validation, missing signer checks, integer overflow, PDA seed collisions, reentrancy
3. **Test coverage**: evaluate if the test file covers key paths (happy path, error cases, edge cases)

Respond ONLY with valid JSON in this exact format:
{
  "build": {
    "status": "pass" or "fail",
    "errors": ["error description 1", ...],
    "warnings": ["warning description 1", ...]
  },
  "tests": [
    {"name": "test name", "status": "pass" or "fail", "message": "explanation"}
  ],
  "security": [
    {"severity": "high" or "medium" or "low", "message": "description", "line": 0}
  ],
  "summary": "1-2 sentence overall assessment"
}

Be specific and actionable. If the code looks correct, still provide the test analysis.
Line numbers should reference the lib.rs file when possible (0 if unknown)."""


async def validate_code(files: list[dict]) -> dict:
    """
    Validate Anchor code using Claude API.
    Falls back to static analysis if API is unavailable.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if api_key:
        try:
            return await _ai_validate(files, api_key)
        except Exception as e:
            logger.error(f"AI validation failed: {e}", exc_info=True)

    return _static_validate(files)


async def _ai_validate(files: list[dict], api_key: str) -> dict:
    """Validate using Claude API."""
    import httpx

    # Build file content for the prompt
    file_contents = []
    for f in files:
        file_contents.append(f"--- {f['path']} ({f.get('language', 'unknown')}) ---\n{f['content']}")

    user_message = "Analyze these Anchor program files:\n\n" + "\n\n".join(file_contents)

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 4000,
                "system": VALIDATE_PROMPT,
                "messages": [
                    {"role": "user", "content": user_message}
                ],
            },
        )
        response.raise_for_status()

        data = response.json()
        content = data["content"][0]["text"]

        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            raise ValueError("No valid JSON in AI validation response")

        result = json.loads(json_match.group())

        # Ensure required fields exist
        if "build" not in result:
            result["build"] = {"status": "pass", "errors": [], "warnings": []}
        if "tests" not in result:
            result["tests"] = []
        if "security" not in result:
            result["security"] = []
        if "summary" not in result:
            result["summary"] = "Validation completed."

        logger.info(
            f"AI validation: build={result['build']['status']}, "
            f"tests={len(result['tests'])}, security={len(result['security'])}"
        )
        return result


def _static_validate(files: list[dict]) -> dict:
    """
    Basic static analysis fallback when Claude API is unavailable.
    Checks for common Anchor patterns.
    """
    lib_rs = next((f for f in files if f["path"].endswith("lib.rs")), None)
    test_file = next((f for f in files if f.get("language") == "typescript"), None)

    build_errors: list[str] = []
    build_warnings: list[str] = []
    security: list[dict] = []
    tests: list[dict] = []

    if not lib_rs:
        build_errors.append("Missing lib.rs - no main program file found")
    else:
        code = lib_rs["content"]

        # Check for declare_id!
        if "declare_id!" not in code:
            build_errors.append("Missing declare_id! macro")

        # Check for #[program] module
        if "#[program]" not in code:
            build_errors.append("Missing #[program] attribute on module")

        # Check for use anchor_lang::prelude::*
        if "anchor_lang::prelude" not in code:
            build_warnings.append("Missing 'use anchor_lang::prelude::*' import")

        # Security: check for Signer in account structs
        if "Signer<'info>" not in code:
            security.append({
                "severity": "high",
                "message": "No Signer account found - instructions may lack authorization checks",
                "line": 0,
            })

        # Security: check for has_one or constraint on mut accounts
        if "#[account(mut" in code and "has_one" not in code:
            security.append({
                "severity": "medium",
                "message": "Mutable accounts without has_one constraint - verify authorization",
                "line": 0,
            })

        # Check for error handling
        if "#[error_code]" not in code:
            build_warnings.append("No custom error enum defined - consider adding #[error_code]")

        build_status = "fail" if build_errors else "pass"

    if test_file:
        code = test_file["content"]
        # Count test cases
        it_matches = re.findall(r'it\(["\'](.+?)["\']', code)
        for test_name in it_matches:
            tests.append({
                "name": test_name,
                "status": "pass",
                "message": "Test case detected (static analysis - run anchor test to verify)",
            })

        if not it_matches:
            tests.append({
                "name": "test coverage",
                "status": "fail",
                "message": "No test cases found in test file",
            })
    else:
        tests.append({
            "name": "test file",
            "status": "fail",
            "message": "No test file found in project",
        })

    return {
        "build": {
            "status": "fail" if build_errors else "pass",
            "errors": build_errors,
            "warnings": build_warnings,
        },
        "tests": tests,
        "security": security,
        "summary": (
            f"Static analysis: {len(build_errors)} errors, {len(build_warnings)} warnings, "
            f"{len(security)} security issues, {len(tests)} tests detected. "
            "Note: AI validation unavailable, results are based on pattern matching only."
        ),
    }
