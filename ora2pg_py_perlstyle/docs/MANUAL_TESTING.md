# 人工测试手册 — perlstyle Oracle → PostgreSQL 转换器

> **目标读者**：需要手动验证 `perlstyle_engine.py` 输出正确性的开发人员 / DBA。
> **覆盖范围**：从单文件语法检查到全量 GKB 真实语料回归。
> **测试引擎版本**：v4.5.6（2026-09-29）

---

## 1. 概述

### 1.1 为什么需要人工测试？

自动化测试（`compare.py` + `test_full_gkb.py`）能高效检查 **结构性指标**（残差、行数、语法），但**不能验证语义正确性**。例如：

| 自动化能发现 | 自动化不能发现 |
|-------------|---------------|
| `NVL` 没转 `COALESCE` | `CASE WHEN ... END` 嵌套层级错误 |
| `(+)` 没转 `LEFT JOIN` | `WHERE` 子句逻辑被破坏导致行数变化 |
| `VARCHAR2` 没转 `VARCHAR` | `RETURNS TABLE(...)` 列类型与 OUT 参数不匹配 |
| 整体行数变化 | 跨包函数调用语义错误 |

**人工测试 = 用真实 PG 实例执行转换产物**，发现自动化无法捕获的问题。

### 1.2 测试层级

| 层级 | 范围 | 工具 | 用时 | 何时 |
|------|------|------|------|------|
| **L1 单元** | 单条规则（如 NVL → COALESCE） | 命令行 | 5 min | 每次规则修改后 |
| **L2 样本** | 16 个代表性样本 | `compare.py` | 10 min | 每次引擎改动后 |
| **L3 全量** | 369 个 GKB 真实文件 | `test_full_gkb.py` | 5 min | 每次版本发布前 |
| **L4 端到端** | 转换 → 加载 PG → 执行 | PG + psql | 1-2 h | 每月一次 / 重大版本 |

---

## 2. 环境准备

### 2.1 软件要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行 perlstyle 引擎 |
| PostgreSQL | 14+ | **L4 必须**，支持 PL/Pack/REFCURSOR/JSONB 等 |
| Oracle Client | 19c+（可选） | L4 验证一致性时需要 |
| Git | 任意 | 拉取代码 |

### 2.2 一次性初始化

```bash
# 1. 克隆代码（如果还没有）
git clone <repo-url>
cd D:/JIP/poc

# 2. 准备 GKB 真实语料（如果是第一次）
#    源文件目录: D:/JIP/poc/gkb/01.release/GK_gakumu/GKB_kyoiku/DB/01.SQL/
#    编码：CP932 / Shift-JIS

# 3. 转码为 UTF-8（自动化测试已包含，但 L4 需要单独的 cp932 文件）
cd D:/JIP/poc/test_reports/ora2pg_py_gkb_test
python convert_to_utf8.py
# 产出：utf8_inputs/<类型>/<文件>.SQL

# 4. 安装 PG（仅 L4）
#    下载 EDB 或社区版，创建测试库
psql -U postgres -c "CREATE DATABASE gkb_test;"
psql -U postgres -d gkb_test -c "CREATE EXTENSION IF NOT EXISTS plpgsql;"
```

### 2.3 检查清单

```text
[ ] Python 3.10+ 安装
[ ] PG 14+ 安装并运行（仅 L4）
[ ] GKB 源文件已转码
[ ] test_reports/ora2pg_py_gkb_test/outputs_perlstyle_full/ 有产物
[ ] 最新 compare_results.json 可读
```

---

## 3. L1: 单元测试（单条规则）

**目标**：验证一条转换规则在各种输入下都正确。

### 3.1 通过命令行测试

```bash
cd D:/JIP/poc/ora2pg_py_perlstyle

# 创建测试输入文件
cat > /tmp/test_nvl.sql <<'EOF'
CREATE OR REPLACE FUNCTION TEST_NVL(i_a IN NUMBER)
RETURN VARCHAR2
IS
BEGIN
    RETURN NVL(i_a, 0);
END;
/

SELECT NVL(NULL, 'default') FROM DUAL;
EOF

# 转换
python perlstyle_engine.py /tmp/test_nvl.sql -o /tmp/test_nvl_out.sql -v

# 查看输出
cat /tmp/test_nvl_out.sql
```

