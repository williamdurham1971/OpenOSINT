#!/usr/bin/env bash
# One paper-trading tick for every lab bot, with state kept in git so any fresh machine can continue where the last one
# stopped. Run it a few minutes after each hour (cron, a scheduled cloud task, or a CI schedule). Simulated money only.
set -euo pipefail
cd "$(dirname "$0")/.."
export BTC_BOT_STATE="$PWD/paper_state"
mkdir -p "$BTC_BOT_STATE"
python3 btc_bot.py lab --once
python3 btc_bot.py lab-status | tee "$BTC_BOT_STATE/STATUS.txt"
if [ "${COMMIT:-1}" = "1" ]; then
  git add -- "$BTC_BOT_STATE"
  if ! git diff --cached --quiet -- "$BTC_BOT_STATE"; then
    git commit -q -m "paper lab tick $(date -u +%Y-%m-%dT%H:%MZ)" -m "${COMMIT_TRAILER:-Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>}" -- "$BTC_BOT_STATE"
    branch="$(git rev-parse --abbrev-ref HEAD)"
    for i in 1 2 3 4; do                                   # another tick may have pushed meanwhile
      git push -q origin "HEAD:$branch" && break
      git pull -q --rebase origin "$branch" && continue
      sleep $((2 ** i))
    done
  fi
fi
