# Oracle → PostgreSQL Pipeline 任务拆分

**来源**: PIPELINE_REVIEW_2026-09-22.md  
**生成时间**: 2026-09-22  
**更新时间**: 2026-09-23  
**状态**: 部分完成 (Phase 6 8/8 ✅, Phase 7 进行中)

---

## 已完成任务 (Phase 6)

| ID | 问题 | 状态 |
|----|------|------|
| P0-A | PACKAGE.FUNC 未改名 | ✅ 已修复 (`a6581a3`) |
| P0-C | Oracle (+) 外连接警告 | ✅ 已修复 (`d8f8f9b`) |
| P0-D | SYSDATE AI 残留 | ✅ 已修复 (`6f381f8`) |
| P0-E | tbl(idx) Oracle 数组 | ✅ 已修复 (`7b79d65`) |
| P0-F | AI BEGIN/END 不平衡 | ✅ 已修复 (`d8f8f9b`) |
| P0-G | Analyzer 注释误报 | ✅ 已修复 (本地) |
| P0-H | 路由过激 | ✅ 已修复 (`d8885a9`) |

---

## Phase 7 本周任务

| ID | 问题 | 状态 |
|----|------|------|
| P0-B | %ROWTYPE/%TYPE Schema 解析 | ✅ 已修复 |
| P0-DBMS | DBMS_OUTPUT 警告标记 | ✅ 已修复 |
| P0-EXCEPTION | EXCEPTION WHEN OTHERS 提示 | ✅ 已修复 |
| P0-ROUTING | 路由优化 | ✅ 已修复 |
| P0-NVL | NVL → COALESCE | ✅ 已修复 |

### P0-B: %ROWTYPE/%TYPE Schema 解析

**问题**: AI 转换后残留 %ROWTYPE/%TYPE，无法识别对应表/类型定义

**实现位置**: `pipeline/stages/stage3_ai/schema_builder.py` + `pipeline/stages/stage3_ai/schema_resolver.py`

**解决方案**:
1. 解析 PACKAGE_SPEC 获取类型定义
2. 解析 TABLE DDL 获取列类型
3. 在转换时查表替换

**验收标准**:
- [x] SchemaResolver 提供 `resolve_rowtype` / `resolve_type` 接口
- [x] 自动从 TABLE DDL 构建表结构映射
- [x] 从 PACKAGE SPEC 提取 RECORD/OBJECT TYPE 定义
- [x] PostProcessor 集成 SchemaResolver 自动替换

**实现状态**: ✅ 已完成
- 新增文件: `pipeline/stages/stage3_ai/schema_builder.py`
- 依赖: `pipeline/stages/stage3_ai/schema_resolver.py`
- 详见下方 Phase 9 `P2-SCHEMA: Schema 集成 %ROWTYPE/%TYPE`

---

### P0-NVL: NVL → COALESCE (PostProcessor)

**问题**: AI 路径输出中 NVL 未转换为 COALESCE

**实现位置**: `pipeline/stages/stage3_ai/post_processor.py`

**实现方式**:
```python
def _fix_nvl_to_coalesce(self, sql: str) -> str:
    """将 NVL(expr1, expr2) 转换为 COALESCE(expr1, expr2)"""
    pattern = r'\bNVL\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)'
    return re.sub(pattern, r'COALESCE(\1, \2)', sql)
```

**验收标准**:
- [x] NVL 模式被正确替换为 COALESCE
- [x] 嵌套 NVL 也能正确处理

**实现状态**: ✅ 已完成 (2026-09-23)
- 修改文件: `pipeline/stages/stage3_ai/post_processor.py`
- 新增方法: `_fix_nvl_to_coalesce(self, code: str) -> str`
- 集成到 `process()` 流程中（在 `_fix_sysdate` 之后、`_mark_outer_join_plus` 之前）
- 支持嵌套 NVL 迭代转换
- 保留 NVL2、DECODE、COALESCE 不受影响
- 新增单元测试: `pipeline/tests/test_postprocessor_nvl.py` (9 个用例全部通过)

---

### P0-DBMS: DBMS_OUTPUT 警告标记 ✅ 已修复

