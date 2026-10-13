# 增量补齐 Perl 规则 - 实施计划

> 基于 GKB 语料实际使用情况，按优先级分 11 个阶段实施。
> 每个阶段完成后立即运行 `compare.py` 验证准确率提升。
>
> **当前准确率：93.8%（v4.5.4）**
> - v4.5.3：+1.0%（PACKAGE_BODY complex 36% → 71.6%，depth 跨函数 bug + 关键字污染 + 残差测量改进）
> - v4.5.4：Batch 2 完成（INDEX 转换、inline 约束清理、批量 DDL 调度）

## 优先级排序依据

GKB 测试语料统计（utf8_inputs/）：

| 函数/特性 | 使用次数 | 优先级 |
|----------|---------|--------|
| NVL | 2679 | ✅ 已实现 |
| TO_CHAR | 2531 | 🔥 P1 |
| TO_NUMBER | 877 | 🔥 P2 |
| DECODE | 136 | 🔥 P3 |
| SYSTIMESTAMP | 130 | ⚡ P4 |
| LPAD | 126 | ⚡ P5 |
| TRANSLATE | 80 | ⚡ P6 |
| DBMS_OUTPUT | 65 | ⚡ P7 |
| TRUNC | 50 | ⚡ P8 |
| LTRIM | 50 | ⚡ P8 |
| ROWNUM (文件数) | 19 | 🔶 P9 |
| REF CURSOR (文件数) | 37 | 🔶 P10 |
| REGEXP_REPLACE | 6 | ⚪ P11 |
| EXTRACT | 6 | ⚪ P11 |
| CONNECT BY | 0 | ➖ 跳过 |
| MERGE INTO | 0 | ➖ 跳过 |
| TRIGGER | 0 | ➖ 跳过 |
| RAISE_APPLICATION_ERROR | 0 | ➖ 跳过 |
| BULK COLLECT | 0 | ➖ 跳过 |
| FORALL | 1 | ➖ 最后 |

---

## 任务拆分

### 阶段 P1: TO_CHAR 完整转换

**目标**：Oracle TO_CHAR(date, fmt) → PG to_char(date, fmt_pg)

**实现要点**：
- 映射 Oracle 格式串 → PG 格式串
  - `YYYY` → `YYYY`, `YY` → `YY`
  - `MM` → `MM`, `MON` → `MON`, `MONTH` → `MONTH`
  - `DD` → `DD`, `DAY` → `DAY`, `DY` → `DY`
  - `HH24` → `HH24`, `HH12` → `HH12`
  - `MI` → `MI`, `SS` → `SS`
  - `AM`/`PM` → `AM`/`PM`
  - `FM` → 去除前导零（无 PG 等价，保留）
  - `TZR`/`TZH:TZM` → `TZH:TZM`
  - `J`, `IW`, `WW`, `Q` 等保留

**测试用例**：
- `TO_CHAR(SYSDATE, 'YYYYMMDD')` → `TO_CHAR(CURRENT_TIMESTAMP, 'YYYYMMDD')`
- `TO_CHAR(SYSDATE, 'YYYY/MM/DD HH24:MI:SS')` → 完整保留

**验证方法**：
```python
python compare.py  # 看 PACKAGE_BODY 复杂样本提升
```

---

### 阶段 P2: TO_NUMBER 完整支持

**目标**：TO_NUMBER(num, fmt) → PG

**实现要点**：
- 移除 fmt 参数（PG 不支持）
- 简单 `TO_NUMBER(x)` → `CAST(x AS NUMERIC)`

---

### 阶段 P3: DECODE 完整实现

**目标**：完整 DECODE → CASE WHEN

**实现要点**：
- 嵌套 DECODE → 嵌套 CASE
- 缺省分支处理
- 保持 1:1 语义

**示例**：
```sql
DECODE(x, 1, 'A', 2, 'B', 'C')
→
CASE WHEN x = 1 THEN 'A' WHEN x = 2 THEN 'B' ELSE 'C' END
```

---

### 阶段 P4: SYSTIMESTAMP/CURRENT_TIMESTAMP/LOCALTIMESTAMP

