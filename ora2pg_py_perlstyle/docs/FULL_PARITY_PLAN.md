# 全功能 Perl 原版对齐 - 实施计划

> 目标：让 Python 重写**功能上与 Perl 原版 100% 对齐**（除数据库连接 DBI）。
> 当前：P1-P11 已完成（Oracle 函数转换），准确率 92.8%。

## 现状对比

| 维度 | Perl 原版 | Python 当前 | 缺口 |
|------|----------|-----------|------|
| `Ora2Pg.pm` | 247 sub | 0（未实现调度层） | 调度、导出、报告全缺失 |
| `PLSQL.pm` | 45 sub | 部分实现 | 大量 sub 缺失 |
| `Oracle.pm` | 57 sub | 0（未实现对象提取） | DDL/DML 提取全缺失 |
| `GEOM.pm` | 21 sub | 0 | SDO 空间函数转换完全缺失 |
| `MSSQL.pm` | 57 sub | 0 | MSSQL 兼容功能缺失 |
| `MySQL.pm` | 55 sub | 0 | MySQL 兼容功能缺失 |
| 代码量 | ~14900 行 | ~2200 行 | **~15%** |
| 函数覆盖 | ~1500 行 PLSQL | ~1500 行 | 架构已对齐 |

---

## 已识别的 Perl 功能缺口

### 一、`PLSQL.pm` 缺失子例程（19 个未实现）

| Perl sub | 功能 | 优先级 | 语料需求 | Python 实现 |
|----------|------|--------|---------|--------------|
| `clear_parenthesis` | 规范化多余括号 | 低 | 通用 | ❌ 未实现 |
| `extract_function_code` | 递归提取嵌套函数 | ✅ 已实现 | - | ✅ `perlstyle_engine._extract_function_calls` |
| `append_alias_clause` | 子查询自动加别名 | **高** | 必需 | ✅ **`plsql_subs.append_alias_clause`** |
| `remove_fct_name` | 移除 END 后函数名 | 低 | 通用 | ❌ 未实现 |
| `set_error_code` | SQLCODE→SQLSTATE | **中** | 异常处理 | ✅ **`plsql_subs.set_error_code`** |
| `remove_named_parameters` | `var=>val` → `var:=val` | **高** | 函数调用 | ✅ **`plsql_subs.remove_named_parameters`** |
| `set_interval_value` | 间隔值处理 | 低 | 通用 | ❌ 未实现 |
| **`perform_replacement`** | PERFORM/CALL 语义（函数调用 → PERFORM/CALL） | **🔴 关键** | 函数过程调用 | ⚠️ `plsql_subs.perform_replacement` 实现 + perlstyle_engine 已有 `_apply_perform_call` (更完善) |
| **`translate_statement`** | SELECT/UNION/IN(subquery)/EXISTS 处理 | **🔴 关键** | 复杂查询 | ✅ **`plsql_subs.translate_statement`** |
| `remove_extra_parenthesis` | 移除多余括号 | 低 | 通用 | ❌ 未实现 |
| `find_or_parenthesis` | 定位 OR 表达式 | 低 | 通用 | ❌ 未实现 |
| `extract_subpart` | 子部分提取 | ✅ 已实现 | - | ✅ `perlstyle_engine._extract_subpart` |
| **`extract_subqueries`** | 子查询自动别名 | **🔴 关键** | 子查询 | ✅ **`plsql_subs.extract_subqueries`** |
| `replace_rownum_with_limit` | ROWNUM→LIMIT | ✅ 已实现 | - | ✅ `perlstyle_engine._replace_rownum_with_limit` |
| `convert_regex_substr` | REGEXP_SUBSTR 完整 | 中 | 复杂正则 | ❌ 未实现 |
| `convert_from_tz` | FROM_TZ 时区转换 | 低 | 时区数据 | ❌ 未实现 |
| `convert_date_format` | 日期格式串转换 | ✅ 已实现 | - | ✅ `perlstyle_engine._convert_date_format` |
| `regex_flags` | 正则 flag 转换 | ✅ 已实现 | - | ✅ |
| `replace_oracle_function` | 主函数转换器 | ✅ 已实现 | - | ✅ `perlstyle_engine._apply_function_conversion` |
| `replace_out_param_call_internal` | OUT 参数内部调用 | ✅ 已实现 | - | ✅ |
| `replace_out_param_call` | OUT 参数调用 | ✅ 已实现 | - | ✅ |
| `replace_decode` | DECODE→CASE | ✅ 已实现 | - | ✅ `perlstyle_engine._decode_to_case` |
| `replace_sys_context` | SYS_CONTEXT | **中** | 上下文函数 | ✅ **`plsql_subs.replace_sys_context`** |
| `replace_sdo_function` | SDO 空间函数 | **低**（语料无） | - | ❌ 未实现 |
| `replace_sdo_operator` | SDO 操作符 | **低**（语料无） | - | ❌ 未实现 |
| `raise_output` | RAISE NOTICE | ✅ 已实现 | - | ✅ |
| `replace_sql_type` | SQL 类型映射 | ✅ 已实现 | - | ✅ `perlstyle_engine._apply_type_mapping` |
| `replace_cursor_def` | REF CURSOR | ✅ 已实现 | - | ✅ `perlstyle_engine._convert_ref_cursor` |
| `estimate_cost` | 成本估算 | **中** | 报告 | ❌ 未实现 |
| `replace_outer_join` | (+) 外连接 | ✅ 已实现 | - | ✅ `perlstyle_engine._restore_outerjoins_to_left_join` |
| `find_associated_clauses` | 关联子句 | ✅ 已实现 | - | ✅ |
| **`replace_connect_by`** | CONNECT BY→递归 CTE | **🔴 关键** | 层次查询 | ✅ **`plsql_subs.replace_connect_by`** + `perlstyle_engine._replace_connect_by` |
| `replace_without_function` | 无函数调用修复 | 中 | 通用 | ✅ **`plsql_subs.replace_without_function`** |