**问题**: Oracle 的 DBMS_OUTPUT.PUT_LINE 无等效 PG 实现

**实现方式**:
1. 检测 `DBMS_OUTPUT.PUT_LINE` 调用
2. 添加警告注释

**实现位置**: `pipeline/stages/stage3_ai/post_processor.py`

```python
def _mark_dbms_output(self, code: str) -> str:
    # 检测 DBMS_OUTPUT.PUT_LINE 调用
    pattern = r'DBMS_OUTPUT\s*\.\s*PUT_LINE\s*\([^)]*\)'
    matches = re.findall(pattern, code, re.IGNORECASE)
    
    if not matches:
        return code
    
    warning = (
        f"-- P0-DBMS WARNING: Found {len(matches)} DBMS_OUTPUT.PUT_LINE() usage(s).\n"
        f"-- PostgreSQL has no direct equivalent for DBMS_OUTPUT.PUT_LINE.\n"
        f"-- Manual implementation required (e.g., RAISE NOTICE, custom logging).\n"
    )
    return self._insert_warning(code, warning)
```

**验收标准**:
- [x] 所有 DBMS_OUTPUT 调用都有警告标记
- [x] 支持大小写不敏感检测
- [x] 支持多行/跨行的 DBMS_OUTPUT 调用

---

### P0-EXCEPTION: EXCEPTION WHEN OTHERS 提示

**问题**: Oracle 的 WHEN OTHERS 需要转换为 PG 的 GET DIAGNOSTICS

**实现位置**: `pipeline/stages/stage3_ai/post_processor.py`

**实现方式**:
```python
def _mark_when_others(self, code: str) -> str:
    """检测 WHEN OTHERS 并在文件头添加 WARNING"""
    pattern = re.compile(r'\bWHEN\s+OTHERS\s+THEN\b', re.IGNORECASE)
    matches = list(pattern.finditer(code))
    if not matches:
        return code
    count = len(matches)
    warning_lines = [
        f"-- P0-EXCEPTION WARNING: Found {count} WHEN OTHERS handler(s).",
        "-- Oracle WHEN OTHERS uses SQLCODE/SQLERRM directly;",
        "-- PostgreSQL requires GET DIAGNOSTICS for exception details.",
        "-- Please review and convert manually if needed.",
        ""
    ]
    # 在第一个非注释非空行前插入
    ...
```

**验收标准**:
- [x] WHEN OTHERS 模式有提示注释
- [x] 多个 WHEN OTHERS 正确计数
- [x] 无 WHEN OTHERS 时不添加注释

**实现状态**: ✅ 已完成 (2026-09-23)
- 修改文件: `pipeline/stages/stage3_ai/post_processor.py`
- 新增方法: `_mark_when_others(self, code: str) -> str`
- 集成到 `process()` 流程中（在 `_check_begin_end_balance` 之后）
- 策略：保守添加警告注释，不自动改写 WHEN OTHERS 块结构
- 新增单元测试: `pipeline/tests/test_postprocessor_exception.py` (5 个用例全部通过)

---

### P0-ROUTING: 路由优化 - 降低 FUNCTION 阈值 ✅ 已修复

**问题**: FUNCTION/PROCEDURE 路由阈值 (50) 过于严格，导致简单函数走 AI 路径成功率低

**实现方式**:

1. **降低阈值**: `config.yaml` 中 `stage3_threshold: 50` → `30`
   - 复杂度 < 30 且无复杂特性 → plsql_converter
   - 复杂度 >= 30 或含复杂特性 → AI Agent

2. **COMPLEX_FEATURES 确认** (已正确配置):
```python
COMPLEX_FEATURES = [
    "REF_CURSOR",        # 保留 (plsql_converter 不支持)
    "BULK_COLLECT",      # 保留
    "FORALL",            # 保留
    "DBMS_SQL",          # 保留
    "LOCK_TABLE",        # 保留
    "SAVEPOINT",         # 保留
    # 已移除 (plsql_converter 已支持):
    # "EXCEPTION_HANDLING",
    # "NESTED_SUBPROGRAM",
    # "DYNAMIC_SQL",
]
```