**预期**：
```sql
CREATE OR REPLACE FUNCTION TEST_NVL(i_a NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN COALESCE(i_a, 0);
END;
$$;
```

### 3.2 通过 Python API 测试

```python
# test_l1.py
import sys
sys.path.insert(0, 'D:/JIP/poc/ora2pg_py_perlstyle')
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning)

from perlstyle_engine import (
    detect_and_convert,
    convert_function,
    convert_package_body,
    convert_view,
    convert_table,
    convert_type,
    convert_sequence,
    convert_index,
)

# 测试 CASE 1: NVL → COALESCE
sql = "BEGIN x := NVL(a, 0); END;"
result, _ = detect_and_convert(sql)
assert "COALESCE(a, 0)" in result, f"NVL not converted: {result}"
print("[OK] NVL -> COALESCE")

# 测试 CASE 2: TO_CHAR(SYSDATE) 嵌套
sql = "SELECT TO_CHAR(SYSDATE, 'YYYYMMDD') FROM DUAL;"
result, _ = detect_and_convert(sql)
assert "TO_CHAR(CURRENT_TIMESTAMP" in result
assert "SYSDATE" not in result
print("[OK] TO_CHAR(SYSDATE) -> TO_CHAR(CURRENT_TIMESTAMP)")

# 测试 CASE 3: PACKAGE_BODY 函数提取
with open('D:/JIP/poc/test_reports/ora2pg_py_gkb_test/utf8_inputs/PACKAGE_BODY/GKBPC00120B.SQL') as f:
    sql = f.read()
result, warns = detect_and_convert(sql)
n_fn = sum(1 for line in result.split('\n') if 'CREATE OR REPLACE FUNCTION' in line or 'CREATE OR REPLACE PROCEDURE' in line)
print(f"[INFO] PACKAGE_BODY GKBPC00120B 提取 {n_fn} 个函数/过程")
assert n_fn >= 40, f"提取函数数过少: {n_fn}"
print("[OK] PACKAGE_BODY 提取完整")
```

```bash
python test_l1.py
```

### 3.3 单元测试矩阵

| 规则 | 输入示例 | 期望输出 |
|------|---------|---------|
| NVL → COALESCE | `NVL(a, b)` | `COALESCE(a, b)` |
| NVL2 → CASE WHEN | `NVL2(x, b, c)` | `CASE WHEN x IS NOT NULL THEN b ELSE c END` |
| TO_CHAR (单参数) | `TO_CHAR(x)` | `(x)::varchar` |
| TO_CHAR (双参数) | `TO_CHAR(x, 'YYYY')` | `TO_CHAR(x, 'YYYY')` |
| TO_NUMBER → ::NUMERIC | `TO_NUMBER(x, '999')` | `(x)::NUMERIC` |
| SYSDATE → CURRENT_TIMESTAMP | `SYSDATE` | `CURRENT_TIMESTAMP` |
| SYSTIMESTAMP → CURRENT_TIMESTAMP | `SYSTIMESTAMP` | `CURRENT_TIMESTAMP` |
| (+) outer join → LEFT JOIN | `WHERE a.x = b.y(+)` | `LEFT JOIN b ON a.x = b.y` |
| DECODE → CASE WHEN | `DECODE(x, 1, 'A', 'B')` | `CASE WHEN x = 1 THEN 'A' ELSE 'B' END` |
| SUBSTR → SUBSTRING | `SUBSTR(s, 1, 5)` | `SUBSTRING(s FROM 1 FOR 5)` |
| INSTR → POSITION | `INSTR(s, sub)` | `POSITION(sub IN s)` |
| TRUNC(date) → DATE_TRUNC | `TRUNC(d, 'mm')` | `DATE_TRUNC('month', d)` |
| TRUNC(number) → TRUNC | `TRUNC(n, 2)` | `TRUNC(n, 2)` |
| DBMS_OUTPUT.PUT_LINE → RAISE NOTICE | `DBMS_OUTPUT.PUT_LINE('x')` | `RAISE NOTICE '%', 'x'` |
| ROWNUM = 1 → LIMIT 1 | `WHERE ROWNUM = 1` | `LIMIT 1` |
| ROWNUM <= N → LIMIT N | `WHERE ROWNUM <= 10` | `LIMIT 10` |
| REF CURSOR → REFCURSOR | `TYPE x IS REF CURSOR` | `REFCURSOR` (注释) |
| PIPE ROW → RETURN NEXT | `PIPE ROW(r)` | `RETURN NEXT r` |
| VARCHAR2 → VARCHAR | `VARCHAR2(100)` | `VARCHAR(100)` |
| NVARCHAR2 → VARCHAR | `NVARCHAR2(100)` | `VARCHAR(100)` |
| NUMBER(p) → BIGINT | `NUMBER(10)` | `BIGINT` |
| NUMBER(p,s) → NUMERIC(p,s) | `NUMBER(10,2)` | `NUMERIC(10,2)` |
| PLS_INTEGER → INTEGER | `PLS_INTEGER` | `INTEGER` |
| BINARY_INTEGER → INTEGER | `BINARY_INTEGER` | `INTEGER` |
| CLOB → TEXT | `CLOB` | `TEXT` |
| BLOB → BYTEA | `BLOB` | `BYTEA` |
| DATE literal | `DATE '2024-01-01'` | `'2024-01-01'::date` |
| EXCEPTION WHEN OTHERS | `WHEN OTHERS THEN` | `WHEN OTHERS THEN` (保留) |
| EXECUTE IMMEDIATE | `EXECUTE IMMEDIATE 'SQL'` | `EXECUTE 'SQL'` |

