#!/usr/bin/env bash
set -euo pipefail

DS="${1:-37a2c0fc-fd10-8077-8040-000b0d80b854}"
OUTPUT="${2:-docs/notion/workspace-tree.md}"

command -v ntn >/dev/null 2>&1 || {
  echo "error: ntn command not found" >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || {
  echo "error: jq command not found" >&2
  exit 1
}

TMP="$(mktemp)"
TREE_TMP="$(mktemp)"

cleanup() {
  rm -f "$TMP" "$TREE_TMP"
}
trap cleanup EXIT

CURSOR=""
PAGE_COUNT=0

while true; do
  if [[ -n "$CURSOR" ]]; then
    RESP="$(
      ntn datasources query "$DS" \
        --limit 100 \
        --start-cursor "$CURSOR" \
        --json
    )"
  else
    RESP="$(
      ntn datasources query "$DS" \
        --limit 100 \
        --json
    )"
  fi

  jq -c '.results[]' <<< "$RESP" >> "$TMP"

  COUNT="$(jq '.results | length' <<< "$RESP")"
  PAGE_COUNT=$((PAGE_COUNT + COUNT))

  if [[ "$(jq -r '.has_more' <<< "$RESP")" != "true" ]]; then
    break
  fi

  CURSOR="$(jq -r '.next_cursor' <<< "$RESP")"
done

jq -rs -r '
  map({
    id: .id,
    title: (
      .properties["메뉴명"].title
      | map(.plain_text)
      | join("")
    ),
    parent: (
      .properties["상위 항목"].relation[0].id // null
    )
  }) as $nodes

  | def children($parent_id):
      [
        $nodes[]
        | select(.parent == $parent_id)
      ]
      | sort_by(.title);

    def print_children($parent_id; $prefix):
      children($parent_id) as $children
      | range(0; $children | length) as $i
      | $children[$i] as $node
      | ($i == (($children | length) - 1)) as $last
      | (
          $prefix
          + (if $last then "└─ " else "├─ " end)
          + $node.title
        ),
        print_children(
          $node.id;
          $prefix + (if $last then "   " else "│  " end)
        );

    [
      $nodes[]
      | select(.parent == null)
    ]
    | sort_by(.title)
    | .[]
    | .title,
      print_children(.id; "")
' "$TMP" > "$TREE_TMP"

mkdir -p "$(dirname "$OUTPUT")"

# 트리 내용이 같으면 generated_at도 갱신하지 않는다.
if [[ -f "$OUTPUT" ]]; then
  EXISTING_TREE="$(
    awk '
      /^```text$/ { in_tree=1; next }
      /^```$/ && in_tree { exit }
      in_tree { print }
    ' "$OUTPUT"
  )"

  NEW_TREE="$(cat "$TREE_TMP")"

  if [[ "$EXISTING_TREE" == "$NEW_TREE" ]]; then
    echo "No changes: $OUTPUT"
    exit 0
  fi
fi

GENERATED_AT="$(TZ=Asia/Seoul date --iso-8601=seconds)"
NTN_VERSION="$(ntn --version | head -n 1)"

{
  cat <<EOF
---
source: notion
data_source_id: "$DS"
generated_at: "$GENERATED_AT"
timezone: "Asia/Seoul"
page_count: $PAGE_COUNT
generator: "bin/notion-tree.sh"
notion_cli: "$NTN_VERSION"
---

# Notion 작업 DB 구조

이 파일은 Notion 작업 DB의 \`메뉴명\`과 \`상위 항목\` 관계를 기준으로 생성한 스냅샷입니다.

\`\`\`text
EOF

  cat "$TREE_TMP"

  cat <<'EOF'
```
EOF
} > "${OUTPUT}.tmp"

mv "${OUTPUT}.tmp" "$OUTPUT"

echo "Updated: $OUTPUT"