3. **默认值同步**: `stage3_generator.py` fallback 阈值从 70 → 30

**实现位置**:
- `pipeline/config.yaml`: `stage3_threshold: 30`
- `pipeline/stages/stage3_generator.py`: 默认阈值 fallback → 30

**验收标准**:
- [x] 简单 FUNCTION/PROCEDURE 更多走 plsql_converter 路径
- [x] plsql_converter 路径通过率保持 100%
- [x] 阈值降低到 30

---

## Phase 8 下周任务

### P1-PROMPT: AI Prompt 增强

**问题**: 当前 prompt 缺少关键转换规范

**实现位置**: `pipeline/stages/stage3_ai/tools/convert_chunk.py` (SYSTEM_PROMPT)

**缺失项**:
1. PACKAGE 命名规范强制要求 (package_method 格式) ✅
2. SYSDATE → CURRENT_DATE 示例 ✅
3. 数组访问 [idx] vs (idx) ✅
4. $$ delimiter 格式要求 ✅
5. LANGUAGE plpgsql 必须结尾 ✅

**实现方式**:
在 `SYSTEM_PROMPT` 中新增/强化：
- CRITICAL RULES 6-7: $$ delimiter 必须作为函数体 wrapper；`LANGUAGE plpgsql;` 必须是输出最后一行
- Key mappings 增加：
  - `SYSDATE → CURRENT_DATE`
  - `PACKAGE_NAME.METHOD_NAME(...) → package_name_method_name(...)` (示例: `GKAPK0030.FCTGetR(...) → kkapk0030_fctgetr(...)`)
  - `Collection/array access: Oracle name(idx) → PostgreSQL name[idx]` (示例: `g_tITEM(1) → g_tITEM[1]`)
- 明确 `Your LAST LINE must be 'LANGUAGE plpgsql;'`

**预期收益**: AI 路径通过率提升 20-30%

**验收标准**:
- [x] Prompt 包含 PACKAGE 命名规范及示例
- [x] Prompt 包含 SYSDATE → CURRENT_DATE 映射
- [x] Prompt 包含数组访问 [idx] vs (idx) 映射及示例
- [x] Prompt 明确 $$ delimiter 格式要求
- [x] Prompt 明确 LANGUAGE plpgsql 必须作为最后一行
- [x] AI 路径通过率从 15% 提升到 35% 以上 (第三轮 FUNCTION/PROCEDURE 达到 80%)

**实现状态**: ✅ Prompt 增强已完成 (2026-09-23)
- 修改文件: `pipeline/stages/stage3_ai/tools/convert_chunk.py`
- 新增单元测试: `pipeline/tests/test_convert_chunk_prompt.py` (9 个用例全部通过)
- 验证结果: 第三轮 5 个 FUNCTION/PROCEDURE 样本中 AI 路径 4 个等价、1 个部分等价，通过率 **80%**，显著高于之前的 15%

---

### P1-TOPO: 依赖拓扑排序 ✅ 已完成

**问题**: PACKAGE 转换顺序依赖人工，无拓扑排序

**实现位置**: `pipeline/stages/stage1_5_dependency_sorter.py`

**核心类**:
- `DependencySorter`: 依赖图构建和拓扑排序
- `DependencyNode`: 依赖图节点
- `TopoSortResult`: 排序结果

**实现方式**:
```
输入: PACKAGE_BODY 调用图
    PACKAGE_A → PACKAGE_B.Func
    PACKAGE_B → PACKAGE_C.Proc

输出: 转换顺序
    1. PACKAGE_C
    2. PACKAGE_B
    3. PACKAGE_A
```

**主要功能**:
1. **依赖提取**: 从 `metadata.extra["dependencies"]` 和 `call_relationships` 提取包依赖
2. **源码分析**: 从源码中提取 `PKG.MEMBER(...)` 调用模式
3. **Kahn 算法拓扑排序**: 确定正确的转换顺序
4. **循环依赖检测**: 检测并报告 A→B→A 等循环