**P0/P1/P2 补全状态（2026-10-08）**：

**已实现 9 个 P0/P1/P2 sub（见 `plsql_subs.py`）**：
- 🔴 P0: `extract_subqueries`, `translate_statement`, `replace_connect_by`
- 🟠 P1: `append_alias_clause`, `remove_named_parameters`
- 🟡 P2: `set_error_code`, `replace_sys_context`, `replace_without_function`

**未实现 5 个 P0/P1/P2 sub**（保持现状，不影响 oracle_poc 语料）：
- ❌ P0 🔴 `perform_replacement` 在 perlstyle 已有更完善的 `_apply_perform_call`
- ❌ P2: `convert_regex_substr`（语料复杂度不足）
- ❌ P2: `convert_from_tz`（时区数据，语料无）
- ❌ P2: `estimate_cost`（报告层，与转换无关）
- ❌ P2: `replace_sdo_function`/`replace_sdo_operator`（SDO 空间，语料无）

**保留未实现（低优先级，不在 C 范围）**：
- `clear_parenthesis` / `remove_fct_name` / `set_interval_value` /
  `remove_extra_parenthesis` / `find_or_parenthesis`
| `replace_rownum_with_limit` | ROWNUM→LIMIT | ✅ 已实现 | - |
| `convert_regex_substr` | REGEXP_SUBSTR 完整 | 中 | 复杂正则 |
| `convert_from_tz` | FROM_TZ 时区转换 | 低 | 时区数据 |
| `convert_date_format` | 日期格式串转换 | ✅ 已实现 | - |
| `regex_flags` | 正则 flag 转换 | ✅ 已实现 | - |
| `replace_oracle_function` | 主函数转换器 | ✅ 部分实现 | ~70% 已实现 |
| `replace_out_param_call_internal` | OUT 参数内部调用 | ✅ 已实现 | - |
| `replace_out_param_call` | OUT 参数调用 | ✅ 已实现 | - |
| `replace_decode` | DECODE→CASE | ✅ 已实现 | - |
| `replace_sys_context` | SYS_CONTEXT | **中** | 上下文函数 |
| `replace_sdo_function` | SDO 空间函数 | **低**（语料无） | - |
| `replace_sdo_operator` | SDO 操作符 | **低**（语料无） | - |
| `raise_output` | RAISE NOTICE | ✅ 已实现 | - |
| `replace_sql_type` | SQL 类型映射 | ✅ 已实现 | - |
| `replace_cursor_def` | REF CURSOR | ✅ 已实现 | - |
| `estimate_cost` | 成本估算 | **中** | 报告 |
| `replace_outer_join` | (+) 外连接 | ✅ 已实现 | - |
| `find_associated_clauses` | 关联子句 | ✅ 已实现 | - |
| **`replace_connect_by`** | CONNECT BY→递归 CTE | **🔴 关键** | 层次查询 |
| `replace_without_function` | 无函数调用修复 | 中 | 通用 |

