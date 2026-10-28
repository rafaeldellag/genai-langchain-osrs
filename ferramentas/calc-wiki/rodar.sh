#!/usr/bin/env bash
# Usa a calculadora de DPS da OSRS Wiki (weirdgloop/osrs-dps-calc, GPL-3.0) para:
#   tabelas   -> regenera conselheiro/dados/tabelas.json (estilos, munição, aliases)
#   gabarito  -> recalcula tests/gabarito/esperado.json a partir de tests/gabarito/casos.json
#
# Requer Node 20+ e git. A calculadora é clonada em $CALC_DIR (padrão: .cache/osrs-dps-calc).
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
CALC_DIR="${CALC_DIR:-$RAIZ/.cache/osrs-dps-calc}"
CALC_REF="${CALC_REF:-main}"

if [ ! -d "$CALC_DIR/.git" ]; then
  git clone --depth 1 --branch "$CALC_REF" https://github.com/weirdgloop/osrs-dps-calc.git "$CALC_DIR"
fi
if [ ! -d "$CALC_DIR/node_modules" ]; then
  (cd "$CALC_DIR" && corepack enable >/dev/null 2>&1 || true; cd "$CALC_DIR" && yarn install --mode=skip-build)
fi

cp "$RAIZ/ferramentas/calc-wiki/oracle.test.ts" "$CALC_DIR/src/tests/oracle.test.ts"
echo "osrs-dps-calc em $(git -C "$CALC_DIR" rev-parse --short HEAD)"

case "${1:-}" in
  tabelas)
    (cd "$CALC_DIR" && EXPORT_OUT="$RAIZ/conselheiro/dados/tabelas.json" \
      yarn jest src/tests/oracle.test.ts -t 'export tables' --coverage=false)
    ;;
  gabarito)
    (cd "$CALC_DIR" && ORACLE_IN="$RAIZ/tests/gabarito/casos.json" \
      ORACLE_OUT="$RAIZ/tests/gabarito/esperado.json" \
      yarn jest src/tests/oracle.test.ts -t 'oracle' --coverage=false)
    ;;
  *)
    echo "uso: $0 tabelas|gabarito" >&2
    exit 2
    ;;
esac
