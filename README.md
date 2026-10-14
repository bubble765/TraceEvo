# TraceEvo

医学多智能体协作诊断系统，包含因果推理图、审计溯源图、报告后审计与恢复，以及三源 RAG。

## 当前目录

- `gos/`：中央智能体、专家智能体、因果图、状态机、提示词与诊断入口。
- `traceevo/`：审计、经验机制、RAG、评测与测试。
- `webapp/`：前后端、双图工作台、知识库浏览及演示病例。
- `configs/`：当前 RAG 配置。
- `traceevo_runs/rag/`：当前知识库、独立经验病例集与最新检索/图评测结果。
- `evaluations/`：最终诊断评测及必要基线，保留原始结果和封存依据。
- `refine-logs/`：当前审计反馈实验与评测计划。

历史阶段文件已移出工作区。少量阶段命名的 Python 辅助模块仍由当前诊断、评测或测试调用，保留为共享工具。
旧格式兼容代码仍受测试覆盖，当前医疗语料与向量索引使用 schema-v5。

## 启动

安装依赖后执行 `./webapp/run.sh`。端口占用时可用
`TRACEEVO_PORT=8001 ./webapp/run.sh`。前端默认地址为 http://127.0.0.1:5173/。

前后端说明见 [webapp/README.md](webapp/README.md)，RAG 说明见
[traceevo/rag/README.md](traceevo/rag/README.md)。模型权重和运行环境需另行安装；API 密钥只保存在本机。

## 评测依据

最新医疗检索结果见 `traceevo_runs/rag/medical_retrieval_expansion_v2/REPORT.md`。
其原配置基线保留在 `traceevo_runs/rag/medical_retrieval_eval_v1/`，用于指标比较。
图质量评测位于独立经验病例集中的 `graph_quality_v1/`。
拒绝阈值尚未默认启用，公开结果属于小样本试测。

公开仓库不包含环境、模型、本地密钥或本机清理备份。数据使用须遵守各原始数据源的许可；医学评测结果不等同于临床验证。
