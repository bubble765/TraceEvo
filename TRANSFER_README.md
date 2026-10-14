# TraceEvo 传输副本

此仓库包含项目代码、数据、文档与实验产物，来自 2026-10-14 的项目压缩包。

未包含 Python 虚拟环境、node_modules、模型权重、缓存、原 Git 历史和本地密钥配置。
使用前需安装依赖、下载模型，并在本机配置 API 密钥。请勿提交密钥。

前后端启动方法见 [webapp/README.md](webapp/README.md)，RAG 说明见
[traceevo/rag/README.md](traceevo/rag/README.md)。

运行默认开发服务：`./webapp/run.sh`。若 8000 端口已占用，可使用
`TRACEEVO_PORT=8001 ./webapp/run.sh`。

此传输仓库使用独立的初始提交，原本地项目及其 Git 工作区未修改。
