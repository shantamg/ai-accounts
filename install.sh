#!/bin/sh
# Install or update ai-accounts.
#
#   ./install.sh            link ai-accounts into ~/bin (or $BIN_DIR)
#   ./install.sh --aliases  also add the `c` and `codex` aliases to your shell rc
#   ./install.sh update     pull the latest version, then re-link
#
# The command is a symlink to this checkout, so `git pull` (or `update`)
# upgrades it in place.
set -eu

REPO_DIR=$(cd "$(dirname "$0")" && pwd -P)
BIN_DIR=${BIN_DIR:-"$HOME/bin"}
TARGET="$BIN_DIR/ai-accounts"
ADD_ALIASES=0

for arg in "$@"; do
  case "$arg" in
    update)
      echo "Updating $REPO_DIR..."
      git -C "$REPO_DIR" pull --ff-only
      ;;
    --aliases) ADD_ALIASES=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }

mkdir -p "$BIN_DIR"
chmod +x "$REPO_DIR/ai_accounts.py"
if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
  echo "$TARGET exists and is not a symlink; move it aside and re-run." >&2
  exit 1
fi
ln -sfn "$REPO_DIR/ai_accounts.py" "$TARGET"
echo "Linked $TARGET -> $REPO_DIR/ai_accounts.py"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: $BIN_DIR is not on your PATH. Add: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

if [ "$ADD_ALIASES" = 1 ]; then
  case "${SHELL:-}" in
    */bash) RC="$HOME/.bashrc" ;;
    *) RC="$HOME/.zshrc" ;;
  esac
  touch "$RC"
  for line in "alias c='ai-accounts run claude --'" "alias codex='ai-accounts run codex --'"; do
    name=$(printf '%s' "$line" | sed "s/^alias \([^=]*\)=.*/\1/")
    if grep -q "^alias $name=" "$RC"; then
      echo "Alias $name already defined in $RC; leaving it."
    else
      printf '%s\n' "$line" >> "$RC"
      echo "Added alias $name to $RC (open a new shell to use it)."
    fi
  done
fi

"$TARGET" --help >/dev/null && echo "ai-accounts is ready. Run: ai-accounts status"
