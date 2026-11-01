#!/bin/zsh
# Opened by "InCad.app" on the Desktop: a Claude Code session in the InCad project.
# Resumes your latest conversation in this project when there is one, otherwise starts a new one.
PROJ="/Users/igor/Claude_projects/InCad"
CLAUDE="$HOME/.local/bin/claude"
[[ -x "$CLAUDE" ]] || CLAUDE="$(command -v claude)" || { echo "❌ Claude Code not found."; read -k1 "?Press any key…"; exit 1; }
printf '\e]0;InCad\a'
cd "$PROJ" || { echo "❌ Project folder not found: $PROJ (is the Docs volume mounted?)"; read -k1 "?Press any key…"; exit 1; }
echo "🧊 InCad — $PROJ"
TALKS="$HOME/.claude/projects/$(print -rn -- "$PROJ" | sed 's/[^A-Za-z0-9]/-/g')"
for f in "$TALKS"/*.jsonl(Nom); do
  echo "Resuming the latest conversation…"
  exec "$CLAUDE" --resume "${f:t:r}"
done
echo "No earlier conversation — starting a new one."
exec "$CLAUDE"
