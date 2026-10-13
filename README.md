# Oracle → PostgreSQL 迁移工具

**目标**：将 Oracle PL/SQL 代码转换为 PostgreSQL，达到"一键安装 + 首调可用 + 业务等价"的可交付状态。

---

## 快速导航

| 你要做什么 | 从哪里开始 |
|---|---|
| 🎯 **手动测试** | `docs/人工测试方案_2026-10-12.md` |
| 🔧 **改规则开发** | `HANDOFF.md` → `docs/最终实施路径_生产级_2026-10-09.md` |
| 📊 **当前状态** | `HANDOFF.md` §1 当前基线 vs 目标 |
| 📖 **完整实施计划** | `docs/最终实施路径_生产级_2026-10-09.md` |

---

## 项目结构

```
withai_ora2pg/
├── ora2pg_py_perlstyle/      # 核心转换引擎（唯一真实引擎，约6600行）
│   ├── perlstyle_engine.py   # 规则转换主引擎
│   └── plsql_subs.py         # 占位符替换
│
├── workspace/                 # 新能力实现目录
│   ├── rules/                # 转换规则（%TYPE/%SUBQUERY/异常/OUTER_JOIN...）
│   ├── agent/                # AI Agent 工具链（validate_pg/quota/orchestrator...）
│   ├── parsers/              # ANTLR 语法解析（结构体检，不做翻译）
│   ├── verification/         # 验证框架（L1-L4 + 残留扫描）
│   └── tests/                # 单测（386个用例）
│
├── scripts/                  # 入口脚本
│   ├── convert_oracle_poc.py # 转换 Oracle → PG 产物
│   ├── install_oracle_poc.py # 部署到 PostgreSQL
│   ├── verify_oracle_poc_output.py  # 验证（L1-L4 + Gate）
│   └── scan_oracle_residuals.py     # 残留扫描（DoD-2门禁）
│
├── oracle_poc/               # 业务场景：学生成绩管理系统
│   ├── 01.SQL/              # Oracle 源（13类/55个对象）
│   └── output/              # PG 转换产物
│
└── docs/                    # 文档（实施路径/分析报告/迁移手册...）
```

---

## 三条核心命令

| # | 动作 | 命令 | 副作用 |
|---|---|---|---|
| ① | **转换** | `python scripts/convert_oracle_poc.py --clean` | 覆盖 `oracle_poc/output/` |
| ② | **安装** | `PGPASSWORD=postgres python scripts/install_oracle_poc.py install` | 建 PG schema |
| ③ | **验证** | `PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py` | 覆盖验证报告 |

> **只读工具**：`python scripts/scan_oracle_poc_residuals.py oracle_poc/output`（扫 Oracle 关键字残留）

---

## 当前状态（2026-10-13 实测）

| 指标 | 当前 | 目标 |
|---|---|---|
| **总分** | **83.0 / C** | ≥90 / A |
| **L3 安装** | **225/243**（18失败）| 0 失败 |
| **L3.5 冒烟** | **33/38 + 5 fail** | 0 fail |
| **残留 major** | **30** | 0 |
| **单测** | **386/386** ✅ | — |
| **Gate** | 未通过 | 通过 |

### 实施阶段

| 阶段 | 任务 | 状态 |
|---|---|---|
| 一（D1-D4） | P1-1~P1-4：静默丢失根治、完整性门禁、残留扫描、首调冒烟 | ✅ 完成 |
| 二（D5-D14） | P2-1~P2-6：%SUBQUERY、%TYPE、(+)、序列、异常、时间语义 | ✅ 完成 |
| 三（D15-D20） | P3-1~P3-4：AST适配器、体检器、元数据、触发器升级 | ✅ 完成 |
| 四（D21-D36） | P4-1~P4-4：Agent工具链、TODO回灌、复杂体专项、TYPE重设计 | 🟡 进行中 |
| 五（D37-D44） | P5-1~P5-4：L4等价用例、工程化、规则目录 | ⬜ 待开始 |

---

## 技术架构（三层路由）

```
Oracle 源文件
    │
    ├─[规则引擎] perlstyle_engine（主力，4.7ms/file）
    │    └─ 落点：ora2pg_py_perlstyle/perlstyle_engine.py
    │
    ├─[ANTLR AST] 结构体检（~11.7s/file，只做检查不做翻译）
    │    └─ 落点：workspace/parsers/grammar/
    │
    └─[AI Agent] 复杂体最后一公里
         └─ 落点：workspace/agent/（validate_pg 硬校验）
```

**三区路由原则**：
- 复杂度低 → 走规则（快、稳）
- 含 AST 可解析的语义结构 → 走 ANTLR（体检仪）
- 规则/解析搞不定的复杂体 → 走 AI Agent（带 `validate_pg` 硬校验）

---

## 验证框架（五道门禁）

| 门禁 | 内容 | 目标 |
|---|---|---|
| **DoD-1** | `psql -v ON_ERROR_STOP=1 -f 00_CREATE_ALL_PG.sql` | 0 错误 |
| **DoD-2** | 残留扫描（Oracle关键字/%TYPE/SQLCODE...）| major=0 |
| **DoD-3** | L3.5 首调冒烟（每个对象至少一次真实调用）| fail=0 |
| **DoD-4** | L4 业务等价（输入/输出快照对比）| 100% |
| **DoD-7** | 完整性断言（源对象数 ≤ 产物对象数）| 0 缺失 |

---

## 关键原则

1. **"能安装 ≠ 可用"**：plpgsql 延迟解析，所有 PL/SQL 必须经 L3.5 首调才算通过
2. **"能转换 ≠ 没丢东西"**：DoD-2 只查残留，查不出缺失（源6触发器→产物1个，报告记success）
3. **落点纪律**：规则改动必须落 `ora2pg_py_perlstyle/perlstyle_engine.py`；**`pipeline/` 已冻结只读**（0处生产引用）
4. **DoD-6 流程纪律**：任何引擎规则修复后必须重转产物并全量复验

---

## 开发闭环

```bash
# 1. 单测（5秒内）
python -m pytest workspace/tests/test_<task>.py -v

# 2. 转换：重转产物
python scripts/convert_oracle_poc.py --clean

# 3. 安装：装到 PG
PGPASSWORD=postgres python scripts/install_oracle_poc.py install --schema dev_test

# 4. 验证：跑 Gate
PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py

# 5. 残留扫描
python scripts/scan_oracle_residuals.py oracle_poc/output
```

---

## 文档索引

| 文档 | 说明 |
|---|---|
| `HANDOFF.md` | 当前状态视图、接任务指南 |
| `docs/最终实施路径_生产级_2026-10-09.md` | **实施唯一权威**：路径+架构+任务 |
| `docs/转换产物分析整合报告_2026-10-09.md` | 缺陷分析基底 |
| `docs/人工测试方案_2026-10-12.md` | 人工测试手册 |
| `AGENTS.md` | Agent 工作规范 |
| `docs/ORACLE_PLSQL_TO_POSTGRESQL_CONVERSION_GUIDE.md` | 转换规则手册 |

---

**最后更新**：2026-10-13
