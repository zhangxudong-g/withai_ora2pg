# AGENTS.md

## 工作规范

* **先读后改**：执行任务前先阅读相关代码、`HANDOFF.md` 和实施路径文档。
* **证据优先**：以代码、测试和实际执行结果为依据。
* **小步修改**：最小、可验证的修改，不改无关文件。
* **验证优先**：未执行的测试不得声称通过。
* **LLM 输出必须验证**：模型结果仅作为候选，必须工具验证。

## 项目目标

oracle_poc Oracle→PG 转换，让产物达到"一键安装 + 首调可用 + 业务等价"。

实施唯一权威：`docs/最终实施路径_生产级_2026-10-09.md`。当前状态：`HANDOFF.md`。

### DoD

| DoD | 验收 |
|---|---|
| DoD-1 | psql 一键安装 0 错误 |
| DoD-2 | 残留扫描器 major=0 |
| DoD-3 | L3.5 冒烟 0 fail |
| DoD-4 | L4 业务等价 100% |
| DoD-5 | Gate ≥ 90 |
| DoD-6 | 规则修复后必须重转产物，禁止 commit 已修但产物未重跑 |
| DoD-7 | 对象数缺失即判 fail |

### 关键原则

- 所有 PG 语义结论必须 **live PG 15.19 实测**
- 嵌套子程序必须 lift 为独立函数，禁止文本内联
- `pipeline/` 冻结只读，任何情况不得修改

### 落点纪律

| 类型 | 落点 |
|---|---|
| 规则改动 | `ora2pg_py_perlstyle/perlstyle_engine.py`（唯一真实引擎）|
| 验证层 | `workspace/verification/`（唯一真源）|
| 新规则/Agent/单测 | `workspace/` |
| AST grammar | `workspace/parsers/grammar/` |
| ⛔ `pipeline/` | **冻结只读** |

## 实施纪律

- **实施路径是唯一权威**，偏离须先更新文档
- 每个 P* 任务至少 1 个 commit，message 引用任务编号
- 单测按内容断言，**禁止写死行号**
- 范围外不擅自修
- `verify_oracle_poc_output.py` 会覆盖 `_verification_*` 文件，**跑前备份或跑后 `git checkout -- oracle_poc/output/` 还原**

## 完成标准

```
P* 实施后:
  1. 单测通过 (pytest workspace/tests/test_<task>.py)
  2. 全门禁通过（残留 0 major + L3.5 0 fail + 完整性 0 缺失）
  3. 重转产物 (python scripts/convert_oracle_poc.py --clean)
  4. commit（产物与源码同一 commit）
  5. HANDOFF.md 更新状态
```

## 不在范围

- 转换器整体重写
- 修改 `pipeline/`（冻结）
- 范围外的重构、新增独立功能
