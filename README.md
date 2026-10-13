# Oracle → PostgreSQL 迁移工具集

本仓库包含两个主要的 Oracle → PostgreSQL 转换工具，以及配套的 GKB 真实语料测试。

---

## 1. 快速选择

| 工具 | 适用场景 | 准确率 | 复杂度 |
|------|---------|------:|--------|
| **[`pipeline/`](pipeline/)** | 多阶段 AI+规则混合迁移，需要智能补全 | 70-90% | 高 |
| **[`ora2pg_py_perlstyle/`](ora2pg_py_perlstyle/)** | 纯规则式批量转换，无需 AI | **95.1%** | 中 |

如果你的需求是：
- ✅ **批量把 Oracle DDL 翻译成 PostgreSQL**（规则化、可重现）→ 用 `ora2pg_py_perlstyle/`
- ✅ **复杂 PL/SQL 业务逻辑转换，需要 AI 补全** → 用 `pipeline/`

---

## 2. Perl 风格转换器（推荐）

**位置**：`ora2pg_py_perlstyle/`
**版本**：v4.5.12（2026-09-29，全量 GKB 369 文件零残差，95.9% 准确率 + Perl 保真度 95%）
**引擎**：`perlstyle_engine.py`（约 3000 行，镜像 Perl 原版 Ora2Pg 实现）

### 2.1 快速使用

```bash
# 单文件转换
python ora2pg_py_perlstyle/perlstyle_engine.py input.sql -o output.sql

# 16 样本对比测试（与原版 Python 实现对比）
python ora2pg_py_perlstyle/compare.py

# 369 个 GKB 真实文件全量回归
cd test_reports/ora2pg_py_gkb_test
python -X utf8 test_full_gkb.py
```

### 2.2 准确率（v4.5.10）

| 类型 | 平均准确率 | 零残差 | A 等级 |
|------|----------:|------:|------:|
| TABLE | **98.4%** | 197/197 | 183 |
| VIEW | **97.0%** | 9/9 | 8 |
| FUNCTION | **98.8%** | 12/12 | 11 |
| PROCEDURE | **99.6%** | 6/6 | 6 |
| PACKAGE | 82.2%* | 70/70 | 1 |
| PACKAGE_BODY | **97.7%** | 70/70 | 67 |
| TYPE | 93.3% | 2/2 | 1 |
| DATA | 90.0%** | 3/3 | 0 |
| **平均** | **95.1%** | **369/369 (100%)** | **277** |

\* PACKAGE 简单声明，展开为 stub 过程导致行数减少；功能性正确
\*\* DATA 无 CREATE 语句，syntax_score 上限 50

### 2.3 已转换的 Oracle 特性

- ✅ 核心函数：`NVL`/`NVL2`/`DECODE`/`SYSDATE`/`SYSTIMESTAMP`/`TO_CHAR`/`TO_NUMBER`/`TO_DATE`/`SUBSTR`/`INSTR`/`TRUNC`/`ROUND`/`DBMS_OUTPUT.PUT_LINE`/`LPAD`/`RPAD`/`EXTRACT`/`ADD_MONTHS`/`LAST_DAY`/`MONTHS_BETWEEN`/`REGEXP_REPLACE`/`REGEXP_LIKE`
- ✅ 类型：`VARCHAR2`/`NVARCHAR2`/`NUMBER`/`PLS_INTEGER`/`BINARY_INTEGER`/`CLOB`/`NCLOB`/`BLOB`/`RAW`/`LONG`
- ✅ 流程：`(+)` 外连接 → `LEFT JOIN` / `ROWNUM` → `LIMIT` / `PIPE ROW` → `RETURN NEXT` / `REF CURSOR` → `REFCURSOR` / `EXECUTE IMMEDIATE` → `EXECUTE`
- ✅ DDL：`CREATE INDEX`（ONLINE/VISIBLE/PARALLEL/BITMAP 等处理）/ inline 约束 / `TABLESPACE`/`NOCACHE` 等物理属性剥离
- ✅ PACKAGE_BODY：平衡 BEGIN/END 计数器，46 个函数正确提取为独立 `CREATE OR REPLACE FUNCTION`
- ⚠️ **未实现**：`CONNECT BY` / `MERGE INTO` / `BULK COLLECT` / `FORALL` / `SDO_` 空间函数 / `XMLTYPE`（GKB 语料中均不存在，跳过）