---

## 4. L2: 16 样本对比测试

**目标**：使用已知样本验证引擎准确性。

### 4.1 运行测试

```bash
cd D:/JIP/poc
python ora2pg_py_perlstyle/compare.py
```

### 4.2 解读输出

```
==============================================================================
COMPARISON SUMMARY
==============================================================================
Type         Sample       Orig%   Perl%    Delta      Grade
------------------------------------------------------------------------------
TABLE        GKBTBKLOG.SQL                   100.0%  100.0%      0.0 A->A
...
PACKAGE_BODY GKBPC00120B.SQL                  36.0%   99.4%    +63.4 F->A
...
AVERAGE                                       84.7%   95.1%    +10.4
```

**关键指标**：
- **平均准确率** ≥ 95% 为合格
- **PACKAGE_BODY complex** ≥ 95% 是核心指标
- **零残差样本数** 应 ≥ 14/16

### 4.3 人工检查清单（每个样本）

```text
□ 打开 outputs_perlstyle/<类型>/<文件>_pgperl.SQL
□ 确认 CREATE OR REPLACE FUNCTION/PROCEDURE/TABLE/VIEW/TYPE 存在
□ 确认 RETURNS 类型正确（NVARCHAR2→VARCHAR, NUMBER→NUMERIC/BIGINT）
□ 在函数体内：是否有 Oracle 残留（NVL/TO_CHAR/SYSDATE 等）
□ CURSOR ... IS SELECT 是否转为 CURSOR ... FOR SELECT
□ OUT 参数是否转为 RETURNS TABLE(...)
□ 包体是否拆分为多个 CREATE OR REPLACE FUNCTION/PROCEDURE
□ 与原版输出 outputs/<类型>/<文件>_pg.SQL 对比，看 perlstyle 是否更优
```

### 4.4 已知样本索引

