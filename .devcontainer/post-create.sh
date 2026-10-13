#!/usr/bin/env bash
set -euo pipefail

# These paths must match containerEnv in devcontainer.json.
PERSIST_DIR="/home/vscode/.persist"
CLAUDE_DIR="$PERSIST_DIR/claude"
HERDR_DIR="$PERSIST_DIR/herdr"
UV_CACHE="$PERSIST_DIR/uv-cache"
BASH_HISTORY="$PERSIST_DIR/bash_history"

CLAUDE_HOME_LINK="/home/vscode/.claude"
HERDR_HOME_LINK="/home/vscode/.config/herdr"

mkdir -p \
  "$PERSIST_DIR" \
  "$CLAUDE_DIR" \
  "$HERDR_DIR" \
  "$UV_CACHE" \
  /home/vscode/.config

touch "$BASH_HISTORY"

# Migrate any image-created or previous non-symlink Claude directory.
if [[ -d "$CLAUDE_HOME_LINK" && ! -L "$CLAUDE_HOME_LINK" ]]; then
  cp -a --update=none "$CLAUDE_HOME_LINK"/. "$CLAUDE_DIR"/
fi

rm -rf "$CLAUDE_HOME_LINK"
ln -sfn "$CLAUDE_DIR" "$CLAUDE_HOME_LINK"

# Migrate Herdr's XDG config directory.
if [[ -d "$HERDR_HOME_LINK" && ! -L "$HERDR_HOME_LINK" ]]; then
  cp -a --update=none "$HERDR_HOME_LINK"/. "$HERDR_DIR"/
fi

rm -rf "$HERDR_HOME_LINK"
ln -sfn "$HERDR_DIR" "$HERDR_HOME_LINK"

# Clean up old/legacy Claude JSON layouts.
if [[ -f /home/vscode/.claude.json && ! -L /home/vscode/.claude.json && ! -e "$CLAUDE_DIR/.claude.json" ]]; then
  mv /home/vscode/.claude.json "$CLAUDE_DIR/.claude.json"
fi

rm -f /home/vscode/.claude.json

if [[ -f "$PERSIST_DIR/claude.json" ]]; then
  if [[ ! -e "$CLAUDE_DIR/.claude.json" ]]; then
    mv "$PERSIST_DIR/claude.json" "$CLAUDE_DIR/.claude.json"
  else
    rm -f "$PERSIST_DIR/claude.json"
  fi
fi

mkdir -p "$CLAUDE_DIR/skills/herdr"

herdr integration install claude
herdr --skill > "$CLAUDE_DIR/skills/herdr/SKILL.md"

# Chromium for the browser tests and bench/, matching the locked playwright version.
# Cached in the persistent volume (PLAYWRIGHT_BROWSERS_PATH), so this is a no-op after the
# first run unless the pin changes. Failure is not fatal: those tests then skip.
uv run --frozen playwright install chromium \
  || echo "warning: Chromium download failed; browser tests will be skipped" >&2

uv sync --locked