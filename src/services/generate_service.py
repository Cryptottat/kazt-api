"""
AI-powered Solana program generator.
Generates Anchor code from natural language descriptions.
Uses Claude API when available, falls back to template-based generation.
"""

import re
import os
import json
from typing import Optional

from src.utils.logger import logger
from src.models.generate import GeneratedFile


# System prompt for AI code generation
SYSTEM_PROMPT = """You are Kazt Forge, an AI that generates Solana programs using the Anchor framework.

Given a natural language description of a Solana program, generate:
1. The main program file (lib.rs) with Anchor code
2. A test file (TypeScript) using @coral-xyz/anchor
3. Anchor.toml configuration
4. Cargo.toml workspace configuration

Rules:
- Use Anchor v0.30+ syntax (anchor_lang::prelude::*)
- Use proper account validation and constraints
- Include proper error handling with custom error enums
- Generate comprehensive tests
- Use declare_id! with placeholder "11111111111111111111111111111111"
- Follow Rust best practices
- Include comments explaining the logic

Respond ONLY with valid JSON in this exact format:
{
  "name": "program_name_snake_case",
  "files": [
    {"path": "programs/name/src/lib.rs", "content": "...", "language": "rust"},
    {"path": "tests/name.ts", "content": "...", "language": "typescript"},
    {"path": "Anchor.toml", "content": "...", "language": "toml"},
    {"path": "Cargo.toml", "content": "...", "language": "toml"}
  ],
  "instructions": ["step 1", "step 2", ...],
  "test_count": 3
}"""


def _sanitize_name(description: str) -> str:
    """Extract a clean program name from description."""
    words = re.sub(r"[^a-zA-Z0-9\s]", "", description).split()[:3]
    name = "_".join(w.lower() for w in words)
    return name or "my_program"