### 二、`Oracle.pm` 缺失子例程（57 个全缺失）

**但大部分是 DBI 连接相关的（DB 信息提取），与"转换"无关。可以分离为"非 DB"和"DB"两类。**

| 非 DB 相关（可实现） | 数量 |
|---------------------|------|
| `_sql_type` | 1 |
| `_column_attributes` | 1 |
| `_parse_constraint` | 1 |
| `custom_type_definition` | 1 |
| `normalize_query` | 1 |
| `set_pg_relation_name` | 1 |
| `replace_cols` | 1 |
| `replace_tables` | 1 |
| `excluded_columns` | 1 |
| `is_reserved_words` | 1 |
| `quote_object_name` | 1 |
| 其他通用转换 | ~10 |

**DB 相关（必须跳过）**：
- `_db_connection`, `_table_exists`, `_get_version`, `_get_encoding`, `_schema_list`, `_table_info`, `_column_info`, `_get_tables`, `_get_views`, `_get_triggers`, `_get_functions`, `_get_packages`, `_get_types`, `_get_sequences`, `_get_partitions`, `_get_materialized_views`, `_get_synonyms`, `_get_dblink`, `_get_directory`, `_get_jobs`, `_get_privileges`, `_get_objects`, `_get_database_size`, `_get_largest_tables`, `_get_audit_queries` 等 ~40 个

### 三、`Ora2Pg.pm` 缺失子例程（247 个，调度层）

| 模块 | 数量 | 必要性 |
|------|------|--------|
| 初始化/配置（`new`, `parse_config`, `read_config`） | ~10 | 高 |
| 调度（`export_schema`, `_tables`, `_views`, `_functions`, `_procedures`, `_packages`, `_types`, `_triggers`, `_sequences`） | ~15 | 高 |
| 读取文件（`read_schema_from_file`, `read_view_from_file` 等） | ~10 | 高 |
| 写入文件（`create_export_file`, `append_export_file` 等） | ~10 | 高 |
| 转换（`_convert_function`, `_convert_type`, `_convert_declare`, `_extract_functions`, `_convert_package`） | ~10 | 高（部分）|
| 导出（`_dump_to_pg`, `_pload_to_pg`, `_send_to_pgdb`） | ~5 | **跳过**（DB 必需） |
| 报告（`_show_report`, `_show_infos`, `_get_human_cost`, `difficulty_assessment`） | ~10 | 中 |
| 工具（`logit`, `logrep`, `normalize_function_call`, `requalify_function_call`） | ~30 | 中 |
| 其他 | ~150 | 大部分可跳过 |

---

## 实施计划 - 分阶段

### 🎯 阶段 A：核心 PL/SQL 高级特性（P12-P15）

**目标**：实现 `translate_statement`, `perform_replacement`, `extract_subqueries`, `replace_connect_by`

**A1. `append_alias_clause` + `extract_subqueries`**（子查询自动加别名）
- 识别 `(SELECT ...)` 没有别名时自动加 `AS subquery_N`
- 处理 `IN (subquery)`, `EXISTS (subquery)`, `NOT EXISTS (subquery)`
- 优先级：**高**
- 复杂度：~300 行

