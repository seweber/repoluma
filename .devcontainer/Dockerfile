# syntax=docker/dockerfile:1
ARG UV_VERSION=0.12.17
ARG JUST_VERSION=1.58.0

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM mcr.microsoft.com/devcontainers/base:noble

ARG JUST_VERSION

USER root

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        cmake \
        ninja-build \
        ripgrep \
        shellcheck \
        gh \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

# just
RUN curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh \
    | bash -s -- --to /usr/local/bin --tag "${JUST_VERSION}"

# Persistent state mount point.
# This makes the target path exist with the expected owner before the named
# volume is mounted by the devcontainer runtime.
RUN mkdir -p /home/vscode/.persist \
    && chown -R vscode:vscode /home/vscode/.persist

USER vscode

ENV PATH="/home/vscode/.venv/bin:/home/vscode/.local/bin:${PATH}"

# Claude Code
RUN curl -fsSL https://claude.ai/install.sh | bash

# Herdr
RUN curl -fsSL https://herdr.dev/install.sh | sh

# Shell conveniences and persistent history behavior.
RUN { \
    echo ''; \
    echo '# Dev aliases'; \
    echo "alias claude-yolo='claude --dangerously-skip-permissions'"; \
    echo "alias dev='herdr --session dev'"; \
    echo ''; \
    echo '# Persistent devcontainer history. HISTFILE comes from containerEnv and'; \
    echo '# histappend is enabled by the stock .bashrc; raise its small size limits.'; \
    echo 'HISTSIZE=100000'; \
    echo 'HISTFILESIZE=100000'; \
    echo ''; \
    echo 'case ";${PROMPT_COMMAND:-};" in'; \
    echo '  *";history -a;"*) ;;'; \
    echo '  *) export PROMPT_COMMAND="history -a${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;'; \
    echo 'esac'; \
} >> /home/vscode/.bashrc