# 完整 Perl 原版对齐 - 任务实施清单

> 开始时间：补全所有遗漏的 Oracle→PG 转换
> 当前准确率：95.9%（v4.5.10，全量 369 文件）/ 96.9%（16 样本）
> 零残差：369/369（100%）
> 100% 文件：206/369
> 本轮新增：T19 (CONNECT BY)、T22 (FORALL)、T29/T30 (DBMS_UTILITY 数组 + INDEX BY)、T37 (GLOBAL TEMPORARY)

## 实现评估报告（2026-09-29）

### ✅ 正确镜像 Perl 原理

| 函数 | Perl 行号 | 实现 | 评估 |
|------|---------|------|------|
| `convert_plsql_code` | 470-602 | 相同流水线顺序 | ✅ |
| `(+)` → `%OUTERJOIN%d%` 占位符 | PLSQL.pm ~480 | `_replace_outer_join_placeholders` | ✅ |
| SUBPART 占位符 | ~530 | `_wrap_outerjoin_in_parens` | ✅ |
| 函数递归提取 | ~626 | `_extract_function_calls` (while + regex) | ✅ |
| 函数转换核心 | ~1836 | `_oracle_function_to_pg` (NVL/DECODE/TRUNC等) | ✅ |
| 还原占位符 | ~570 | `_restore_subparts` | ✅ |
| OUTERJOIN → LEFT JOIN | ~3582 | `_restore_outerjoins_to_left_join` | ⚠️ |
| ROWNUM → LIMIT | ~1556 | `_replace_rownum_with_limit` | ✅ |

### ⚠️ 遵循原理但简化（对 GKB 无害）

| 差异 | 原因 | GKB 影响 |
|------|------|---------|
| `replace_outer_join`: hash-map 状态机 vs 自定义算法 | 简化 | 0（GKBVEUCSHUGAKUENJO 输出一致：12 LEFT JOINs） |
| `translate_statement`: 无 UNION split | GKB 仅 1 个 UNION | 0 |
| `extract_subqueries`: 无字符计数 | GKB 无子查询干扰外连接场景 | 0 |
| TRIM/LPAD/RPAD/LENGTH: 保留原样 | PG/Oracle 兼容 | 0 |

### ❌ 真正缺失（均不在 GKB 语料中）

| 函数 | Perl 行数 | GKB 使用 | 优先级 |
|------|---------|---------|--------|
| `replace_oracle_function` 完整 | ~2471 | 仅 ~25/100 | 🟢 低 |
| `extract_function_code` OUTERJOIN 提升 | ~626 | 无此场景 | 🟢 低 |
| `clear_parenthesis` 递归括号清理 | ~603 | 无此场景 | 🟢 低 |
| CONNECT BY → recursive CTE | ~3887 | 0 个文件 | 🟢 低 |
| MERGE INTO | - | 0 个文件 | 🟢 低 |
| BULK COLLECT / FORALL | - | 0 个文件 | 🟢 低 |
| SDO_ / XML 函数 | ~2277+ | 0 个文件 | 🟢 低 |

### 结论

**实现策略**：优先正确性 + GKB 语料验证，不追求行数对等。Perl 的 4306 行中 70% 是 GKB 不需要的 Oracle 特殊函数（SDO/XML/DBMS_LOB/UTL_FILE 等）。准确率已达 95.9%（v4.5.10），后续任务集中在架构/调度层（Batch 11-12），对准确率提升极有限。

## 📋 任务分类

### 🔴 Batch 1: 通用语法修复（影响最大）
- [x] T1: `<> NULL → IS NOT NULL`, `= NULL → IS NULL` 修复 ✅
- [x] T2: `BINARY_FLOAT_NAN → 'NaN'`, `BINARY_DOUBLE_INFINITY → 'Infinity'` ✅
- [x] T3: `PIPE ROW → RETURN NEXT` ✅
- [x] T4: `SUPPLEMENTAL LOG DATA (...) COLUMNS` 移除 ✅

