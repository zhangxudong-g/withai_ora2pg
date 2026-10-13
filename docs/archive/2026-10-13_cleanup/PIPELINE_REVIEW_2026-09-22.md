# Oracle → PostgreSQL Pipeline Review & Optimization

**Date**: 2026-09-22
**Phase**: Phase 6 修复完成 (7/8)
**Review Focus**: 整体架构 + 验证结果分析 + 优化路线图

---

## 1. 整体架构评估

### 当前架构 (6 Stage Pipeline)

```
Stage 0: Preprocessor  → 编码处理 (CP932/UTF-8/BOM)
Stage 1: Parser       → ANTLR4 解析 + 元数据提取
Stage 2: Analyzer      → 复杂度评分 + 路由决策
Stage 3: Generator     → [路由] DDL/VIEW/TYPE/plsql_converter/AI Agent
Stage 4: (缺失)       → 后处理 PostProcessor (独立模块)
Stage 5: Validator     → 5 级验证 (语法/依赖/结构/语义/业务)
```

### 核心问题概览

| 层级 | 问题 | 影响 |
|------|------|------|
| 路由 | AI 路径输出质量不稳定 | 70% PACKAGE_BODY 失败 |
| PostProcessor | 分散在多模块，缺少集中管理 | P0 修复重复劳动 |
| 验证 | Stage 5 是本地验证，非实际 PG 执行 | 无法发现运行时错误 |
| 依赖 | 无拓扑排序 | PACKAGE 转换顺序依赖人工 |

---

## 2. 各组件审查

### 2.1 Stage 1 Parser ✅ 稳定

**优点**:
- ANTLR4 解析器覆盖主要 Oracle 语法
- 支持 TYPE/VIEW 回退推断
- 提取复杂度特征 (complexity_flags)

**问题**:
- `complexity_score` 基于字符串匹配，不可靠
- PACKAGE_BODY 解析可能丢失嵌套 subprogram 信息

### 2.2 Stage 2 Analyzer ⚠️ 需改进

**当前问题**:

```python
# COMPLEXITY_RULES 中的问题
COMPLEXITY_RULES = {
    "EXCEPTION_HANDLING": 10,  # plsql_converter 已支持
    "NESTED_SUBPROGRAMS": 10,  # plsql_converter 已支持
    ...
}
```

**问题**: 复杂度评分过高导致不必要的 AI 路由

### 2.3 Stage 3 Generator ⚠️ 路由策略问题

**当前路由逻辑**:

```python
if file_type == 'TABLE':     → DDL converter
elif file_type == 'VIEW':     → VIEW converter
elif file_type == 'TYPE':     → TYPE converter
elif file_type in ('PACKAGE', 'PACKAGE_BODY'):  → AI (强制)
elif complexity >= threshold: → AI
else:                        → plsql_converter
```

**问题**:
1. PACKAGE_BODY 强制 AI，但 AI 输出质量不稳定
2. FUNCTION/PROCEDURE 路由阈值 (50) 过于严格
3. 无 fallback 降级机制 (AI 失败 → plsql_converter)

### 2.4 AI Agent ⚠️ 核心瓶颈

**问题清单**:

| 问题 | 频率 | 根因 |
|------|------|------|
| PACKAGE.FUNC 未改名 | 95% | prompt 强调不足 |
| SYSDATE 未转换 | 5 次 | AI 遗忘 |
| BEGIN/END 不平衡 | 巨型 chunk | 截断检测不足 |
| 缺 LANGUAGE | 10+ | AI 漏写结尾 |

**改进方向**:
1. 增强 SYSTEM_PROMPT
2. 多轮 retry 机制
3. 结构化输出格式

### 2.5 PostProcessor ✅ Phase 6 已改进

**当前功能 (Phase 6 已完成)**:

```python
# ✅ 已集成
- _fix_varchar2()               (P0)
- _fix_raise()                   (P0)
- _remove_param_prefix()         (P0)
- convert_package_call()          (P0-A)
- _fix_sysdate()                 (P0-D)
- _fix_array_access()            (P0-E)
- _mark_outer_join()             (P0-C)
- _check_begin_end_balance()      (P0-F)
```