**目标**：Oracle 时间函数 → PG 等价

- `SYSTIMESTAMP` → `CURRENT_TIMESTAMP`
- `CURRENT_TIMESTAMP` (Oracle 已有) → `CURRENT_TIMESTAMP` (无变化)
- `LOCALTIMESTAMP` → `LOCALTIMESTAMP`
- `SYSDATE` → `CURRENT_TIMESTAMP`（已实现，需统一）

---

### 阶段 P5: 字符串函数

**目标**：LPAD/RPAD/LTRIM/RTRIM/TRANSLATE

- `LPAD(s, n [, p])` → `LPAD(s, n [, p])` (已兼容)
- `RPAD(s, n [, p])` → `RPAD(s, n [, p])` (已兼容)
- `LTRIM(s [, set])` → `LTRIM(s [, set])` (已兼容)
- `RTRIM(s [, set])` → `RTRIM(s [, set])` (已兼容)
- `TRANSLATE(s, from, to)` → 自定义 PG 函数（PG `TRANSLATE` 行为不同）
  - Oracle: 字符级别单字符替换
  - PG: 字符串级别替换（等价于 REPLACE，但有差异）
  - 需要实现 PL/pgSQL 函数或 CASE 表达式

---

### 阶段 P6: TRUNC/ROUND 完整实现

**目标**：TRUNC/ROUND(date|number)

- `TRUNC(date [, fmt])` → `DATE_TRUNC(fmt, date)`
- `TRUNC(number [, n])` → `TRUNC(number::NUMERIC, n)`
- `ROUND(number [, n])` → `ROUND(number::NUMERIC, n)`
- `ROUND(date [, fmt])` → `DATE_TRUNC(fmt, date) + INTERVAL '1 ...' - INTERVAL '1 ...'`

---

### 阶段 P7: DBMS_OUTPUT → RAISE NOTICE

**目标**：DBMS_OUTPUT.PUT_LINE(s) → RAISE NOTICE '%', s

**实现要点**：
- 占位符协议：保护字符串中的引号
- `PUT`、`PUT_LINE`、`NEW_LINE` 全部映射
- RAISE NOTICE 仅支持单条，要处理缓冲区语义（近似）

---

### 阶段 P8: ROWNUM → LIMIT

**目标**：Oracle ROWNUM 限制 → PG LIMIT

**实现要点**：
- `WHERE ROWNUM <= N` → `LIMIT N`（需包装子查询）
- `WHERE ROWNUM < N` → `LIMIT N-1`
- `WHERE ROWNUM = 1` → `LIMIT 1`
- `AND ROWNUM <= N` 配合其他条件时需包装 SELECT

---

### 阶段 P9: REF CURSOR → REFCURSOR

**目标**：SYS_REFCURSOR/REF CURSOR → REFCURSOR

**实现要点**：
- 类型声明转换
- 游标变量赋值 `cur SYS_REFCURSOR` → `cur REFCURSOR`

---

### 阶段 P10: REGEXP_REPLACE 完整实现

**目标**：REGEXP_REPLACE(src, pattern, replace [, flags]) → REGEXP_REPLACE

**实现要点**：
- 大部分语义 PG 已兼容
- 需要差异处理：Oracle `position`/`occurrence`/`match_param` 参数

---

### 阶段 P11: EXTRACT + ADD_MONTHS

**目标**：EXTRACT/YEAR FROM date 等

- `EXTRACT(YEAR FROM x)` → `EXTRACT(YEAR FROM x)`（已兼容）
- `ADD_MONTHS(d, n)` → 自定义函数或 `d + (n || ' months')::INTERVAL`
- `LAST_DAY(d)` → `(date_trunc('month', d) + INTERVAL '1 month - 1 day')::DATE`
- `MONTHS_BETWEEN(d1, d2)` → PG 无等价，需要自定义
- `NEXT_DAY(d, day)` → PG 无等价，需要自定义

---

## 跳过（语料中无）

