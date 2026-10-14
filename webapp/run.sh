#!/usr/bin/env bash
#
# 一键启动 TraceEvo 双图审计溯源 Web 系统。
#
#   ./webapp/run.sh            开发模式：后端 8000 + 前端 dev server 5173
#   ./webapp/run.sh --build    构建模式：只起后端 8000，由它托管构建产物
#   ./webapp/run.sh --build-only  只构建前端，不起服务
#
# 开发模式起两个进程是为了热更新；演示时用 --build 只需要一个端口，
# 少一个进程就少一个可能出问题的地方。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${TRACEEVO_PORT:-8000}"
FRONTEND_PORT="${TRACEEVO_WEB_PORT:-5173}"
MODE="dev"
case "${1:-}" in
  --build) MODE="build" ;;
  --build-only) MODE="build-only" ;;
  -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "未知参数：$1（用 --help 查看用法）" >&2; exit 2 ;;
esac

cd "$ROOT"

# ------------------------------------------------------------------ 依赖检查
command -v python3 >/dev/null || { echo "缺少 python3" >&2; exit 1; }

python3 - <<'PY' || { echo "缺少 Python 依赖。请先：pip install -r webapp/backend/requirements.txt" >&2; exit 1; }
import importlib.util, sys   # importlib.util 不会被 importlib 自动带上
missing = [m for m in ("fastapi", "uvicorn", "pydantic")
           if importlib.util.find_spec(m) is None]
sys.exit(1 if missing else 0)
PY

if [ "$MODE" != "build-only" ] || [ ! -d frontend/dist ]; then
  command -v npm >/dev/null || { echo "缺少 npm" >&2; exit 1; }
fi

# ------------------------------------------------------------------ 前端
cd "$ROOT/webapp/frontend"
if [ ! -d node_modules ]; then
  echo "▸ 安装前端依赖（首次运行，约一分钟）…"
  npm install --no-audit --no-fund
fi

if [ "$MODE" = "build" ] || [ "$MODE" = "build-only" ]; then
  echo "▸ 构建前端…"
  npm run build
  if [ "$MODE" = "build-only" ]; then
    echo "✓ 构建完成：webapp/frontend/dist"
    exit 0
  fi
fi

# ------------------------------------------------------------------ 启动
cd "$ROOT"
PIDS=()
cleanup() {
  trap - INT TERM EXIT
  for p in "${PIDS[@]:-}"; do
    [ -n "$p" ] && kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

if [ "$MODE" = "dev" ]; then
  echo "▸ 后端 http://127.0.0.1:${BACKEND_PORT}"
  python3 -m uvicorn webapp.backend.app:app \
    --host 127.0.0.1 --port "$BACKEND_PORT" --log-level warning &
  PIDS+=($!)

  # 把反代目标告诉 Vite。端口是 8000 时没有实际影响，但一旦换端口
  # （比如 8000 被别的服务占着），少了这一句，前端会静默地去连 8000
  # 上那个**别的**服务 —— 表现是接口返回 404 或形状不对的 JSON，
  # 而不是「连不上」，排查起来会绕远路。
  export TRACEEVO_API="http://127.0.0.1:${BACKEND_PORT}"

  echo "▸ 前端 http://127.0.0.1:${FRONTEND_PORT}"
  npm --prefix "$ROOT/webapp/frontend" run dev -- --port "$FRONTEND_PORT" &
  PIDS+=($!)

  echo
  echo "✓ 打开 http://127.0.0.1:${FRONTEND_PORT}/ 进入系统总览。Ctrl-C 停止。"
else
  echo "▸ 后端（含静态托管）http://127.0.0.1:${BACKEND_PORT}"
  python3 -m uvicorn webapp.backend.app:app \
    --host 127.0.0.1 --port "$BACKEND_PORT" --log-level warning &
  PIDS+=($!)
  echo
  echo "✓ 打开 http://127.0.0.1:${BACKEND_PORT}/workbench/case-ankle 直接进入踝关节展示。Ctrl-C 停止。"
fi

wait