### 2.4 文档

| 文档 | 说明 |
|------|------|
| [`ora2pg_py_perlstyle/docs/MANUAL_TESTING.md`](ora2pg_py_perlstyle/docs/MANUAL_TESTING.md) | **人工测试手册**（L1-L4 测试方法、故障排查、checklist） |
| [`ora2pg_py_perlstyle/docs/IMPLEMENTATION_PLAN.md`](ora2pg_py_perlstyle/docs/IMPLEMENTATION_PLAN.md) | 增量规则实施计划 |
| [`ora2pg_py_perlstyle/docs/FULL_PARITY_TASKS.md`](ora2pg_py_perlstyle/docs/FULL_PARITY_TASKS.md) | 完整 Perl 原版对齐任务 |
| [`test_reports/ora2pg_py_gkb_test/full_test_report.md`](test_reports/ora2pg_py_gkb_test/full_test_report.md) | **369 个文件全量测试报告** |
| [`test_reports/ora2pg_py_gkb_test/PERLSTYLE_COMPARISON.md`](test_reports/ora2pg_py_gkb_test/PERLSTYLE_COMPARISON.md) | 16 样本 Perl 风格 vs 原版对比 |

---

## 3. Pipeline（多阶段 AI + 规则）

**位置**：`pipeline/`
**架构**：Stage 0-2 解析分析 → Stage 3 路由 → 规则式 / AI Agent

### 3.1 快速使用

```bash
cd pipeline

# 解析单个文件（规则式）
python -c "
from core import MigrationPipeline
ctx = MigrationPipeline().run(file_path='../gkb/.../GKBFKGKCDGET.SQL')
print(ctx.generated_code)
"

# 批量测试
python batch_test.py --limit 5

# 启用 AI 模式
python batch_test.py --ai --limit 3
```

### 3.2 转换器

| 类型 | 转换器 | 说明 |
|------|--------|------|
| TABLE | DDL | 100% ✅ |
| VIEW | VIEW | 规则式 |
| TYPE | TYPE | 100% ✅ |
| FUNCTION/PROCEDURE | plsql_converter | 复杂度 < 50 |
| FUNCTION/PROCEDURE | AI Agent | 复杂度 ≥ 50 |
| PACKAGE_BODY | AI Agent | 强制 |

### 3.3 当前状态

- **Phase 6 完成**（7/8 P0 修复）
- **验证通过率**：规则式 100%，AI 15%
- **目标**：整体 70% 通过率

---

## 4. GKB 真实语料

**位置**：`gkb/01.release/GK_gakumu/GKB_kyoiku/DB/01.SQL/`

日本 G 教机构 GKB 系统的真实 Oracle PL/SQL 语料（CP932 编码），共 369 个 SQL 文件：

| 类型 | 文件数 | 用途 |
|------|------:|------|
| TABLE | 197 | 表 DDL（含 INDEX） |
| VIEW | 9 | 视图 |
| FUNCTION | 12 | 函数 |
| PROCEDURE | 6 | 存储过程 |
| PACKAGE | 70 | 包声明 |
| PACKAGE_BODY | 70 | 包体 |
| TYPE | 2 | 类型 |
| DATA | 3 | DML 数据 |

**转码为 UTF-8**：
```bash
cd test_reports/ora2pg_py_gkb_test
python convert_to_utf8.py
```

---

## 5. 测试

### 5.1 自动化测试

```bash
# 16 样本对比测试
python ora2pg_py_perlstyle/compare.py

# 369 全量测试
cd test_reports/ora2pg_py_gkb_test
python -X utf8 test_full_gkb.py
```

### 5.2 人工测试

参见 [`ora2pg_py_perlstyle/docs/MANUAL_TESTING.md`](ora2pg_py_perlstyle/docs/MANUAL_TESTING.md) 完整的人工测试手册：

- **L1 单元测试**：单条规则验证（约 5 分钟）
- **L2 样本测试**：16 个代表性样本（约 10 分钟）
- **L3 全量测试**：369 个 GKB 真实文件（约 5 分钟）
- **L4 端到端**：转换 → 加载 PG → 验证（约 1-2 小时）

### 5.3 测试结果位置

