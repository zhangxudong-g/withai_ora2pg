"""workspace/agent — P4-1 AI Agent 工具链

参考 pipeline/stages/stage3_ai/ 但**新写**（pipeline/ 已冻结）。

模块：
- config: 配置（Ollama URL / 模型 / 配额 / 缓存）
- llm_client: OllamaClient（cache + retry + quota）
- quota: 全局配额计数器（持久化）
- validate_pg: 硬校验 PG 语法（强化版：正则 + 真 PG 试装）
- chunker: 按函数边界切块
- prompt_builder: 构造 LLM prompt
- orchestrator: 主流程（编排 4 步：分块 → 转换 → 校验 → 替换）
- snapshot: 每批前 git 快照

设计原则：
1. 模板可复现：temperature=0（项目硬约束）
2. 配额不可越界：60–200 次上限触顶 → QuotaExceeded
3. 失败可回滚：每批 git snapshot + 自动 checkout
4. 失败必留 TODO（不静默 NULL）：RAISE EXCEPTION 'not implemented'
5. 缓存复用：同输入不重复调（节省配额）
"""