| 样本 | 类型 | 源行数 | 复杂度 | 验证重点 |
|------|------|------:|--------|----------|
| `TABLE/GKBTBKLOG.SQL` | TABLE | 25 | 简单 | 基本表转换 |
| `TABLE/GKBTGAKUREIBO.SQL` | TABLE | 633 | 复杂 | 多列 + 注释 + 约束 |
| `VIEW/GKBVEUCSEIJIN.SQL` | VIEW | 42 | 简单 | (+) 外连接 |
| `VIEW/GKBVEUCGAKUREIBO.SQL` | VIEW | 397 | 复杂 | 嵌套 (+) + 子查询 |
| `FUNCTION/GKBFKIDOJYGET.SQL` | FUNCTION | 30 | 简单 | 单参数函数 |
| `FUNCTION/GKBFKHMCTRL.SQL` | FUNCTION | 906 | 复杂 | 含 (+) + OUT 参数 |
| `PROCEDURE/GKBSKGYMSHKK.SQL` | PROCEDURE | 198 | 简单 | 基础存储过程 |
| `PROCEDURE/GKBSKIDOTCH.SQL` | PROCEDURE | 791 | 复杂 | 含 (+) + 多 OUT 参数 |
| `PACKAGE/GKBPB00320.SQL` | PACKAGE | 33 | 简单 | 包声明 |
| `PACKAGE/GKBPK00010.SQL` | PACKAGE | 174 | 复杂 | 多声明 + 函数过程 |
| `PACKAGE_BODY/GKBPA00081B.SQL` | PACKAGE_BODY | 431 | 简单 | 小型包体 |
| `PACKAGE_BODY/GKBPC00120B.SQL` | PACKAGE_BODY | 8741 | 复杂 | 46 函数 + 全局变量 |
| `TYPE/GKBEKENSU_LIST.SQL` | TYPE | 3 | 简单 | VARRAY 类型 |
| `TYPE/GKBEKENSU_RECORD.SQL` | TYPE | 7 | 简单 | RECORD 类型 |
| `DATA/Del_Data.sql` | DATA | 56 | 简单 | DELETE 语句 |
| `DATA/INS_GKBTTSUCHISHOKANRI.SQL` | DATA | 1959 | 复杂 | 大量 INSERT |

---

## 5. L3: 全量 GKB 真实语料测试

**目标**：369 个文件一次性回归，捕捉边界 case。

### 5.1 运行测试

```bash
cd D:/JIP/poc/test_reports/ora2pg_py_gkb_test
python -X utf8 test_full_gkb.py
```

**输出位置**：
- 单文件：`outputs_perlstyle_full/<类型>/<文件>`
- JSON 详情：`full_test_results.json`
- Markdown 报告：`full_test_report.md`

### 5.2 解读报告

查看 `full_test_report.md` 的关键部分：

| 章节 | 关注点 |
|------|--------|
| 1. 执行摘要 | 平均准确率、零残差、A 等级数 |
| 2. 按类型结果 | 哪类对象的平均分最低？是否合理？ |
| 3. 全语料残差模式 Top 10 | 哪些 Oracle 模式还有残留？ |
| 4. 残差最多的文件 Top 20 | 具体看每个文件分析 |
| 6. 各类型低准确率文件 | 是否合理（PACKAGE 简化算分） |

### 5.3 人工抽检

每次大改后，建议从以下 5 类样本中各抽 2-3 个：

```bash
# 抽检大包体
ls outputs_perlstyle_full/PACKAGE_BODY/ | head -5

# 抽检视图
ls outputs_perlstyle_full/VIEW/

# 抽检函数
ls outputs_perlstyle_full/FUNCTION/ | head -5
```

**对每个抽检样本**：
1. 打开转换产物
2. 在源文件（`utf8_inputs/...`）中找出关键逻辑（SELECT, IF, LOOP）
3. 确认转换后逻辑等价

### 5.4 性能检查

测试完成后查看耗时：

```bash
cat full_test_results.json | python -c "
import json, sys
data = json.load(sys.stdin)
times = [r['elapsed_s'] for r in data if r['ok']]
print(f'总耗时: {sum(times):.2f}s')
print(f'平均: {sum(times)/len(times)*1000:.1f}ms/文件')
print(f'最大: {max(times)*1000:.1f}ms')
print(f'P95: {sorted(times)[int(len(times)*0.95)]*1000:.1f}ms')
"
```

**预期**：
- 平均 < 500ms/文件
- 最大 < 30s/文件（PACKAGE_BODY 大文件）
- 总耗时 < 3 分钟

---

## 6. L4: 端到端 PG 执行测试

**目标**：将转换产物加载到真实 PG 中执行，验证语义正确性。

### 6.1 准备 PG 测试库

```bash
# 创建专用测试库
psql -U postgres <<'EOF'
DROP DATABASE IF EXISTS gkb_pgperl;
CREATE DATABASE gkb_pgperl;
\c gkb_pgperl
CREATE EXTENSION IF NOT EXISTS plpgsql;
EOF
```

### 6.2 加载转换产物

