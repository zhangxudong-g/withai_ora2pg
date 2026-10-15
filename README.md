# Oracle → PostgreSQL 迁移工具

> 将 Oracle PL/SQL 代码转换为 PostgreSQL，达到**"一键安装 + 首调可用 + 业务等价"**的可交付状态。

[![Gate: PASSED](https://img.shields.io/badge/Gate-PASSED-brightgreen)](#)
[![Score: 99.6/A](https://img.shields.io/badge/Score-99.6%2FA-brightgreen)](#)
[![L3 Install: 12/12](https://img.shields.io/badge/L3-12%2F12-brightgreen)](#)
[![L4 Equiv: 65/65](https://img.shields.io/badge/L4-65%2F65-brightgreen)](#)

---

## 快速开始

```bash
# 1. 转换 Oracle → PG
python scripts/convert_oracle_poc.py --clean

# 2. 安装到 PostgreSQL
PGPASSWORD=postgres python scripts/install_oracle_poc.py install

# 3. 验证（五道门禁）
PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py
```

---

## 项目结构

```
withai_ora2pg/
├── ora2pg_py_perlstyle/      # 核心转换引擎（~6600行）
│   └── perlstyle_engine.py   # 唯一真实引擎
│
├── workspace/                 # 新能力实现
│   ├── rules/                # 转换规则（%TYPE/%SUBQUERY/异常...）
│   ├── agent/                # AI Agent 工具链
│   ├── parsers/              # ANTLR 结构体检
│   ├── verification/         # 验证框架（L1-L4 + 残留扫描）
│   └── tests/                # 单测（386用例）
│
├── scripts/                  # 入口脚本
│   ├── convert_oracle_poc.py # 转换
│   ├── install_oracle_poc.py # 安装
│   ├── verify_oracle_poc_output.py  # 验证
│   └── scan_oracle_residuals.py     # 残留扫描
│
├── oracle_poc/               # 业务场景：学生成绩管理系统
│   ├── 01.SQL/              # Oracle 源
│   └── output/              # PG 产物
│
└── docs/                    # 文档
```

---

## 当前状态（2026-10-15，PG 15.19 实测）

| 指标 | 结果 |
|---|---|
| **总分** | **99.6 / A** ✅ |
| **L3 安装** | **12/12 通过** ✅ |
| **L3.5 冒烟** | **120/121, 0 fail** ✅ |
| **L4 业务等价** | **65/65 (100%)** ✅ |
| **残留** | **major=0, critical=0** ✅ |
| **单测** | **385 passed + 1 skipped** ✅ |
| **Gate** | **PASSED** ✅ |

### 实施阶段（全部完成）

| 阶段 | 内容 | 状态 |
|---|---|---|
| 一 | P1-1~P1-4：静默丢失/完整性/残留/冒烟 | ✅ |
| 二 | P2-1~P2-5：%SUBQUERY/%TYPE/(+)/序列/异常 | ✅ |
| 三 | P3-1~P3-4：AST/触发器/元数据 | ✅ |
| 四 | P4-1~P4-4：Agent/TODO/复杂体/TYPE | ✅ |
| 五 | P5-1~P5-2：L4用例/端到端验证 | ✅ |
| **M4** | **工程交付级达成** | ✅ |
| 二期 | T16-T26：PG13+/性能/Agent确定性化... | ⬜ 待启动 |

---

## 技术架构

```
Oracle 源
    │
    ├─[规则引擎] perlstyle_engine（主力，4.7ms/file）
    │
    ├─[ANTLR AST] 结构体检（~11.7s/file，只检查不翻译）
    │
    └─[AI Agent] 复杂体最后一公里（validate_pg 硬校验）
```

---

## 验证门禁

| DoD | 内容 | 结果 |
|---|---|---|
| DoD-1 | 一键安装 | ✅ 0 错误 |
| DoD-2 | 残留扫描 | ✅ major=0 |
| DoD-3 | 首调冒烟 | ✅ 0 fail |
| DoD-4 | 业务等价 | ✅ 100% |
| DoD-5 | Gate ≥ 90 | ✅ 99.6/A |
| DoD-7 | 完整性 | ✅ 0 缺失 |

---

## 关键原则

1. **落点纪律**：规则改动 → `ora2pg_py_perlstyle/perlstyle_engine.py`
2. **验证先于结论**：PG 语义必须 live PG 15.19 实测
3. **产物同步**：规则修复后必须重转产物并复验

---

## 文档

| 文档 | 说明 |
|---|---|
| `HANDOFF.md` | 当前状态、接任务指南 |
| `docs/最终实施路径_生产级_2026-10-09.md` | **实施唯一权威** |
| `docs/oracle_pg_migration_guide.md` | 迁移手册（~67KB）|
| `docs/archive/` | 历史归档 |
| `AGENTS.md` | Agent 工作规范 |

---

**🎉 M4 工程交付级达成 | 2026-10-15**
