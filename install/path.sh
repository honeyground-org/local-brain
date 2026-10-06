#!/bin/sh
# Puts `brain` on PATH so it works from any folder.
#
# ⛔ It's a ★symlink★, not a copy — fixing the repository takes effect immediately, with no two copies to drift apart.
#    (`bin/brain` follows the link to find the repository.)
set -e
DIR=$(cd "$(dirname "$0")/.." && pwd)
for D in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
  case ":$PATH:" in *":$D:"*) [ -w "$D" ] && TARGET=$D && break ;; esac
done
if [ -z "$TARGET" ]; then
  mkdir -p "$HOME/.local/bin"
  TARGET=$HOME/.local/bin
  echo "⛔ no writable PATH folder found, so $TARGET was created."
  echo "   add this one line to your shell config (~/.zshrc for zsh):"
  echo '     export PATH="$HOME/.local/bin:$PATH"'
fi
ln -sfn "$DIR/bin/brain" "$TARGET/brain"
echo "link: $TARGET/brain → $DIR/bin/brain"
command -v brain >/dev/null 2>&1 \
  && echo "found: $(command -v brain)  ·  now usable from any folder as ★brain★" \
  || echo "⚠️ open a new terminal, or re-read your shell (exec zsh)"
