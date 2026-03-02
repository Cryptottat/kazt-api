FROM python:3.12-slim

# System deps for Rust + Solana + Anchor
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential pkg-config libssl-dev libudev-dev clang cmake \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.79.0

# Install Solana CLI
ENV PATH="/root/.local/share/solana/install/active_release/bin:$PATH"
RUN sh -c "$(curl -sSfL https://release.anza.xyz/v1.18.26/install)" && \
    solana --version

# Install Anchor CLI via cargo (avm)
RUN cargo install --git https://github.com/coral-xyz/anchor avm --force && \
    avm install 0.30.1 && \
    avm use 0.30.1 && \
    anchor --version

# Warmup: create a throwaway Anchor project to cache Rust/BPF deps
RUN mkdir /tmp/warmup && cd /tmp/warmup && \
    anchor init warmup_project --no-git && \
    cd warmup_project && \
    anchor build || true && \
    rm -rf /tmp/warmup

# Python app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}