**API 使用示例**:
```python
from stages.stage1_5_dependency_sorter import sort_by_dependencies

# 对 metadata 列表排序
sorted_metadata, result = sort_by_dependencies(metadata_list)

# 检查循环依赖
if result.circular_dependencies:
    print("警告: 发现循环依赖")
    for cycle in result.circular_dependencies:
        print(f"  {' -> '.join(cycle)}")

# 按顺序处理
for metadata in sorted_metadata:
    process(metadata)
```

**验收标准**:
- [x] 自动生成正确的转换顺序
- [x] 无循环依赖时报错提示
- [x] 正确跳过内置包 (DBMS_OUTPUT, DBMS_SQL 等)
- [x] 支持从源码提取包调用

---

### P1-JSON: 结构化输出 JSON Schema

**问题**: AI 输出格式不稳定

**实现位置**: `pipeline/stages/stage3_ai/json_output.py` + `pipeline/stages/stage3_ai/tools/convert_chunk.py`

**实现方式**:
1. 定义 `CONVERSION_RESULT_SCHEMA` JSON Schema，强制字段：`schema_version`, `code`, `object_name`, `object_kind`, `warnings`, `notes`
2. `parse_and_validate(raw_text)` 主入口：
   - 尝试解析 JSON 并验证
   - 验证 `code` 字段：必须以 CREATE 开头、含 LANGUAGE plpgsql、含 `$$`、BEGIN/END 平衡、括号平衡
   - 检测 Oracle 残留（VARCHAR2, NVL, SYSDATE, %ROWTYPE, %TYPE）
   - JSON 解析失败时降级从 markdown 代码块提取
3. `convert_chunk()` 默认 `use_json_schema=True`，prompt 中注入 `get_schema_instructions()`
4. `PLSQLAgent` 对 `status != "success"` 的 chunk 结果触发 retry

**验收标准**:
- [x] AI 输出符合预期 schema
- [x] BEGIN/END 自动校验
- [x] 括号平衡检查
- [x] Oracle 残留检测
- [x] JSON 解析失败可降级提取代码块

**实现状态**: ✅ 已完成
- 修改文件:
  - `pipeline/stages/stage3_ai/json_output.py`
  - `pipeline/stages/stage3_ai/tools/convert_chunk.py`
- 新增单元测试:
  - `pipeline/tests/test_json_output.py` (6 个用例全部通过)
  - `pipeline/tests/test_convert_chunk_json.py` (3 个用例全部通过)

---

### P2-SEQ: SEQUENCE.NEXTVAL 语法检查

**问题**: Oracle 的 sequence.NEXTVAL 在 PG 语法不同

**实现位置**: `pipeline/stages/stage3_ai/post_processor.py`

**实现方式**:
```python
def _fix_sequence_nextval(self, code: str) -> str:
    # sequence.NEXTVAL → nextval('sequence')
    code = re.sub(
        r'\b([A-Za-z_][A-Za-z0-9_]*)\.NEXTVAL\b',
        lambda m: f"nextval('{m.group(1).lower()}')",
        code, flags=re.IGNORECASE,
    )
    # sequence.CURRVAL → currval('sequence')
    code = re.sub(
        r'\b([A-Za-z_][A-Za-z0-9_]*)\.CURRVAL\b',
        lambda m: f"currval('{m.group(1).lower()}')",
        code, flags=re.IGNORECASE,
    )
    return code
```

**验收标准**:
- [x] `sequence.NEXTVAL` 被转换为 `nextval('sequence')`
- [x] `sequence.CURRVAL` 被转换为 `currval('sequence')`
- [x] 大小写不敏感
- [x] `schema.table.column` 等普通点号表达式不误伤

**实现状态**: ✅ 已完成 (2026-09-23)
- 修改文件: `pipeline/stages/stage3_ai/post_processor.py`
- 新增方法: `_fix_sequence_nextval(self, code: str) -> str`
- 集成到 `process()` 流程中（在 `_mark_when_others` 之后）
- 序列名统一转为小写（符合 PG 无引号标识符习惯）
- 新增单元测试: `pipeline/tests/test_postprocessor_sequence.py` (6 个用例全部通过)

