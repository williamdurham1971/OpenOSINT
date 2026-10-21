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
    git commit -q -m "paper lab tick $(date -u +%Y-%m-%dT%H:%MZ)" -- "$BTC_BOT_STATE"
    git push -q
  fi
fi
