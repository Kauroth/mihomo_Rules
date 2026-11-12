#!/usr/bin/env bash
# 把 rules/ 下的文本规则集编译成 mrs，文本版一起拷进 dist/
# 用法：MIHOMO=/path/to/mihomo bash scripts/build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIHOMO="${MIHOMO:-$ROOT/mihomo}"
DIST="$ROOT/dist"
SRC="$ROOT/rules"

[ -x "$MIHOMO" ] || { echo "找不到可执行内核：$MIHOMO"; exit 1; }
rm -rf "$DIST"
mkdir -p "$DIST/domain" "$DIST/ipcidr"

fail=0
build() {
  local behavior="$1" dir f name out log
  dir="$SRC/$1"
  [ -d "$dir" ] || return 0
  shopt -s nullglob
  for f in "$dir"/*.txt; do
    name="$(basename "$f" .txt)"
    out="$DIST/$behavior/$name"
    log="$("$MIHOMO" convert-ruleset "$behavior" text "$f" "$out.mrs" 2>&1 || true)"
    if grep -q 'invalid' <<<"$log"; then
      echo "!! $f 含非法行："
      sed 's/^/   /' <<<"$log"
      fail=1
    fi
    cp -f "$f" "$out.txt"
    printf 'ok  %-30s %6s B (mrs)\n' "$behavior/$name" "$(wc -c < "$out.mrs")"
  done
}
build domain
build ipcidr

[ "$fail" -eq 0 ] || { echo "构建失败：先修掉上面标出的行"; exit 1; }
echo "产物目录：$DIST"