**A2. `perform_replacement`**（PERFORM/CALL 语义）
- 检测 `func(arg)` 作为表达式 → 加 PERFORM
- 检测 `proc(arg)` 作为语句 → 加 CALL
- 处理 `IF func()`, `WHILE func()`, `BEGIN func()`, `EXCEPTION func()`
- 处理 `pkg.func()` 跨包调用
- 处理 OUT 参数：`v := proc(args)` → `CALL proc(args, v)`
- 优先级：**🔴 关键**
- 复杂度：~500 行

**A3. `replace_connect_by`**（CONNECT BY → 递归 CTE）
- `START WITH` → 起始条件
- `CONNECT BY PRIOR` → 递归关联
- `CONNECT_BY_ISCYCLE` → `depth > X` 检测
- `CONNECT_BY_ROOT` → `recursive_seed.col`
- `LEVEL` → 自增列
- `SYS_CONNECT_BY_PATH` → 字符串累积
- `ORDER SIBLINGS BY` → 保持
- 优先级：**🔴 关键**
- 复杂度：~400 行

**A4. `translate_statement`**（SELECT/UNION 翻译）
- `UNION` / `UNION ALL` 标准化
- `IN (subquery)` → `IN (SELECT ...)`
- `EXISTS` / `NOT EXISTS` 处理
- 内联视图别名
- 复杂度：~300 行

### 🎯 阶段 B：调度层 + 多种对象类型（P16-P18）

**B1. 调度层**（最小可用版）
- `detect_and_convert(sql)` 扩展：识别 CREATE OR CREATE FUNCTION/PROCEDURE/PACKAGE/TRIGGER/SEQUENCE/VIEW/TABLE/TYPE
- `convert_file()` 完整文件读写
- 一目录一文件的输出结构
- 复杂度：~200 行

**B2. CREATE SEQUENCE 转换**
- Oracle: `CREATE SEQUENCE name START WITH 1 INCREMENT BY 1 ...`
- PG: `CREATE SEQUENCE name INCREMENT BY 1 START WITH 1 ...` (基本兼容)
- 处理 `NEXTVAL`, `CURRVAL`
- 复杂度：~80 行

**B3. CREATE TRIGGER 转换**
- `BEFORE/AFTER INSERT/UPDATE/DELETE ON table` → PG 函数 + 触发器
- `:NEW`, `:OLD` → PG 兼容（NEW, OLD）
- `WHEN (condition)` 转换
- `INSTEAD OF` 触发器
- `compound trigger`（多事件）
- 复杂度：~400 行

### 🎯 阶段 C：DDL 提取与格式转换（P19-P20）

**C1. CREATE VIEW 完整转换**
- 复杂视图 + 注释保留
- `WITH READ ONLY` → PG 视图选项
- `FORCE` 移除 ✅ 已实现

**C2. CREATE INDEX 转换**
- Oracle: `CREATE INDEX name ON table(col DESC) TABLESPACE ts`
- PG: `CREATE INDEX name ON table(col DESC)` (移除 TABLESPACE)
- 位图索引、函数索引、唯一索引

**C3. CREATE TABLE 高级特性**
- 分区表 → CREATE TABLE + PARTITION OF
- IOT 表 → 普通表
- 物化视图 → 普通视图 + REFRESH
- 临时表语法差异

### 🎯 阶段 D：DBMS/UTL 包转换（P21）

**D1. DBMS_OUTPUT 全套** ✅ 部分
- `DBMS_OUTPUT.PUT` 累积到字符串
- `DBMS_OUTPUT.PUT_LINE` → RAISE NOTICE ✅
- `DBMS_OUTPUT.NEW_LINE` → RAISE NOTICE '' ✅
- `DBMS_OUTPUT.GET_LINE` → SELECT INTO

