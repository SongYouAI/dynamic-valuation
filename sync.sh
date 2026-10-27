#!/usr/bin/env bash
# 把本地 skill 同步到 GitHub：SongYouAI/dynamic-valuation
#
# 用法：
#   ./sync.sh                          # 用默认提交说明（带时间戳）
#   ./sync.sh -m "fix: 修正 XX"         # 自定义提交说明
#   ./sync.sh -n                       # 演练：只显示差异，不提交不推送
#
# 环境变量：
#   DV_SYNC_DIR  仓库工作区（默认 ~/.workbuddy/.cache/dv-sync）
#   DV_PYTHON    跑测试用的 python（默认 managed venv，回退 python3）
#   DV_USE_PROXY 设 1 则沿用当前 http(s)_proxy；默认忽略（本机代理常挂，直连 GitHub 更稳）
#
# 规则：
#   - 本地目录是唯一源头；scripts/ templates/ tests/ 用 rsync --delete 镜像，
#     本地删掉的文件也会从仓库删掉（模板 .bak* 备份、缓存文件不会被同步）。
#   - references/ 仅在仓库 .gitignore 未排除时才同步（默认被排除，不公开）。
#   - 测试不通过 → 中止，不提交、不推送。
set -euo pipefail

# 网络：本机 github.com 常被墙、系统代理常失效，这里自动挑一个能通的本地代理
# 只影响本脚本进程，不改任何系统设置。DV_USE_PROXY=1 则原样沿用当前环境变量。
if [ "${DV_USE_PROXY:-0}" = "1" ]; then
  echo "网络：沿用当前代理设置"
else
  CANDIDATES=()
  for v in "${HTTPS_PROXY:-}" "${https_proxy:-}" "${HTTP_PROXY:-}" "${http_proxy:-}"; do
    [ -n "$v" ] && CANDIDATES+=("${v#*://}")
  done
  CANDIDATES+=(127.0.0.1:7897 127.0.0.1:7890 127.0.0.1:1087 127.0.0.1:6152)
  PICKED=""
  for hp in "${CANDIDATES[@]}"; do
    hp="${hp%%/*}"
    if curl -sS -o /dev/null --max-time 3 -x "http://$hp" https://github.com 2>/dev/null; then
      PICKED="$hp"; break
    fi
  done
  if [ -n "$PICKED" ]; then
    export HTTP_PROXY="http://$PICKED" HTTPS_PROXY="http://$PICKED" http_proxy="http://$PICKED" https_proxy="http://$PICKED"
    echo "网络：走本地代理 $PICKED"
  else
    unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy 2>/dev/null || true
    echo "网络：未找到可用代理，改直连（可能超时）"
  fi
fi

REPO="SongYouAI/dynamic-valuation"
BRANCH="main"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${DV_SYNC_DIR:-$HOME/.workbuddy/.cache/dv-sync}"
URL="https://github.com/$REPO"

MSG=""
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    -m|--message) MSG="${2:-}"; shift 2 ;;
    -n|--dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（用 -h 看用法）" >&2; exit 1 ;;
  esac
done

if [ -z "${DV_PYTHON:-}" ]; then
  if [ -x "$HOME/.workbuddy/binaries/python/envs/default/bin/python" ]; then
    PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
  else
    PY="$(command -v python3 || true)"
  fi
else
  PY="$DV_PYTHON"
fi
[ -n "$PY" ] || { echo "❌ 找不到 python，请设 DV_PYTHON" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "❌ 需要 gh CLI（brew install gh && gh auth login）" >&2; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "❌ 需要 rsync" >&2; exit 1; }

echo "① 准备仓库工作区：$WORK"
if [ ! -d "$WORK/.git" ]; then
  mkdir -p "$(dirname "$WORK")"
  gh repo clone "$REPO" "$WORK" -- --quiet
else
  git -C "$WORK" fetch origin "$BRANCH" --quiet
  git -C "$WORK" checkout "$BRANCH" --quiet
  git -C "$WORK" reset --hard "origin/$BRANCH" --quiet
fi

echo "② 覆盖本地文件 → 工作区"
EXC=(--exclude '.DS_Store' --exclude '__pycache__' --exclude '*.pyc' --exclude '*.bak*' --exclude '.pytest_cache')
for f in SKILL.md README.md LICENSE sync.sh; do
  [ -f "$SRC/$f" ] && rsync -a "${EXC[@]}" "$SRC/$f" "$WORK/"
done
for d in scripts templates tests; do
  [ -d "$SRC/$d" ] && rsync -a --delete "${EXC[@]}" "$SRC/$d/" "$WORK/$d/"
done
if [ -d "$SRC/references" ] && ! grep -qE '^/?references/?' "$WORK/.gitignore" 2>/dev/null; then
  rsync -a --delete "${EXC[@]}" "$SRC/references/" "$WORK/references/"
else
  echo "   （references/ 仍被 .gitignore 排除，未同步）"
fi

# 排除规则会保护被排除文件不被 rsync 删除，所以已入库的历史备份要单独摘掉
BAKS="$(git -C "$WORK" ls-files '*.bak*')"
if [ -n "$BAKS" ]; then
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    git -C "$WORK" rm -q --cached -- "$f" 2>/dev/null || true
    rm -f "$WORK/$f"
    echo "   已从仓库移除备份：$f"
  done <<< "$BAKS"
fi

echo "③ 跑回归测试"
LOG="$(mktemp -t dv-sync)"
if ! ( cd "$WORK" && "$PY" tests/test_cases.py >"$LOG" 2>&1 ); then
  echo "❌ 测试未通过，已中止（未提交、未推送）" >&2
  tail -15 "$LOG" >&2
  echo "完整日志：$LOG" >&2
  exit 1
fi
grep -E '测试结果' "$LOG" || true

echo "④ 检查差异"
git -C "$WORK" add -A
if git -C "$WORK" diff --cached --quiet; then
  echo "✅ 本地与远端一致，无需推送"
  exit 0
fi
git -C "$WORK" diff --cached --stat
if [ "$DRY" -eq 1 ]; then
  echo "（--dry-run：已停止，未提交、未推送）"
  exit 0
fi

echo "⑤ 提交并推送"
[ -n "$MSG" ] || MSG="sync: $(date '+%Y-%m-%d %H:%M') 同步本地 skill"
git -C "$WORK" commit -q -m "$MSG"
git -C "$WORK" push origin "$BRANCH" --quiet
echo "✅ 已推送 $URL"
git -C "$WORK" log --oneline -1