| 文件 | 内容 |
|------|------|
| `test_reports/ora2pg_py_gkb_test/full_test_report.md` | 全量测试 Markdown 报告 |
| `test_reports/ora2pg_py_gkb_test/full_test_results.json` | 每文件详细 JSON |
| `test_reports/ora2pg_py_gkb_test/outputs_perlstyle_full/` | 转换产物目录 |
| `test_reports/ora2pg_py_gkb_test/compare_results.json` | 16 样本对比结果 |

---

## 6. 文档索引

| 文档 | 说明 |
|------|------|
| `HANDOFF.md` | 项目状态追踪 + 当前进度 |
| `AGENTS.md` | Agent 工作规范 |
| `ARCHITECTURE.md` | 整体架构说明 |
| `RUN_ALL.md` | 批量运行所有测试 |
| `重构方案.md` | 整体重构方案 |
| `ora2pg_analysis.md` | ora2pg 工具对比分析 |
| `ora2pg_py_perlstyle/docs/MANUAL_TESTING.md` | **人工测试手册** |

---

## 7. 版本历史

| 版本 | 日期 | 关键改进 | 准确率 |
|------|------|---------|------:|
| v4.5.12 | 2026-09-29 | Perl 架构对齐：OUTERJOIN 提升 + 关键字空格 + 嵌套括号清理 | **95.9%** |
| v4.5.11 | 2026-09-29 | %ROWTYPE 保留表名（语义修复）+ 语义等价性报告 | **95.9%**（语义提升）|
| v4.5.10 | 2026-09-29 | T19 CONNECT BY + T22 FORALL + T29/T30 DBMS_UTILITY 数组 + INDEX BY + %ROWTYPE | **95.9%** |
| v4.5.9 | 2026-09-29 | T19 CONNECT BY + T13 PRAGMA 清理 + _split_top_level 多字符分隔符 | 95.4% |
| v4.5.8 | 2026-09-29 | 测试脚本修复：GLOBAL TEMPORARY TABLE + compare.py 列名修正 | 95.4% |
| v4.5.7 | 2026-09-29 | T15 同包 PERFORM/CALL + T20 LEFT JOIN 子查询干扰 + TYPE `/` 终止符 | **96.9%**（16 样本）|
| v4.5.5 | 2026-09-28 | PACKAGE_BODY depth 注释污染修复 + properly_split 检测 | 96.9% |
| v4.5.4 | 2026-09-28 | Batch 2：DDL 调度 + INDEX 转换 | 93.8% |
| v4.5.3 | 2026-09-28 | PACKAGE_BODY 主体 + 关键字污染过滤 | 71.6% → 75.8% |
| v4.5.0 | 2026-09-28 | Perl 风格重写初始版本 | 84.7% → 92.7% |

---

## 8. 常见任务

### 8.1 转换单个 Oracle 文件

```bash
# 转码（如果是 CP932 源文件）
iconv -f CP932 -t UTF-8 input.sql > input_utf8.sql

# 用 perlstyle 转换
python ora2pg_py_perlstyle/perlstyle_engine.py input_utf8.sql -o output.sql

# 查看输出
cat output.sql
```

### 8.2 转换整个目录

```bash
# 批量转换（自动识别类型）
python ora2pg_py_perlstyle/perlstyle_engine.py dump.sql --batch output_dir/
```

### 8.3 加载到 PostgreSQL

```bash
# 按依赖顺序加载
for type in TYPE TABLE VIEW FUNCTION PROCEDURE PACKAGE_BODY; do
    for f in output_dir/$type/*.sql; do
        psql -U postgres -d mydb -f "$f" 2>&1 | grep -i error
    done
done
```

### 8.4 添加新转换规则

1. 在 `perlstyle_engine.py` 找到对应函数
2. 添加新规则
3. 跑 `compare.py` 验证不退步
4. 跑 `test_full_gkb.py` 验证全量
5. 更新 `FULL_PARITY_TASKS.md` 勾选状态
6. 更新 `HANDOFF.md`

---

## 9. 贡献

- **bug 报告**：附上 `full_test_results.json` 和复现文件
- **新规则请求**：在 [`FULL_PARITY_TASKS.md`](ora2pg_py_perlstyle/docs/FULL_PARITY_TASKS.md) 添加条目
- **代码风格**：参见 `AGENTS.md`

---

**最后更新**：2026-09-29