```bash
# 按类型顺序加载（依赖顺序：TYPE → TABLE → VIEW → FUNCTION/PROCEDURE → PACKAGE_BODY）
for t in TYPE TABLE VIEW FUNCTION PROCEDURE; do
    cd D:/JIP/poc/test_reports/ora2pg_py_gkb_test/outputs_perlstyle_full/$t
    for f in *.SQL *.sql; do
        if [ -f "$f" ]; then
            psql -U postgres -d gkb_pgperl -v ON_ERROR_STOP=1 -f "$f" 2>&1 | tee /tmp/load_$t.log
        fi
    done
done

# PACKAGE_BODY 最后（依赖 PACKAGE 的全局变量）
cd D:/JIP/poc/test_reports/ora2pg_py_gkb_test/outputs_perlstyle_full/PACKAGE_BODY
for f in *.SQL; do
    psql -U postgres -d gkb_pgperl -v ON_ERROR_STOP=1 -f "$f" 2>>/tmp/load_pkgbody.log
done
```

### 6.3 错误分析

加载失败时，PSQL 会报语法错误。常见错误及处理：

| 错误模式 | 原因 | 修复方向 |
|---------|------|----------|
| `syntax error at or near "("` | 函数调用语法问题 | 检查 `EXECUTE IMMEDIATE` 等 |
| `column "x" does not exist` | 包全局变量替换错误 | 调整 `_apply_package_global_subs` |
| `function FUNC_NAME does not exist` | 跨包调用未解析 | 需要手动建依赖 |
| `type "REFCURSOR" does not exist` | REF CURSOR 转换丢失 | 检查 `_convert_ref_cursor` |
| `permission denied` | PG 用户权限不足 | 用 superuser 加载 |

### 6.4 功能验证脚本

加载成功后，运行 PG 验证 SQL：

```sql
-- 测试基本函数调用
SELECT GKBFKIDOJYGET(1) FROM (VALUES (1)) AS t;

-- 测试 OUT 参数函数
SELECT * FROM GKBFKHMCTRL(1, 2, 3);

-- 测试包函数
SELECT GKBPC00120.FUNC_GET_JRENKEI_WK(1, 1);

-- 测试游标
SELECT * FROM GKBPC00120.GKBPB00130B(1);
```

### 6.5 数据完整性对比

如果有 Oracle 测试库，可对比：

```bash
# Oracle 端
sqlplus user/pass@oracle <<'EOF'
SELECT count(*) FROM GKBTBKLOG WHERE CREATED_DATE > '2024-01-01';
EOF

# PG 端
psql -U postgres -d gkb_pgperl -c "SELECT count(*) FROM GKBTBKLOG WHERE CREATED_DATE > '2024-01-01';"
```

差异应在 < 1%（时区/精度导致）。

---

## 7. 回归测试

### 7.1 性能回归

每次修改后记录耗时：

```bash
# 记录本次基准
cd D:/JIP/poc/test_reports/ora2pg_py_gkb_test
python -X utf8 test_full_gkb.py > /tmp/perf_$(date +%Y%m%d).log 2>&1
grep "Processed" /tmp/perf_*.log
```

**预期**：耗时变化 < 30%

### 7.2 准确率回归

每次修改后跑 L2 + L3，对比平均准确率：

```bash
# v4.5.6 基准：95.1%
# 若新版本 < 94%，需要分析退步原因
```

### 7.3 输出差异对比

```bash
# 对比新版与上一版输出
diff -r outputs_perlstyle_full/ outputs_perlstyle_full_$(date -d "yesterday" +%Y%m%d)/ > /tmp/diff_$(date +%Y%m%d).log
```

如果差异超过 10%，需要人工审查。

---

## 8. 故障排查指南

### 8.1 引擎崩溃

```bash
# 单独运行可疑文件
python perlstyle_engine.py utf8_inputs/PACKAGE_BODY/SUSPECT.SQL -o /tmp/out.SQL
```

### 8.2 内存占用高

大 PACKAGE_BODY 可能占用大量内存。可设置：

```bash
export PYTHONHASHSEED=0
ulimit -v 2097152  # 2GB
python perlstyle_engine.py big_package_body.sql -o out.sql
```

### 8.3 编码问题

GKB 源文件是 CP932。perlstyle 引擎**只能处理 UTF-8**。如果直接传 CP932 会乱码：

