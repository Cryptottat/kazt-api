"""
Real Anchor Build Service.
서버에서 실제 `anchor build` + `anchor test`를 실행하고 결과를 SSE로 스트리밍.
asyncio.Semaphore 기반 빌드 큐로 동시 빌드 수 제한.
"""

import asyncio
import os
import re
import shutil
import tempfile
import time
from typing import AsyncGenerator

from src.utils.logger import logger

# 동시 빌드 제한 (Railway 8GB 기준 2~3개)
MAX_CONCURRENT_BUILDS = 2
BUILD_TIMEOUT = 120  # seconds

_build_semaphore = asyncio.Semaphore(MAX_CONCURRENT_BUILDS)
_queue_waiters = 0
_queue_lock = asyncio.Lock()


def _sanitize_name(name: str) -> str:
    """프로그램 이름을 Anchor 프로젝트명 규칙에 맞게 변환."""
    name = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "kazt_program"


def _snake_to_pascal(name: str) -> str:
    """snake_case → PascalCase"""
    return "".join(word.capitalize() for word in name.split("_"))


def _prepare_anchor_project(work_dir: str, files: list[dict], program_name: str) -> str:
    """
    파일 목록으로 Anchor 프로젝트 구조를 만든다.
    이미 Anchor 프로젝트 구조이면 그대로, 아니면 자동 생성.
    반환: 프로젝트 루트 디렉토리 경로
    """
    safe_name = _sanitize_name(program_name)
    project_dir = os.path.join(work_dir, safe_name)
    os.makedirs(project_dir, exist_ok=True)

    # 파일에서 lib.rs 찾기
    lib_rs = None
    test_file = None
    anchor_toml = None
    cargo_toml = None

    for f in files:
        p = f["path"]
        if p.endswith("lib.rs"):
            lib_rs = f
        elif f.get("language") == "typescript" or p.endswith(".ts"):
            test_file = f
        elif p.endswith("Anchor.toml"):
            anchor_toml = f
        elif p.endswith("Cargo.toml") and "programs" in p:
            cargo_toml = f

    if not lib_rs:
        raise ValueError("No lib.rs found in project files")

    # programs/<name>/src/lib.rs
    src_dir = os.path.join(project_dir, "programs", safe_name, "src")
    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "lib.rs"), "w") as fp:
        fp.write(lib_rs["content"])

    # programs/<name>/Cargo.toml
    cargo_dir = os.path.join(project_dir, "programs", safe_name)
    if cargo_toml:
        with open(os.path.join(cargo_dir, "Cargo.toml"), "w") as fp:
            fp.write(cargo_toml["content"])
    else:
        with open(os.path.join(cargo_dir, "Cargo.toml"), "w") as fp:
            fp.write(f"""[package]
name = "{safe_name}"
version = "0.1.0"
description = "Created by Kazt Forge"
edition = "2021"

[lib]
crate-type = ["cdylib", "lib"]
name = "{safe_name}"

[features]
no-entrypoint = []
no-idl = []
no-log-ix-name = []
cpi = ["no-entrypoint"]
default = []

[dependencies]
anchor-lang = "0.30.1"
anchor-spl = "0.30.1"
""")

    # Anchor.toml
    if anchor_toml:
        with open(os.path.join(project_dir, "Anchor.toml"), "w") as fp:
            fp.write(anchor_toml["content"])
    else:
        pascal_name = _snake_to_pascal(safe_name)
        with open(os.path.join(project_dir, "Anchor.toml"), "w") as fp:
            fp.write(f"""[features]
seeds = false
skip-lint = false

[programs.localnet]
{safe_name} = "Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS"

[registry]
url = "https://api.apr.dev"

[provider]
cluster = "Localnet"
wallet = "./id.json"

[scripts]
test = "yarn run ts-mocha -p ./tsconfig.json -t 1000000 tests/**/*.ts"
""")

    # tests/<name>.ts
    tests_dir = os.path.join(project_dir, "tests")
    os.makedirs(tests_dir, exist_ok=True)
    if test_file:
        with open(os.path.join(tests_dir, f"{safe_name}.ts"), "w") as fp:
            fp.write(test_file["content"])

    # tsconfig.json
    with open(os.path.join(project_dir, "tsconfig.json"), "w") as fp:
        fp.write("""{
  "compilerOptions": {
    "types": ["mocha", "chai"],
    "typeRoots": ["./node_modules/@types"],
    "lib": ["es2015"],
    "module": "commonjs",
    "target": "es6",
    "esModuleInterop": true
  }
}
""")

    # package.json (minimal)
    with open(os.path.join(project_dir, "package.json"), "w") as fp:
        fp.write(f"""{{"name": "{safe_name}","version": "0.1.0","dependencies": {{"@coral-xyz/anchor": "^0.30.1"}},"devDependencies": {{"@types/mocha": "^10.0.0","@types/chai": "^4.3.0","chai": "^4.3.0","mocha": "^10.0.0","ts-mocha": "^10.0.0","typescript": "^5.0.0"}}}}
""")

    # Dummy wallet for anchor build (doesn't need real keys)
    with open(os.path.join(project_dir, "id.json"), "w") as fp:
        fp.write("[1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1]")

    return project_dir