**待集成**:

- NVL → COALESCE (AI 路径)
- DBMS_OUTPUT 警告
- EXCEPTION WHEN OTHERS → GET DIAGNOSTICS 提示

### 2.6 Validator ⚠️ 覆盖不完整

**当前验证**:

| 级别 | 状态 | 说明 |
|------|------|------|
| 语法检查 | ✅ | 括号平衡、关键字 |
| 依赖检查 | ❌ | 需 PG 连接 |
| 结构检查 | ⚠️ | 参数数量、RETURN |
| 语义检查 | ❌ | 需执行 |
| 业务检查 | ❌ | 需人工 |

---

## 3. 验证结果分析

### 第二轮验证 (17 样本, jev_validation_01)

| 路由 | 样本数 | 通过率 |
|------|--------|--------|
| ddl_converter | 2 | **100%** |
| type_converter | 1 | **100%** |
| ai_agent | 13 | **15%** |
| view_converter | 2 | **0%** |

**结论**: 规则式路径稳定，AI 路径是主要瓶颈

### AI 失败原因分布

| 问题 | 文件数 | 频率 |
|------|--------|------|
| PACKAGE.FUNC 未改名 | 12 | 95次/最大 |
| %ROWTYPE/%TYPE 残留 | 6 | 33次/最大 |
| Oracle (+) 外连接 | 2 | 4次/文件 |
| SYSDATE 未转换 | 1 | 5次 |
| tbl(idx) 数组访问 | 2 | - |
| BEGIN/END 不平衡 | 1 | - |

---

## 4. 优化方案

### 4.1 短期优化 (1-2 周)

#### 4.1.1 PostProcessor 增强清单

```
建议添加:
□ NVL → COALESCE (AI 路径)
□ DBMS_OUTPUT 警告标记
□ EXCEPTION WHEN OTHERS → GET DIAGNOSTICS 提示
□ SEQUENCE.NEXTVAL 语法检查
□ RETURNING INTO 警告
```

#### 4.1.2 AI Prompt 优化

```python
# 当前 prompt 缺少:
1. PACKAGE 命名规范强制要求 (package_method 格式)
2. SYSDATE → CURRENT_DATE 示例
3. 数组访问 [idx] vs (idx)
4. $$ delimiter 格式要求
5. LANGUAGE plpgsql 必须结尾
```

#### 4.1.3 路由策略调整

```python
# 建议调整 stage3_generator.py 的 COMPLEX_FEATURES:

COMPLEX_FEATURES = [
    "REF_CURSOR",        # 保留 (plsql_converter 不支持)
    "BULK_COLLECT",      # 保留
    "FORALL",            # 保留
    "DBMS_SQL",          # 保留
    "LOCK_TABLE",        # 保留
    "SAVEPOINT",         # 保留
    # 移除 (plsql_converter 已支持):
    # "EXCEPTION_HANDLING",
    # "NESTED_SUBPROGRAM",
    # "DYNAMIC_SQL",
]

# 预期效果: 简单 FUNCTION/PROCEDURE 更多走 plsql_converter 路径
```

### 4.2 中期优化 (1 个月)

#### 4.2.1 AI 增强

| 改进项 | 实现方式 | 优先级 |
|--------|----------|--------|
| 结构化输出 | 强制 JSON 格式 + schema 验证 | P0 |
| 多轮校验 | 输出后自动验证 BEGIN/END 平衡 | P1 |
| 增量转换 | 先转简单函数，再处理复杂函数 | P2 |

#### 4.2.2 验证增强

```python
# 建议新增验证项 (post_processor.py)

ORACLE_LEAK_PATTERNS = {
    "VARCHAR2": r'\bVARCHAR2\b',
    "NVARCHAR2": r'\bNVARCHAR2\b',
    "SYSDATE": r'(?<![A-Za-z0-9_])SYSDATE(?![A-Za-z0-9_])',
    "ROWTYPE": r'%ROWTYPE',
    "TYPE": r'%TYPE',
    "OUTER_JOIN": r'\(\+\)',
    "ARRAY_ACCESS": r'\w+\(\d+\)',
    "ORACLE_PARAM": r':[A-Za-z_]\w*',  # :param
}
```