---

### P2-RETURNING: RETURNING INTO 警告 ✅ 已完成

**问题**: Oracle 的 RETURNING INTO 在 PG 需用不同方式处理

**实现位置**: `pipeline/stages/stage3_ai/post_processor.py`

**实现方式**:
1. 检测 DML 语句中的 RETURNING INTO 子句 (INSERT/UPDATE/DELETE)
2. 提示使用 CTE 或 OUTPUT 参数替代

**检测模式**:
```python
# Oracle:
INSERT INTO table VALUES (...) RETURNING id INTO v_id;
UPDATE t1 SET col = val RETURNING col INTO v_col;
DELETE FROM t1 RETURNING * INTO v_rec;

# PG 无等效语法，需人工转换
```

**生成的警告**:
```sql
-- P2-RETURNING WARNING: Found 1 RETURNING INTO clause(s).
-- PostgreSQL does not support RETURNING INTO directly.
-- Alternatives: CTE (WITH ... RETURNING), or use function OUT parameters.
```

**验收标准**:
- [x] RETURNING INTO 有警告提示
- [x] 正确识别 INSERT/UPDATE/DELETE 中的 RETURNING INTO
- [x] 排除 SELECT RETURNING (PG 支持 SELECT RETURNING)

---

## Phase 9 月内任务

### P2-SCHEMA: Schema 集成 %ROWTYPE/%TYPE ✅ 已完成

**问题**: PostProcessor 无法转换 %ROWTYPE/%TYPE，需要 schema 信息

**前置依赖**: P0-B (Schema 解析) ✅ 已实现

**实现方式**:
1. 解析所有 TABLE DDL 建立列类型映射
2. 解析 TYPE 定义
3. 在 PostProcessor 中查表替换

**新增文件**:
- `pipeline/stages/stage3_ai/schema_builder.py` - Schema 自动构建器
- `pipeline/stages/stage3_ai/schema_resolver.py` - Schema 解析器 (已存在)

**核心功能**:

1. **SchemaResolver** (`schema_resolver.py`):
   - `add_table()` - 添加表结构
   - `parse_create_table()` - 解析 CREATE TABLE DDL
   - `ingest_package_spec()` - 解析 PACKAGE SPEC 中的 RECORD/OBJECT TYPE
   - `resolve_rowtype()` - 解析 `%ROWTYPE` → `RECORD(...)`
   - `resolve_type()` - 解析 `%TYPE` → 具体 PG 类型

2. **SchemaBuilder** (`schema_builder.py`):
   - `build_schema_resolver()` - 从项目目录构建完整 SchemaResolver
   - `create_schema_resolver_from_project()` - 便捷函数
   - `SchemaManager` - 生命周期管理器

**使用示例**:
```python
from stages.stage3_ai.schema_builder import create_schema_resolver_from_project
from stages.stage3_ai.post_processor import PostProcessor

# 1. 创建 SchemaResolver
resolver = create_schema_resolver_from_project(
    source_dir="D:/JIP/poc/gkb/..."
)

# 2. 传入 PostProcessor
pp = PostProcessor(schema_resolver=resolver)

# 3. 处理代码
result = pp.process(oracle_code)
# %ROWTYPE 和 %TYPE 会被自动替换
```

**验收标准**:
- [x] SchemaResolver 提供 resolve_rowtype / resolve_type 接口
- [x] 自动从 TABLE DDL 构建表结构映射
- [x] 从 PACKAGE SPEC 提取 RECORD/OBJECT TYPE 定义
- [x] PostProcessor 集成 SchemaResolver 自动替换

---

### P2-MULTI: AI 多轮校验

**问题**: 单次 AI 生成质量不稳定

**实现方式**:
1. 首轮生成
2. 自动验证关键模式
3. 失败时触发 retry (最多 3 次)

**验收标准**:
- [ ] 多轮校验机制正常工作
- [ ] retry 后通过率显著提升

---

### P2-INCREMENT: 增量转换策略

**问题**: 巨型 PACKAGE_BODY 一次性转换失败率高