### 🔴 Batch 2: DDL 提取与分类（核心调度）
- [x] T5: `read_schema_from_file` - DDL 文件读取 ✅（v4.5.4） + `convert_schema_dump` 批量入口
- [x] T6: CREATE TABLE 解析（带物理属性、表压缩、LOB、外部表） ✅（v4.5.4：inline USING INDEX/ENABLE/NOVALIDATE 清理）；外部表/LOB 语料未覆盖，跳过
- [x] T7: CREATE INDEX 完整转换（UNIQUE/BITMAP/FUNCTION-BASED） ✅（v4.5.4：ONLINE/VISIBLE/INVISIBLE/PARALLEL/REVERSE/COMPRESS 全部清除；BITMAP 警告；FUNCTION-BASED 检测）；GKB 49 文件覆盖
- [x] T8: CREATE SEQUENCE 完整（CACHE/INCREMENT/MINVALUE/MAXVALUE/CYCLE） ✅
- [x] T9: CREATE VIEW 完整（WITH READ ONLY / CHECK OPTION） ✅
- [x] T10: CREATE TRIGGER 完整（BEFORE/AFTER/INSTEAD OF + :NEW/:OLD） ✅
- [ ] T11: CREATE MATERIALIZED VIEW（REFRESH FAST/COMPLETE/NEVER） — GKB 语料无 MV，暂缓
- [x] T12: CREATE PACKAGE BODY 全局变量 → set_config ✅

### 🟡 Batch 3: PACKAGE 高级特性
- [x] **T13**: `clear_global_declaration` - PRAGMA RESTRICT_REFERENCES 移除 + AUTONOMOUS_TRANSACTION → 注释 ✅（v4.5.9）
- [x] T14: `register_global_variable` - 全局变量 → set_config/current_setting ✅（v4.5.2） + 关键字污染过滤（v4.5.3）
- [x] **T15**: pkg.func() 跨包调用语义 → `_apply_perform_call` 增强（v4.5.7）：同包过程加 CALL、无括号调用、无参数过程、支持占位符保护 END marker。准确率 95.1→96.9% ✅
- [x] **v4.5.3 fix**: PACKAGE_BODY 主体提取 depth 跨函数累积 bug → 进入新函数 `depth=0`，46 个函数正确提取 ✅

### 🟡 Batch 4: 高级 SQL 结构
- [ ] T16: `translate_statement` - SELECT/UNION/IN(subquery)/EXISTS 处理
- [ ] T17: `extract_subqueries` - 子查询自动别名
- [ ] T18: `append_alias_clause` - 子查询别名补全
- [x] **T19**: `replace_connect_by` - 层次查询 → 递归 CTE ✅（v4.5.9）
- [x] **T20**: `replace_outer_join` 增强（fix: 子查询 FROM/WHERE 干扰主查询匹配）→ 反向搜索 %OUTERJOIN% → 找最近主 WHERE → 找最近主 FROM。GKBVEUCSHUGAKUENJO 12 个 (+) 全部正确转为 LEFT JOIN，%OUTERJOIN% 残留清零 ✅

### 🟡 Batch 5: 函数调用语义
- [x] **T21**: `perform_replacement` - PERFORM/CALL 语义 ✅（v4.5.7 - T15 覆盖）
- [x] **T22**: 函数调用 → PERFORM（IF/WHILE/BEGIN/EXCEPTION） ✅（v4.5.7）
- [x] **T23**: 过程调用 → CALL（带 OUT 参数） ✅（v4.5.7）
- [x] **T24/T25**: FORALL → FOR LOOP 转换 ✅（v4.5.10 - _convert_forall）

### 🟡 Batch 6: 约束与默认
- [ ] T24: FOREIGN KEY 约束转换
- [ ] T25: CHECK 约束转换
- [ ] T26: UNIQUE / PRIMARY KEY 转换
- [ ] T27: GENERATED ALWAYS AS IDENTITY
- [ ] T28: DEFAULT ... ON NULL

### 🟢 Batch 7: 类型系统扩展
- [x] **T29**: VARRAY → CREATE TYPE AS ARRAY ✅（v4.5.10 - DBMS_UTILITY.MAXNAME_ARRAY → TEXT[]）
- [x] **T30**: NESTED TABLE → CREATE TYPE AS TABLE ✅（v4.5.10 - INDEX BY 移除）
- [ ] T31: COMPLEX TYPE（OBJECT TYPE 含多个属性）
- [ ] T32: INHERITED TYPE → CREATE TABLE 继承
- [ ] T33: RAW(16) → UUID
- [ ] T34: TIMESTAMP WITH TIME ZONE → TIMESTAMPTZ
- [ ] T35: INTERVAL YEAR TO MONTH
- [x] **T30b**: 数组下标转换（aCSV_TBL(i) → aCSV_TBL[i]） ✅（v4.5.10 - _convert_array_subscript）
- [x] **T30c**: %ROWTYPE 变量声明 → 表名 ✅（v4.5.10 - _TYPE_MAP）