- CONNECT BY 递归查询
- MERGE INTO
- TRIGGER 转换
- RAISE_APPLICATION_ERROR
- BULK COLLECT
- FORALL（仅 1 处，建议手工修复）
- SDO_/XMLTYPE（企业项目，语料中无）

---

## 验证策略

每个阶段完成后立即运行：

```bash
cd D:/JIP/poc/ora2pg_py_perlstyle
python compare.py
```

预期效果：
- P1: PACKAGE_BODY 复杂样本 +10%
- P2: PACKAGE_BODY 复杂样本 +5%
- P3: PACKAGE_BODY 复杂样本 +5%
- P4-P11: 综合 +5-10%

---

## 文档同步

每个阶段完成更新：
- `D:/JIP/poc/HANDOFF.md` - 当前阶段、进度
- `D:/JIP/poc/test_reports/ora2pg_py_gkb_test/PERLSTYLE_COMPARISON.md` - 测试结果
- `D:/JIP/poc/ora2pg_py_perlstyle/docs/IMPLEMENTATION_PLAN.md` - 勾选完成项

---

## 当前状态

| 阶段 | 状态 | 详情 |
|------|------|------|
| P1: TO_CHAR | ✅ 完成 | 格式串保留，RR/FF 转换，嵌套调用递归处理 |
| P2: TO_NUMBER | ✅ 完成 | 移除格式参数，::NUMERIC 转换 |
| P3: DECODE 完整 | ✅ 完成 | 完整 CASE WHEN 嵌套转换 |
| P4: 时间戳函数 | ✅ 完成 | SYSTIMESTAMP/SYSDATE → CURRENT_TIMESTAMP |
| P5: 字符串函数 | ✅ 完成 | NVL→COALESCE, SUBSTR→SUBSTRING, INSTR→STRPOS |
| P6: TRUNC/ROUND | ✅ 完成 | TRUNC(date)→DATE_TRUNC, TRUNC(num)→TRUNC |
| P7: DBMS_OUTPUT | ✅ 完成 | DBMS_OUTPUT.PUT_LINE → RAISE NOTICE |
| P8: ROWNUM | ✅ 完成 | `ROWNUM = 1` → `LIMIT 1`, `ROWNUM <= N` → `LIMIT N`, `ROWNUM < N` → `LIMIT N-1`, `ROWNUM > N` → `OFFSET N`, `ROWNUM >= N` → `OFFSET N-1 LIMIT ALL` |
| P9: REF CURSOR | ✅ 完成 | `TYPE xxx IS REF CURSOR` → REFCURSOR, `CURSOR IS SELECT` → `FOR SELECT`, `SYS_REFCURSOR` → REFCURSOR |
| P10: REGEXP_REPLACE | ✅ 完成 | `REGEXP_REPLACE` → `regexp_replace`（加 `'g'` flag），`REGEXP_LIKE` → `regexp_match IS NOT NULL` |
| P11: EXTRACT | ✅ 完成 | EXTRACT（保持） + ADD_MONTHS + LAST_DAY + MONTHS_BETWEEN |

**当前准确率**：93.8%（v4.5.4）—— 详见 HANDOFF.md

### 技术实现说明

**内层函数递归转换架构**：
- `_oracle_function_to_pg()` 调用 `_convert_function_recursive()` 处理基础模式（NVL/SYSDATE等）
- `_oracle_function_to_pg()` 调用 `_convert_inner_functions()` 递归处理复杂嵌套（TO_DATE套TO_CHAR等）
- 使用独立 `ConversionState()` + 深度限制避免无限递归
- 内层函数先转换，外层函数再使用已转换的实参

**已知限制**：
- `TO_NUMBER(TO_CHAR(x,'fmt'))` → `(TO_CHAR(x,'fmt'))::NUMERIC` 有冗余 cast（语法有效但可优化）
- TO_CHAR 单参数 → `(arg)::varchar`，多参数 → `TO_CHAR(arg, 'fmt')`（PG 格式串兼容保留）
- TRUNC/ROUND 带日期格式时使用 DATE_TRUNC（近似值，非精确四舍五入）