```bash
# 错误示范
python perlstyle_engine.py original_cp932.sql -o out.sql  # 输出乱码

# 正确做法
python convert_to_utf8.py  # 先转码
python perlstyle_engine.py utf8_inputs/.../file.SQL -o out.sql
```

### 8.4 残差诊断流程

```text
1. 看 full_test_report.md 的 Top 10 残差
2. 对残差文件，grep 查找实际位置
4. 推断是哪种规则未覆盖
5. 在 perlstyle_engine.py 找对应函数，添加/修复
6. 重跑 test_full_gkb.py 验证修复
```

### 8.5 提取函数数量异常

```bash
# 查看提取了多少函数
head -5 outputs_perlstyle_full/PACKAGE_BODY/<文件>.SQL
# 例如：
# Extracted 46 functions/procedures from PACKAGE BODY GKBPC00120
```

如果数字明显少于源代码中实际函数数：

```bash
# 查看源文件中 FUNCTION/PROCEDURE 数量
grep -cE "^\s*(FUNCTION|PROCEDURE)" utf8_inputs/PACKAGE_BODY/<文件>.SQL
```

**常见原因**：
- 注释里的 END 被错误识别（已修复，见 v4.5.5）
- DECLARE 段 END; 被误判为函数结束（已修复，见 v4.5.6）
- 嵌套 BEGIN/END 平衡错误

---

## 9. 测试结果记录模板

每次测试完成后，复制以下模板填入 `test_reports/ora2pg_py_gkb_test/TEST_LOG.md`：

```markdown
## 测试记录 YYYY-MM-DD

### 环境
- perlstyle 引擎版本: v4.5.X (commit: xxxx)
- Python: 3.12.x
- PG: 16.x（仅 L4）
- 测试人员: [请填写]

### L1 单元测试
- 通过: X/Y
- 新增规则: [列出]
- 失败用例: [列出 + 原因]

### L2 样本测试
- 平均准确率: XX.X%
- 零残差样本: X/16
- A 等级样本: X/16
- 退步样本: [列出 + 文件名]

### L3 全量测试
- 平均准确率: XX.X%
- 零残差文件: X/369
- 异常文件: X
- 最大耗时: X 秒
- 新发现残差模式: [列出]

### L4 端到端测试（如执行）
- 加载成功率: X%
- 函数调用测试通过率: X/Y
- 数据对比差异: < X%
- 阻塞问题: [列出]

### 决策
- [ ] 可以发布
- [ ] 需要修复: [列出]
- [ ] 需要回归测试
```

---

## 10. 附录

### 10.1 Oracle ↔ PostgreSQL 数据类型对照

| Oracle | PostgreSQL | 备注 |
|--------|-----------|------|
| `VARCHAR2(n)` | `VARCHAR(n)` | PG 中 VARCHAR 长度限制为字符数 |
| `NVARCHAR2(n)` | `VARCHAR(n)` | 同上 |
| `NUMBER(p)` | `BIGINT` | p ≤ 18 |
| `NUMBER(p,s)` | `NUMERIC(p,s)` | |
| `NUMBER` | `NUMERIC` | 不指定精度 |
| `PLS_INTEGER` | `INTEGER` | PG INTEGER 4 字节 |
| `BINARY_INTEGER` | `INTEGER` | |
| `CLOB` | `TEXT` | |
| `NCLOB` | `TEXT` | |
| `BLOB` | `BYTEA` | |
| `RAW(n)` | `BYTEA` | |
| `LONG` | `TEXT` | PG 中已废弃 |
| `LONG RAW` | `BYTEA` | |
| `DATE` | `TIMESTAMP` | PG DATE 仅有日期 |
| `BOOLEAN` | `BOOLEAN` | |
| `REF CURSOR` | `REFCURSOR` | |

### 10.2 Oracle ↔ PostgreSQL 函数对照