def _generate_template(description: str) -> dict:
    """Template-based fallback generation when AI is unavailable."""
    name = _sanitize_name(description)

    lib_rs = f'''use anchor_lang::prelude::*;

declare_id!("11111111111111111111111111111111");

#[program]
pub mod {name} {{
    use super::*;

    /// Initialize the program state
    pub fn initialize(ctx: Context<Initialize>, config_value: u64) -> Result<()> {{
        let state = &mut ctx.accounts.state;
        state.authority = ctx.accounts.authority.key();
        state.config_value = config_value;
        state.is_initialized = true;
        state.created_at = Clock::get()?.unix_timestamp;
        msg!("{name} initialized with config_value: {{}}", config_value);
        Ok(())
    }}

    /// Update the configuration
    pub fn update_config(ctx: Context<UpdateConfig>, new_value: u64) -> Result<()> {{
        let state = &mut ctx.accounts.state;
        require!(state.is_initialized, {name.title().replace("_", "")}Error::NotInitialized);
        state.config_value = new_value;
        msg!("Config updated to: {{}}", new_value);
        Ok(())
    }}

    /// Execute the main program logic
    pub fn execute(ctx: Context<Execute>, amount: u64) -> Result<()> {{
        let state = &ctx.accounts.state;
        require!(state.is_initialized, {name.title().replace("_", "")}Error::NotInitialized);
        require!(amount > 0, {name.title().replace("_", "")}Error::InvalidAmount);

        // Program logic based on: {description}
        msg!("Executing with amount: {{}}", amount);
        Ok(())
    }}
}}

#[derive(Accounts)]
pub struct Initialize<'info> {{
    #[account(init, payer = authority, space = 8 + State::INIT_SPACE)]
    pub state: Account<'info, State>,
    #[account(mut)]
    pub authority: Signer<'info>,
    pub system_program: Program<'info, System>,
}}

#[derive(Accounts)]
pub struct UpdateConfig<'info> {{
    #[account(mut, has_one = authority)]
    pub state: Account<'info, State>,
    pub authority: Signer<'info>,
}}

#[derive(Accounts)]
pub struct Execute<'info> {{
    #[account(has_one = authority)]
    pub state: Account<'info, State>,
    pub authority: Signer<'info>,
}}

#[account]
#[derive(InitSpace)]
pub struct State {{
    pub authority: Pubkey,
    pub config_value: u64,
    pub is_initialized: bool,
    pub created_at: i64,
}}

#[error_code]
pub enum {name.title().replace("_", "")}Error {{
    #[msg("Program is not initialized")]
    NotInitialized,
    #[msg("Invalid amount")]
    InvalidAmount,
}}'''

    class_name = name.title().replace("_", "")

    test_ts = f'''import * as anchor from "@coral-xyz/anchor";
import {{ Program }} from "@coral-xyz/anchor";
import {{ {class_name} }} from "../target/types/{name}";
import {{ expect }} from "chai";

describe("{name}", () => {{
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);
  const program = anchor.workspace.{class_name} as Program<typeof {class_name}>;

  const state = anchor.web3.Keypair.generate();

  it("initializes the program", async () => {{
    await program.methods
      .initialize(new anchor.BN(100))
      .accounts({{ state: state.publicKey }})
      .signers([state])
      .rpc();

    const account = await program.account.state.fetch(state.publicKey);
    expect(account.isInitialized).to.be.true;
    expect(account.configValue.toNumber()).to.equal(100);
  }});

  it("updates config", async () => {{
    await program.methods
      .updateConfig(new anchor.BN(200))
      .accounts({{ state: state.publicKey }})
      .rpc();

    const account = await program.account.state.fetch(state.publicKey);
    expect(account.configValue.toNumber()).to.equal(200);
  }});

  it("executes program logic", async () => {{
    await program.methods
      .execute(new anchor.BN(50))
      .accounts({{ state: state.publicKey }})
      .rpc();
  }});

  it("rejects zero amount", async () => {{
    try {{
      await program.methods
        .execute(new anchor.BN(0))
        .accounts({{ state: state.publicKey }})
        .rpc();
      expect.fail("Should have thrown");
    }} catch (err) {{
      expect(err.toString()).to.include("InvalidAmount");
    }}
  }});
}});'''

    anchor_toml = f'''[features]
seeds = false
skip-lint = false

[programs.localnet]
{name} = "11111111111111111111111111111111"

[registry]
url = "https://api.apr.dev"

[provider]
cluster = "Localnet"
wallet = "~/.config/solana/id.json"

[scripts]
test = "yarn run ts-mocha -p ./tsconfig.json -t 1000000 tests/**/*.ts"'''

    cargo_toml = f'''[workspace]
members = ["programs/{name}"]
resolver = "2"

[profile.release]
overflow-checks = true
lto = "fat"
codegen-units = 1

[profile.release.build-override]
opt-level = 3
incremental = false
codegen-units = 1'''

    return {
        "name": name,
        "description": description,
        "files": [
            {"path": f"programs/{name}/src/lib.rs", "content": lib_rs, "language": "rust"},
            {"path": f"tests/{name}.ts", "content": test_ts, "language": "typescript"},
            {"path": "Anchor.toml", "content": anchor_toml, "language": "toml"},
            {"path": "Cargo.toml", "content": cargo_toml, "language": "toml"},
        ],
        "instructions": [
            f"Created program: {name}",
            "Generated lib.rs with 3 instructions: initialize, update_config, execute",
            "Generated 4 test cases",
            "Anchor.toml and Cargo.toml configured for localnet",
            "Run 'anchor build' to compile",
            "Run 'anchor test' to run test suite",
        ],
        "test_count": 4,
    }


async def generate_program(description: str) -> dict:
    """
    Generate a Solana program from natural language description.
    Tries AI generation first, falls back to template.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if api_key:
        try:
            return await _ai_generate(description, api_key)
        except Exception as e:
            logger.warning(f"AI generation failed, using template fallback: {e}")

    return _generate_template(description)


async def _ai_generate(description: str, api_key: str) -> dict:
    """Generate using Claude API."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 8000,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": f"Generate a Solana program for: {description}",
                    }
                ],
            },
        )
        response.raise_for_status()

        data = response.json()
        content = data["content"][0]["text"]

        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            raise ValueError("No valid JSON in AI response")

        result = json.loads(json_match.group())
        result["description"] = description
        return result