async def _run_command(cmd: str, cwd: str, timeout: float = BUILD_TIMEOUT) -> tuple[int, str, str]:
    """Run a shell command asynchronously with timeout."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CARGO_TERM_COLOR": "never"},
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", f"Build timed out after {timeout}s"


def _parse_build_errors(stderr: str) -> list[str]:
    """Rust/Anchor 빌드 stderr에서 에러 메시지를 파싱."""
    errors = []
    for line in stderr.split("\n"):
        line = line.strip()
        if line.startswith("error"):
            errors.append(line)
        elif "cannot find" in line or "not found" in line or "mismatched types" in line:
            errors.append(line)
    # 에러가 너무 길면 잘라내기
    return errors[:30] if errors else (["Build failed. See full output for details."] if stderr.strip() else [])


def _parse_build_warnings(stderr: str) -> list[str]:
    """Rust/Anchor 빌드 stderr에서 경고 메시지를 파싱."""
    warnings = []
    for line in stderr.split("\n"):
        line = line.strip()
        if line.startswith("warning"):
            warnings.append(line)
    return warnings[:20]


def _parse_test_results(stdout: str, stderr: str) -> list[dict]:
    """mocha/ts-mocha 테스트 결과 파싱."""
    tests = []
    combined = stdout + "\n" + stderr

    # Match passing tests: ✓ or √ or "passing"
    for m in re.finditer(r"[✓√]\s+(.+?)(?:\s+\(\d+ms\))?$", combined, re.MULTILINE):
        tests.append({"name": m.group(1).strip(), "status": "pass", "message": ""})

    # Match failing tests
    for m in re.finditer(r"\d+\)\s+(.+?)$", combined, re.MULTILINE):
        name = m.group(1).strip()
        if not any(t["name"] == name for t in tests):
            tests.append({"name": name, "status": "fail", "message": "Test failed"})

    # If no test results parsed, check for error patterns
    if not tests:
        if "passing" in combined:
            count_match = re.search(r"(\d+)\s+passing", combined)
            if count_match:
                n = int(count_match.group(1))
                for i in range(n):
                    tests.append({"name": f"test_{i+1}", "status": "pass", "message": "Passed"})
        fail_match = re.search(r"(\d+)\s+failing", combined)
        if fail_match:
            n = int(fail_match.group(1))
            for i in range(n):
                tests.append({"name": f"failed_test_{i+1}", "status": "fail", "message": "Failed"})

    return tests


async def build_stream(
    files: list[dict], program_name: str = "kazt_program"
) -> AsyncGenerator[dict, None]:
    """
    Real anchor build + test as async generator.
    Yields SSE events for real-time progress including queue position.
    """
    global _queue_waiters

    safe_name = _sanitize_name(program_name)
    work_dir = None

    # Queue management
    async with _queue_lock:
        _queue_waiters += 1
        position = _queue_waiters

    if position > MAX_CONCURRENT_BUILDS:
        yield {
            "type": "queued",
            "position": position - MAX_CONCURRENT_BUILDS,
            "message": f"Build queued. Position: {position - MAX_CONCURRENT_BUILDS}",
        }

    try:
        # Wait for semaphore (queue)
        await _build_semaphore.acquire()

        async with _queue_lock:
            _queue_waiters -= 1

        yield {
            "type": "building",
            "message": "Build started. Preparing project...",
        }

        # Create temp directory
        work_dir = tempfile.mkdtemp(prefix="kazt_build_")

        try:
            project_dir = _prepare_anchor_project(work_dir, files, safe_name)
        except ValueError as e:
            yield {"type": "error", "message": str(e)}
            return

        yield {
            "type": "build_output",
            "message": f"Project structure ready: {safe_name}",
        }

        # ---- anchor build ----
        yield {
            "type": "build_output",
            "message": "Running anchor build...",
        }

        start_time = time.time()
        returncode, stdout, stderr = await _run_command("anchor build", project_dir)
        build_time = round(time.time() - start_time, 1)

        # Stream build output lines
        build_output_lines = []
        for line in (stderr + "\n" + stdout).split("\n"):
            line = line.strip()
            if line and not line.startswith("Downloaded") and not line.startswith("Compiling"):
                build_output_lines.append(line)

        build_errors = _parse_build_errors(stderr) if returncode != 0 else []
        build_warnings = _parse_build_warnings(stderr)

        build_passed = returncode == 0

        yield {
            "type": "build_result",
            "message": f"Build {'PASS' if build_passed else 'FAIL'} ({build_time}s)",
            "build": {
                "status": "pass" if build_passed else "fail",
                "errors": build_errors,
                "warnings": build_warnings,
                "output": build_output_lines[-50:],  # last 50 lines
            },
        }

        # ---- anchor test (only if build passed) ----
        tests = []
        if build_passed:
            yield {
                "type": "build_output",
                "message": "Running tests...",
            }

            # Install test dependencies
            yield {
                "type": "build_output",
                "message": "Installing test dependencies...",
            }
            await _run_command("yarn install --frozen-lockfile 2>/dev/null || npm install 2>/dev/null || true", project_dir, timeout=60)

            test_returncode, test_stdout, test_stderr = await _run_command(
                "anchor test --skip-build --skip-local-validator 2>&1 || true",
                project_dir,
                timeout=60,
            )

            tests = _parse_test_results(test_stdout, test_stderr)

            if tests:
                pass_count = sum(1 for t in tests if t["status"] == "pass")
                yield {
                    "type": "build_output",
                    "message": f"Tests: {pass_count}/{len(tests)} passed",
                }
        else:
            tests = [{"name": "build", "status": "fail", "message": "Skipped: build failed"}]

        # ---- Security scan (basic static check on lib.rs) ----
        security = _static_security_check(files)

        # ---- Final result ----
        result = {
            "build": {
                "status": "pass" if build_passed else "fail",
                "errors": build_errors,
                "warnings": build_warnings,
            },
            "tests": tests,
            "security": security,
            "summary": (
                f"Real build {'PASS' if build_passed else 'FAIL'} in {build_time}s. "
                f"{len(tests)} tests, {len(security)} security issues."
            ),
        }

        yield {
            "type": "complete",
            "message": "Build complete.",
            "result": result,
        }

    except Exception as e:
        logger.error(f"Build failed: {e}", exc_info=True)
        yield {"type": "error", "message": f"Build error: {str(e)}"}
    finally:
        _build_semaphore.release()

        # Cleanup temp directory
        if work_dir and os.path.exists(work_dir):
            try:
                shutil.rmtree(work_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup {work_dir}: {e}")


def _static_security_check(files: list[dict]) -> list[dict]:
    """lib.rs에 대한 기본 보안 체크 (정적 분석)."""
    issues = []
    lib_rs = next((f for f in files if f["path"].endswith("lib.rs")), None)
    if not lib_rs:
        return issues

    code = lib_rs["content"]

    if "Signer<'info>" not in code and "Signer<" not in code:
        issues.append({
            "severity": "high",
            "message": "No Signer account found - instructions may lack authorization checks",
            "line": 0,
        })

    if "#[account(mut" in code and "has_one" not in code and "constraint" not in code:
        issues.append({
            "severity": "medium",
            "message": "Mutable accounts without has_one/constraint - verify authorization",
            "line": 0,
        })

    if "unchecked_account" in code.lower():
        issues.append({
            "severity": "high",
            "message": "UncheckedAccount usage detected - ensure manual validation",
            "line": 0,
        })

    if "as u64" in code or "as i64" in code:
        issues.append({
            "severity": "medium",
            "message": "Numeric cast detected - verify no overflow/underflow risk",
            "line": 0,
        })

    return issues


async def build_and_validate(files: list[dict], program_name: str = "kazt_program") -> dict:
    """
    Non-streaming version: runs build + returns final ValidateResult.
    Used by autofix loop.
    """
    result = None
    async for event in build_stream(files, program_name):
        if event["type"] == "complete":
            result = event.get("result")
        elif event["type"] == "error":
            return {
                "build": {"status": "fail", "errors": [event.get("message", "Build error")], "warnings": []},
                "tests": [],
                "security": [],
                "summary": event.get("message", "Build failed"),
            }
    return result or {
        "build": {"status": "fail", "errors": ["No build result"], "warnings": []},
        "tests": [],
        "security": [],
        "summary": "Build produced no result",
    }
