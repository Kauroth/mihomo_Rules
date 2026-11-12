#!/usr/bin/env bash
# 把规则集编译成 mrs，文本版一起拷进 dist/
#   输入：build/（scripts/fetch-sources.py 从上游拉取生成）+ rules/（手写补丁）
#         同名文件两边都会合并，build 在前、rules 追加在后
#   用法：MIHOMO=/path/to/mihomo bash scripts/build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIHOMO="${MIHOMO:-$ROOT/mihomo}"
DIST="$ROOT/dist"
GEN="$ROOT/build"
SRC="$ROOT/rules"
TMP="$ROOT/.tmp"

[ -x "$MIHOMO" ] || { echo "找不到可执行内核：$MIHOMO"; exit 1; }
rm -rf "$DIST" "$TMP"
mkdir -p "$DIST/domain" "$DIST/ipcidr" "$TMP"

fail=0
compile() {                       # behavior name input keep-ext
  local behavior="$1" name="$2" input="$3" keep="$4"
  local out="$DIST/$behavior/$name.mrs" log
  log="$("$MIHOMO" convert-ruleset "$behavior" text "$input" "$out" 2>&1 || true)"
  # 注意：内核遇到非法行只发 warning，遇到 panic 甚至仍然 exit 0 —— 必须自己 grep
  if grep -qE 'invalid|panic' <<<"$log"; then
    echo "!! $behavior/$name 构建异常："
    sed 's/^/   /' <<<"$log"
    fail=1
  fi
  [ -n "$keep" ] && cp -f "$input" "$DIST/$behavior/$name.$keep"
  printf 'ok  %-30s %8s B (mrs)\n' "$behavior/$name" "$(wc -c < "$out")"
}

for behavior in domain ipcidr; do
  names="$( { ls "$GEN/$behavior" 2>/dev/null || true; ls "$SRC/$behavior" 2>/dev/null || true; } \
            | sed -n 's/\.txt$//p' | sort -u )"
  [ -n "$names" ] || continue
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    merged="$TMP/$name.txt"
    : > "$merged"
    for d in "$GEN/$behavior" "$SRC/$behavior"; do
      [ -f "$d/$name.txt" ] && cat "$d/$name.txt" >> "$merged"
    done
    compile "$behavior" "$name" "$merged" txt
  done <<< "$names"
done

# classical：装 domain 规则集装不下的东西（keyword / regexp / 进程名）
# 只出文本 —— mihomo v1.19.25 的 `convert-ruleset classical text` 必 panic
# （classical_strategy.go:57 nil 解引用），客户端用 `format: text` 读即可。
if [ -d "$GEN/classical" ]; then
  mkdir -p "$DIST/classical"
  for f in "$GEN"/classical/*.list; do
    [ -e "$f" ] || continue
    name="$(basename "$f" .list)"
    cp -f "$f" "$DIST/classical/$name.list"
    printf 'ok  %-30s %8s B (list)\n' "classical/$name" "$(wc -c < "$f")"
  done
fi

[ "$fail" -eq 0 ] || { echo "构建失败：先修掉上面标出的行"; exit 1; }
echo "产物目录：$DIST"