**D2. DBMS_SQL 全套**
- `DBMS_SQL.OPEN_CURSOR` → 字符串累积
- `DBMS_SQL.PARSE` → 原生 SQL
- `DBMS_SQL.EXECUTE` → 简化
- `DBMS_SQL.FETCH_ROWS` → 循环
- 完整模拟需要数百行，建议保守实现

**D3. UTL_FILE 全套**
- `UTL_FILE.FOPEN` → PG `pg_read_file` 或外部表
- `UTL_FILE.GET_LINE` → 函数
- `UTL_FILE.PUT_LINE` → COPY FROM

**D4. DBMS_RANDOM, DBMS_LOB, DBMS_UTILITY**
- 基础函数映射

### 🎯 阶段 E：MSSQL/MySQL 兼容（P22-P23）

**E1. MSSQL → PG 转换层**
- `[bracket]` 标识符 → `"quote"`
- `TOP N` → `LIMIT N`
- `IDENTITY` → `SERIAL`
- `GETDATE()` → `CURRENT_TIMESTAMP`
- `ISNULL()` → `COALESCE()`
- `DATEADD/DATEDIFF` → `date + interval`
- 优先级：**低**（企业项目使用频率低）

**E2. MySQL → PG 转换层**
- 反引号 → 双引号
- `LIMIT N OFFSET M` 兼容
- `AUTO_INCREMENT` → `SERIAL`
- `ENGINE=InnoDB` → 移除

### 🎯 阶段 F：空间数据 SDO_* 转换（P24）

**F1. SDO_GEOM.* 函数**
- `SDO_GEOM.SDO_DISTANCE` → PostGIS `ST_Distance`
- `SDO_GEOM.SDO_AREA` → `ST_Area`
- `SDO_GEOM.SDO_LENGTH` → `ST_Length`
- `SDO_GEOM.SDO_BUFFER` → `ST_Buffer`
- `SDO_GEOM.SDO_INTERSECTION` → `ST_Intersection`
- `SDO_GEOM.SDO_UNION` → `ST_Union`
- `SDO_GEOM.SDO_DIFFERENCE` → `ST_Difference`
- `SDO_GEOM.VALIDATE_GEOMETRY` → `ST_IsValid`
- 等等 30+ 核心函数
- 优先级：**低**（语料无）

### 🎯 阶段 G：高级 PL/SQL 特性（P25-P26）

**G1. RAISE_APPLICATION_ERROR**
- `RAISE_APPLICATION_ERROR(-20001, 'text')` → `RAISE EXCEPTION '%', 'text'`
- 异常代码映射

**G2. BULK COLLECT + FORALL**
- `BULK COLLECT INTO collection` → 数组累积
- `FORALL i IN 1..n` → `FOR i IN 1..n LOOP ... END LOOP`

**G3. CONNECT BY 高级**
- `CONNECT_BY_ISCYCLE` → 防环检测
- `NOCYCLE` 参数处理

### 🎯 阶段 H：报告与工具（P27-P28）

**H1. 成本估算与人类可读报告**
- `_show_report`: 生成迁移成本 HTML/Markdown 报告
- 难度评估（DIFFICULTY）
- 函数复杂度评分

**H2. 文件输出管理**
- 一目录一对象
- `EXPORT_SCHEMA` 输出 DROP 语句
- 文件权限处理

### 🎯 阶段 I：编码与兼容（P29-P30）

**I1. 编码自动检测**
- `Encode::Guess` → `chardet`
- 自动识别 CP932/EUC-JP/UTF-8

**I2. 字符集转换**
- 输出文件 BOM 处理
- `SET client_encoding`

---

## 实施优先级排序

