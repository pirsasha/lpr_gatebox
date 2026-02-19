#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

echo "[finalize14] running strict release gate..."
BASE_URL="$BASE_URL" bash scripts/release_gate_strict.sh

echo "[finalize14] strict gate passed, marking docs as done..."

python - <<'PY'
from pathlib import Path

root = Path('.')
chk = root / 'docs/iteration14-checklist-ru.md'
plan = root / 'docs/product-plan-ui-runtime.md'

s = chk.read_text(encoding='utf-8')
s = s.replace(
    '- [ ] Зафиксировать в product plan статус итераций как `done` после прохождения `scripts/release_gate_strict.sh` на стенде.',
    '- [x] Зафиксировать в product plan статус итераций как `done` после прохождения `scripts/release_gate_strict.sh` на стенде.'
)
chk.write_text(s.rstrip() + '\n', encoding='utf-8')

p = plan.read_text(encoding='utf-8')
line = '- Iteration 14 done: strict release gate (`scripts/release_gate_strict.sh`) успешно пройден на стенде, чеклист закрыт.\n'
if line not in p:
    p = p.rstrip() + '\n' + line
plan.write_text(p.rstrip() + '\n', encoding='utf-8')
PY

echo "[finalize14] docs updated: Iteration 14 marked done"
