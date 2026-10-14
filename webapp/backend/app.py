"""FastAPI 入口。

启动：`uvicorn webapp.backend.app:app --reload --port 8000`
或直接用 `webapp/run.sh`（同时起后端与前端）。

静态托管是可选的：`webapp/frontend/dist` 存在时它会挂在根路径上，
这样 `npm run build` 之后不需要再起 dev server。开发时不存在该目录，
前端走 Vite 的 5173 端口，由 CORS 放行。
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, i18n
from .routes import audit, cases, experience, graphs, knowledge_bases, raw

app = FastAPI(
    title="TraceEvo 双图审计溯源",
    description="面向医学多智能体协同推理的双图审计溯源与可信经验固化",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (cases.router, graphs.router, audit.router, experience.router,
          knowledge_bases.router, raw.router):
    app.include_router(r, prefix=config.API_PREFIX)


@app.get(config.API_PREFIX + "/health")
def health():
    """存活探针。同时报告数据源里有多少病例可读 —— 一个 0 病例的
    「健康」服务对使用者没有意义，所以它一并返回。"""
    # 数的是**数据源**里能点开的病例数，与 `/api/cases` 同一口径。
    # 用 `config.all_cases()` 会少算手写展示轨迹 —— 探针报的数比界面
    # 少一个，这种不一致只会在「界面看得到、探针说没有」时才被发现。
    try:
        from .sources import get_source
        n = len(get_source().available())
    except Exception:
        n = -1
    return {"ok": True, "demoCase": config.case_task(config.DEMO_CASE),
            "nCases": n}


@app.get(config.API_PREFIX + "/i18n")
def vocabulary():
    """整份中文词表。前端不硬编码任何业务文案，一律从这里取。"""
    return i18n.vocabulary()


@app.exception_handler(KeyError)
async def _key_error(_request, exc: KeyError):
    """未登记的病例 -> 404，而不是 500。

    KeyError 在本系统里的语义就是「查无此病例」（`config.run_dir_for`
    在找不到时抛它）。不处理会变成一个 500，让「这个病例不在系统里」
    看起来像「系统坏了」。
    """
    return JSONResponse(status_code=404, content={"detail": str(exc)})


# ------------------------------------------------------------------ 静态托管
_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "frontend", "dist")

if os.path.isdir(_DIST):
    # 构建产物挂在 /assets 下（Vite 的默认资源前缀），而不是把整个目录挂到 "/"。
    # 后者会让 `/workbench/case-85` 这类**前端路由**直接 404 —— StaticFiles
    # 只认文件，不认 SPA 的路由表。结果是在页面上点一次病例切换就白屏，
    # 而开发模式下（Vite dev server）一切正常，所以这个问题只会在演示时出现。
    _assets = os.path.join(_DIST, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """未命中 API 的路径一律交回 index.html，由前端路由接管。"""
        if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
            return JSONResponse(status_code=404, content={"detail": "未找到"})
        index = os.path.join(_DIST, "index.html")
        if not os.path.exists(index):
            return JSONResponse(status_code=404, content={"detail": "前端未构建"})
        with open(index, encoding="utf-8") as f:
            return HTMLResponse(f.read())