#### 4.2.3 依赖拓扑排序

```
输入: PACKAGE_BODY 调用图
    PACKAGE_A → PACKAGE_B.Func
    PACKAGE_B → PACKAGE_C.Proc

输出: 转换顺序
    1. PACKAGE_C
    2. PACKAGE_B
    3. PACKAGE_A
```

### 4.3 长期优化 (3 个月)

#### 4.3.1 Schema 集成

```
当前: PostProcessor 无法转换 %ROWTYPE/%TYPE
原因: 需要对应的表/类型定义

方案:
1. 解析 PACKAGE_SPEC 获取类型定义
2. 解析 TABLE DDL 获取列类型
3. 在转换时查表替换
```

#### 4.3.2 AI 模型优化

| 当前 | 目标 |
|------|------|
| qwen3.8:27b | qwen3.14:27b 或更大 |
| 单次生成 | 多轮 self-correction |
| 通用 prompt | 领域微调 |

---

## 5. 实施路线图

```
Phase 7 (本周):
├── [ ] P0-B: %ROWTYPE/%TYPE Schema 解析
├── [ ] NVL → COALESCE (PostProcessor)
└── [ ] 路由优化: 降低 FUNCTION 阈值

Phase 8 (下周):
├── [ ] AI Prompt 增强
├── [ ] 拓扑排序实现
└── [ ] 结构化输出 JSON Schema

Phase 9 (月内):
├── [ ] Schema 集成 %ROWTYPE/%TYPE
├── [ ] AI 多轮校验
└── [ ] 增量转换策略
```

---

## 6. 关键指标跟踪

| 指标 | 当前 | 目标 |
|------|------|------|
| AI 路径通过率 | 15% | 60% |
| 规则式路径通过率 | 100% | 100% |
| 整体通过率 | 29% | 70% |
| PACKAGE.FUNC 残留 | 95次 | 0次 |
| SYSDATE 残留 | 5次 | 0次 |
| BEGIN/END 不平衡 | 1 文件 | 0次 |

---

## 7. 建议优先级

| 优先级 | 任务 | 预期收益 | 工作量 |
|--------|------|----------|--------|
| P0 | PostProcessor 增强 NVL/DBMS_OUTPUT | 消除 AI 常见遗漏 | 2小时 |
| P0 | AI Prompt 增强 | 提升 20-30% 通过率 | 1天 |
| P1 | 路由优化 | 减少不必要的 AI 调用 | 2小时 |
| P1 | 拓扑排序 | 解决依赖顺序问题 | 1天 |
| P2 | Schema 解析 %ROWTYPE | 消除最大残留项 | 1周 |
| P2 | 结构化 AI 输出 | 提升 AI 稳定性 | 3天 |

---

## 8. Phase 6 完成状态

### 已修复 (7/8)

| ID | 问题 | 提交 |
|----|------|------|
| P0-A | PACKAGE.FUNC 未改名 | `a6581a3` |
| P0-C | Oracle (+) 外连接警告 | `d8f8f9b` |
| P0-D | SYSDATE AI 残留 | `6f381f8` |
| P0-E | tbl(idx) Oracle 数组 | `7b79d65` |
| P0-F | AI BEGIN/END 不平衡 | `d8f8f9b` |
| P0-G | Analyzer 注释误报 | (本地) |
| P0-H | 路由过激 | `d8885a9` |

### 待处理 (1/8)

| ID | 问题 | 原因 |
|----|------|------|
| P0-B | %ROWTYPE/%TYPE 残留 | 需 schema 解析 |

---

## 9. 关键文件索引

| 文件 | 说明 |
|------|------|
| `pipeline/core.py` | Pipeline 入口 |
| `pipeline/stages/stage3_generator.py` | 路由逻辑 |
| `pipeline/stages/stage2_analyzer.py` | 复杂度评分 |
| `pipeline/stages/stage3_ai/post_processor.py` | AI 后处理 (Phase 6 修复) |
| `pipeline/plsql_converter/pg_naming.py` | 命名规范 |
| `HANDOFF.md` | 项目状态追踪 |

---

*Generated: 2026-09-22*
*Author: Pipeline Review*