| Oracle | PostgreSQL | 备注 |
|--------|-----------|------|
| `NVL(x, y)` | `COALESCE(x, y)` | |
| `NVL2(x, y, z)` | `CASE WHEN x IS NOT NULL THEN y ELSE z END` | |
| `DECODE(x, ...)` | `CASE WHEN ...` | 嵌套 |
| `SYSDATE` | `CURRENT_TIMESTAMP` | |
| `SYSTIMESTAMP` | `CURRENT_TIMESTAMP` | |
| `LOCALTIMESTAMP` | `LOCALTIMESTAMP` | |
| `TO_CHAR(d, fmt)` | `TO_CHAR(d, fmt)` | 格式串需调整 |
| `TO_NUMBER(s, fmt)` | `(s)::NUMERIC` | PG 无格式 |
| `TO_DATE(s, fmt)` | `TO_TIMESTAMP(s, fmt)::date` | |
| `SUBSTR(s, x, y)` | `SUBSTRING(s FROM x FOR y)` | |
| `INSTR(s, sub)` | `POSITION(sub IN s)` | |
| `TRUNC(d, fmt)` | `DATE_TRUNC(fmt, d)` | |
| `TRUNC(n, x)` | `TRUNC(n, x)` | |
| `ROUND(d, fmt)` | `DATE_TRUNC(fmt, d)` | 近似 |
| `ROUND(n, x)` | `ROUND(n, x)` | |
| `LPAD/RPAD` | `LPAD/RPAD` | |
| `LTRIM/RTRIM` | `LTRIM/RTRIM` | |
| `REPLACE` | `REPLACE` | |
| `REGEXP_REPLACE(s, p, r)` | `regexp_replace(s, p, r, 'g')` | |
| `REGEXP_LIKE(s, p)` | `(s ~ p)` | |
| `EXTRACT(YEAR FROM d)` | `EXTRACT(YEAR FROM d)` | |
| `ADD_MONTHS(d, n)` | `d + (n || ' months')::INTERVAL` | |
| `LAST_DAY(d)` | `(date_trunc('month', d) + INTERVAL '1 month - 1 day')::DATE` | |
| `MONTHS_BETWEEN(d1, d2)` | 自定义 PL/PgSQL 函数 | |
| `DBMS_OUTPUT.PUT_LINE(s)` | `RAISE NOTICE '%', s` | |
| `ROWNUM = 1` | `LIMIT 1` | |
| `ROWNUM <= N` | `LIMIT N` | |
| `PIPE ROW(r)` | `RETURN NEXT r` | |
| `EXECUTE IMMEDIATE` | `EXECUTE` | |
| `(+) outer join` | `LEFT JOIN` | |

### 10.3 常见错误对照

| Oracle | PG 报错 | 原因 |
|-------|----------|------|
| `||` 字符串拼接 | 不会报错 | PG 支持 |
| `DECODE` | function does not exist | 已转换 |
| `(+)` | syntax error | 已转换 |
| `NVL` | function does not exist | 已转换 |
| `SYSDATE` | column does not exist | 已转换 |
| `VARCHAR2` | type does not exist | 已转换 |
| `NUMBER` | type does not exist | 已转换 |
| `PLS_INTEGER` | type does not exist | 已转换 |
| `DBMS_OUTPUT.PUT_LINE` | package does not exist | 已转换 |
| `CLOB` | type does not exist | 已转换 |
| `BLOB` | type does not exist | 已转换 |
| `RAW(16)` | type does not exist | 已转换 |
| `REF CURSOR` | type does not exist | 已转换 |
| `CONNECT BY` | syntax error | **未实现**（GKB 语料无此用法）|
| `MERGE INTO` | syntax error | **未实现**（GKB 语料无此用法）|
| `BULK COLLECT` | syntax error | **未实现**（GKB 语料无此用法）|
| `FORALL` | syntax error | **未实现**（GKB 语料无此用法）|

### 10.4 测试数据集统计

| 数据集 | 文件数 | 源行数 | 用途 |
|--------|------:|------:|------|
| 16 样本 | 16 | ~17k | L2 快速对比 |
| GKB 真实语料 | 369 | ~188k | L3 全量回归 |
| 临时测试 | 任意 | - | L1/L4 单条规则验证 |

---

## 11. 联系与反馈

- **bug 报告**：在 git issue 中附上 `full_test_results.json` 和复现文件
- **新规则请求**：在 FULL_PARITY_TASKS.md 中添加条目
- **问题咨询**：参考 HANDOFF.md 中的当前状态

**最近更新**：2026-09-29（v4.5.6 全量零残差达成）