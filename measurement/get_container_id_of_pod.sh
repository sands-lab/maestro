#!/usr/bin/env bash
set -euo pipefail

# 获取包含指定名称的 Pod 的“Pod 名称 -> 容器ID”列表（支持多名称）
# 用法:
#   ./get_container_id_of_pod.sh [-n NAMESPACE | -A] [-a] [-S] NAME_SUBSTRING [NAME_SUBSTRING ...]
# 选项:
#   -n NAMESPACE  指定命名空间（默认使用当前 context 的 namespace）
#   -A            所有命名空间（与 -n 互斥）
#   -a            包含 init 与 ephemeral 容器（默认也包含，保留此开关仅为显式）
#   -S            保留容器 ID 的 runtime 前缀（如 containerd://、docker://）
# 示例:
#   ./get_container_id_of_pod.sh -n kagent coordinator
#   ./get_container_id_of_pod.sh -A -S editor writer planner

NS_ARGS=()
INCLUDE_ALL=true
KEEP_SCHEME=false

usage() {
  sed -n '1,25p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts ":n:ASah" opt; do
  case "$opt" in
    n) NS_ARGS=(-n "$OPTARG") ;;
    A) NS_ARGS=(-A) ;;
    a) INCLUDE_ALL=true ;;  # 兼容占位
    S) KEEP_SCHEME=true ;;
    h) usage; exit 0 ;;
    \?) echo "无效选项: -$OPTARG" >&2; usage; exit 2 ;;
    :) echo "选项 -$OPTARG 需要参数" >&2; usage; exit 2 ;;
  esac
done
shift $((OPTIND-1))

if [[ $# -lt 1 ]]; then
  echo "缺少 NAME_SUBSTRING 参数" >&2
  usage
  exit 2
fi

# 支持多个名称子串
NAME_SUBSTRS=("$@")

if ! command -v kubectl >/dev/null 2>&1; then
  echo "需要 kubectl 命令" >&2
  exit 1
fi

strip_scheme() {
  if $KEEP_SCHEME; then
    cat
  else
    sed -E 's@^[a-zA-Z0-9._+-]+://@@'
  fi
}

# 优先使用 jq（更稳定可靠）
if command -v jq >/dev/null 2>&1; then
  # 构建正则模式：name1|name2|...
  PATTERN=$(printf '%s|' "${NAME_SUBSTRS[@]}")
  PATTERN=${PATTERN%|}

  if $KEEP_SCHEME; then
    kubectl get pods "${NS_ARGS[@]}" -o json \
    | jq -r --arg pattern "$PATTERN" '
        .items[]
        | . as $pod
        | select($pod.metadata.name | test($pattern))
        | (
            [
              ($pod.status.containerStatuses // []),
              ($pod.status.initContainerStatuses // []),
              ($pod.status.ephemeralContainerStatuses // [])
            ] | add
          ) as $all
        | ($all
            | map(.containerID)
            | map(select(. != null and . != ""))
          ) as $ids
        | if ($ids | length) > 0 then
            "\($pod.metadata.name) -> \($ids | unique | join(" "))"
          else empty end
      '
  else
    kubectl get pods "${NS_ARGS[@]}" -o json \
    | jq -r --arg pattern "$PATTERN" '
        .items[]
        | . as $pod
        | select($pod.metadata.name | test($pattern))
        | (
            [
              ($pod.status.containerStatuses // []),
              ($pod.status.initContainerStatuses // []),
              ($pod.status.ephemeralContainerStatuses // [])
            ] | add
          ) as $all
        | ($all
            | map(.containerID)
            | map(select(. != null and . != ""))
            | map(gsub("^[A-Za-z0-9._+-]+://"; ""))
          ) as $ids
        | if ($ids | length) > 0 then
            "\($pod.metadata.name) -> \($ids | unique | join(" "))"
          else empty end
      '
  fi
  exit 0
fi

# 无 jq 的回退方案（逐 Pod 用 jsonpath 取）
# 获取匹配的 Pod 名称列表（任一子串匹配即可）
pattern=$(printf '|%s' "${NAME_SUBSTRS[@]}")
pattern=${pattern:1}
mapfile -t PODS < <(kubectl get pods "${NS_ARGS[@]}" -o name \
  | awk -F/ '{print $2}' \
  | grep -E "$pattern" || true)

if [[ ${#PODS[@]} -eq 0 ]]; then
  exit 0
fi

TMP_FILE="$(mktemp)"
cleanup() { rm -f "$TMP_FILE"; }
trap cleanup EXIT

for pod in "${PODS[@]}"; do
  ids="$(kubectl get pod "$pod" "${NS_ARGS[@]}" -o jsonpath='{range .status.containerStatuses[*]}{.containerID}{"\n"}{end}{range .status.initContainerStatuses[*]}{.containerID}{"\n"}{end}{range .status.ephemeralContainerStatuses[*]}{.containerID}{"\n"}{end}' || true)"
  # 清洗、去重并拼接
  cleaned=$(printf "%s\n" "$ids" | sed '/^$/d' | { if $KEEP_SCHEME; then cat; else sed -E 's@^[A-Za-z0-9._+-]+://@@'; fi; } | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')
  if [[ -n "$cleaned" ]]; then
    printf "%s -> %s\n" "$pod" "$cleaned" >> "$TMP_FILE"
  fi
done

# 输出（已按 Pod 聚合）
sed -i '/^$/d' "$TMP_FILE"
cat "$TMP_FILE" | sort