| 阶段 | 内容 | 优先级 | 估算代码量 | 影响准确率 |
|------|------|--------|----------|----------|
| **A2** | `perform_replacement` | 🔴 关键 | 500 行 | +3-5% |
| **A1** | `extract_subqueries` | 🔴 关键 | 300 行 | +1-3% |
| **A3** | `replace_connect_by` | 🔴 关键 | 400 行 | 语料无 |
| **A4** | `translate_statement` | 🔴 关键 | 300 行 | +1-2% |
| **B1** | 调度层 | 🟡 高 | 200 行 | 必需 |
| **B2** | SEQUENCE | 🟡 高 | 80 行 | +0.5% |
| **B3** | TRIGGER | 🟡 高 | 400 行 | 语料无 |
| **C2** | INDEX | 🟢 中 | 100 行 | +0.5% |
| **C3** | 分区表 | 🟢 中 | 200 行 | 语料无 |
| **D1-D4** | DBMS/UTL 包 | 🟢 中 | 400 行 | +1% |
| **E1** | MSSQL | ⚪ 低 | 300 行 | 无 |
| **F1** | SDO 空间 | ⚪ 低 | 500 行 | 语料无 |
| **G1** | RAISE_APPLICATION_ERROR | 🟢 中 | 80 行 | +0.5% |
| **G2** | BULK COLLECT/FORALL | 🟢 中 | 200 行 | 语料无 |
| **H1** | 报告 | 🟢 中 | 200 行 | 必需 |
| **I1** | 编码检测 | 🟢 中 | 80 行 | 必需 |

**总估算新增代码量**：~4340 行
**目标最终代码量**：~6500 行（Perl PLSQL.pm 4306 行的 150%，含扩展注释）

---

## 建议实施顺序（迭代交付）

### Sprint 1：A2 perform_replacement（最关键，500 行）
- 检测函数调用语义（PERFORM/CALL）
- 处理 pkg.func() 跨包调用
- 处理 OUT 参数返回值

### Sprint 2：A1 + A4 联合（IN/EXISTS 子查询 + UNION）
- 子查询自动别名
- `IN (subquery)` 处理
- UNION/UNION ALL 标准化

### Sprint 3：B1 + B2 调度层 + SEQUENCE
- 完整 convert_file 调度
- SEQUENCE 转换

### Sprint 4：B3 + C2 TRIGGER + INDEX
- TRIGGER 完整转换
- INDEX 清理

### Sprint 5：G1 + G2 RAISE_APPLICATION_ERROR + BULK
- 异常处理
- 批量操作

### Sprint 6：H1 + I1 报告 + 编码
- 迁移报告
- 编码检测

### Sprint 7：A3 CONNECT BY（如果语料扩展）
- 递归 CTE 完整实现

### Sprint 8：D DBMS 包（精细化）

---

## 不实施的部分（明确跳过）

| 类别 | 原因 |
|------|------|
| DBI 数据库连接 | 用户明确要求排除 |
| Oracle.pm 中所有 `_get_*` 系列 | 需要 DBI |
| 数据导出到 PG（`_dump_to_pg`） | 需要 PG 连接 |
| Web 表/管理界面 | 非命令行工具 |
| MSSQL/MySQL 兼容模式 | 优先级低 |

---

## 验证方法

每完成一个 Sprint：
1. 运行 `python compare.py` 验证 GKB 16 样本
3. 验证新样本测试
5. 更新 `PERLSTYLE_COMPARISON.md` 和 `GAP_ANALYSIS.md`

**最终目标**：
- 准确率 >95%
- 代码量 ~6500 行（Perl 4300 行 PLSQL.pm + 扩展）
- 与 Perl 功能 95%+ 对齐（除 DBI）

---

## 状态追踪

| Sprint | 状态 | 准确率 | 代码量 |
|--------|------|--------|--------|
| P1-P11（已完成） | ✅ | 92.8% | 2200 |
| Sprint 1: A2 | 🔲 | - | +500 |
| Sprint 2: A1+A4 | 🔲 | - | +600 |
| Sprint 3: B1+B2 | 🔲 | - | +280 |
| Sprint 4: B3+C2 | 🔲 | - | +500 |
| Sprint 5: G1+G2 | 🔲 | - | +280 |
| Sprint 6: H1+I1 | 🔲 | - | +280 |
| Sprint 7: A3 | 🔲 | - | +400 |
| Sprint 8: D | 🔲 | - | +400 |
| **目标总计** | - | **>95%** | **~6500** |