**实现位置**: `pipeline/stages/stage3_ai/chunk_planner.py` + `pipeline/stages/stage3_ai/agent.py`

**实现方式**:
1. 为每个 chunk 计算 `complexity_score` (0-100):
   - 动态 SQL (EXECUTE IMMEDIATE / OPEN ... FOR / DBMS_SQL): +10
   - 游标 (CURSOR / FETCH): +5
   - 异常处理 (EXCEPTION / WHEN OTHERS): +5
   - DBMS_* 调用: +3
   - 嵌套子程序: +8
   - 数组/集合访问: +3
   - Oracle hint: +2
   - 每 100 行: +1
2. 新增 `ChunkPlanner.plan_incremental()` 方法，按 `complexity_score` 升序排序 chunks
3. 在 `PLSQLAgent.convert()` 中自动触发增量策略：
   - 源码行数 > 500 或 metadata complexity_score > 50 时使用 `plan_incremental`
   - 否则使用普通 `plan`

**验收标准**:
- [x] 每个 chunk 有复杂度评分
- [x] `plan_incremental()` 返回按复杂度升序排列的 chunks
- [x] 巨型文件 (>500 行或 complexity>50) 自动走增量策略
- [ ] 大文件转换成功率提升 (需批量测试验证)

**实现状态**: ✅ 核心逻辑已完成 (2026-09-23)
- 修改文件:
  - `pipeline/stages/stage3_ai/models.py` (Chunk 增加 `complexity_score`)
  - `pipeline/stages/stage3_ai/unit.py` (PlsqlUnit 增加 `complexity_score`)
  - `pipeline/stages/stage3_ai/chunk_planner.py` (新增 `_estimate_complexity` + `plan_incremental`)
  - `pipeline/stages/stage3_ai/agent.py` (自动选择增量策略)
- 新增单元测试: `pipeline/tests/test_chunk_planner_incremental.py` (4 个用例全部通过)
- 验证结果: 第三轮 2 个 PACKAGE_BODY（663/826 行）均因 AI Agent 处理挂起而超时，增量策略的 Chunk 边界与超时控制仍需优化

---

## 长期优化 (3 个月)

### L1-MODEL: AI 模型升级

| 当前 | 目标 |
|------|------|
| qwen3.8:27b | qwen3.14:27b 或更大 |
| 单次生成 | 多轮 self-correction |
| 通用 prompt | 领域微调 |

---

## 验收指标

| 指标 | 当前 (第三轮 12 样本) | Phase 7 目标 | Phase 8 目标 | 最终目标 |
|------|------------------------|--------------|--------------|----------|
| AI 路径通过率 (FUNCTION/PROCEDURE) | **80%** (4/5) | 25% | 40% | 60% |
| 规则式路径通过率 | **100%** (3/3) | 100% | 100% | 100% |
| 整体通过率 | **58.3%** (7/12) | 40% | 55% | 70% |
| PACKAGE.FUNC 残留 | 2 文件 (VIEW 中) | 0次 | 0次 | 0次 |
| SYSDATE 残留 | 0 文件 | 0次 | 0次 | 0次 |
| %ROWTYPE/%TYPE 残留 | 0 文件 | 0次 | 0次 | 0次 |
| BEGIN/END 不平衡 | 0 文件 | 0次 | 0次 | 0次 |

> **说明**: 第三轮验证中 2 个 PACKAGE_BODY 因 AI Agent 挂起被标记为超时，不计入等价；2 个 VIEW 因 `PACKAGE.FUNC` 调用未改名被判为不等价。FUNCTION/PROCEDURE 的 AI 路径表现显著提升。

---

## 关键文件索引

| 文件 | 说明 |
|------|------|
| `pipeline/core.py` | Pipeline 入口 |
| `pipeline/stages/stage3_generator.py` | 路由逻辑 |
| `pipeline/stages/stage2_analyzer.py` | 复杂度评分 |
| `pipeline/stages/stage3_ai/post_processor.py` | AI 后处理 |
| `pipeline/plsql_converter/pg_naming.py` | 命名规范 |
| `HANDOFF.md` | 项目状态追踪 |

---

*Generated: 2026-09-22*