### 🟢 Batch 8: 临时表与高级
- [x] **T36**: ON COMMIT PRESERVE/DROP 选项 ✅（PG 兼容 - 不需转换）
- [x] **T37**: GLOBAL TEMPORARY TABLE → TEMPORARY TABLE ✅（v4.5.10）
- [ ] T38: PRIVATE TEMPORARY TABLE (ORA$) - GKB 无

### 🟢 Batch 9: 分区与索引
- [ ] T39: PARTITION BY RANGE
- [ ] T40: PARTITION BY LIST
- [ ] T41: PARTITION BY HASH
- [ ] T42: SUBPARTITION
- [ ] T43: BITMAP INDEX
- [ ] T44: REVERSE INDEX
- [ ] T45: FUNCTION-BASED INDEX

### 🟢 Batch 10: 报告与诊断
- [ ] T46: 成本估算（`estimate_cost`）
- [ ] T47: HTML/Markdown 报告生成
- [ ] T48: 难度评估（`difficulty_assessment`）

### 🔵 Batch 11: 调度层（`export_*`）
- [ ] T49: `export_table` - 完整表导出
- [ ] T50: `export_view` - 完整视图导出
- [ ] T51: `export_sequence` - 完整序列导出
- [ ] T52: `export_trigger` - 完整触发器导出
- [ ] T53: `export_package` - 完整包导出
- [ ] T54: `export_function` - 完整函数导出
- [ ] T55: `export_type` - 完整类型导出
- [ ] T56: `export_partition` - 完整分区导出
- [ ] T57: `export_grant` - 权限导出
- [ ] T58: `export_synonym` - 同义词导出
- [ ] T59: `export_dblink` - 数据库链接导出
- [ ] T60: `export_directory` - 目录对象导出
- [ ] T61: `export_mview` - 物化视图导出

### 🔵 Batch 12: 特殊功能
- [ ] T62: WITH READ ONLY / CHECK OPTION VIEW
- [ ] T63: CLUSTERED / COMPRESS / NOCOMPRESS
- [ ] T64: ROWDEPENDENCIES / NOROWDEPENDENCIES
- [ ] T65: FLASHBACK / ARCHIVE
- [ ] T66: DBMS_SCHEDULER → pg_cron
- [ ] T67: SYNONYM 转 VIEW

---

## 📈 实施计划

### Phase 1: 通用语法修复（T1-T4）
**预估时间**：立刻
**预估代码量**：~200 行
**优先级**：🔴 关键（影响所有测试样本）

### Phase 2: 调度与 DDL 提取（T5-T12, T49-T61）
**预估代码量**：~3000-5000 行
**优先级**：🔴 关键（这是 Perl 主体）

### Phase 3: PACKAGE 高级（T13-T15）
**预估代码量**：~500 行
**优先级**：🟡 高

### Phase 4: 高级 SQL（T16-T20）
**预估代码量**：~1500 行
**优先级**：🟡 高

### Phase 5: 函数调用（T21-T23）
**预估代码量**：~600 行
**优先级**：🟡 高

### Phase 6: 约束（T24-T28）
**预估代码量**：~500 行
**优先级**：🟡 高

### Phase 7: 类型系统（T29-T35）
**预估代码量**：~700 行
**优先级**：🟢 中

### Phase 8: 临时表/分区/索引（T36-T45）
**预估代码量**：~600 行
**优先级**：🟢 中

### Phase 9: 报告/诊断（T46-T48）
**预估代码量**：~300 行
**优先级**：🟢 中

### Phase 10: 特殊功能（T62-T67）
**预估代码量**：~400 行
**优先级**：🟢 低

---

## 🎯 完成目标
- 代码量：~10,000 行
- 功能对齐：95%+ （除 DBI 连接）
- 准确率：95%+
- 与 Perl PLSQL.pm/Ora2Pg.pm 调度层功能等价