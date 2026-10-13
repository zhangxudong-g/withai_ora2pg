# Oracle → PostgreSQL 迁移 Pipeline - 整合规划

**文档类型**: 路线图 / 行动计划  
**数据来源**: 
- HANDOFF.md (GKB 323 文件实测数据, 2026-09-21)
- jev_review_report.md (Jev AI 模型评估, 11 次运行, 2026-09-22)
- jev_review_analysis.png (可视化分析)

**目标**: GKB 系统 (323 SQL 文件) 完整迁移到 PostgreSQL

---

## 📊 综合评估摘要

### Jev 模型评估（11 次运行，100% 一致）

| 维度 | 评分 | 置信度 | 状态 |
|------|------|--------|------|
| **语法风险** | 3.94/4.0 | 0.95 | 🔴 极高 |
| **语义风险** | 3.70/4.0 | 0.75 | 🔴 高 |
| **架构风险** | 3.68/4.0 | 0.73 | 🔴 高 |
| **AI 生成风险** | 3.61/4.0 | 0.68 | 🔴 高 |
| **依赖风险** | 3.15/4.0 | 0.82 | 🟠 中高 |
| 验证充分性 | 1.31/4.0 | 0.72 | 🔴 不足 |
| 架构完整性 | 1.71/4.0 | 0.37 | 🟠 不足 |
| 范围完整性 | 1.98/4.0 | 0.31 | 🟡 一般 |

### Jev 关键判断（100% 一致）

```
routing_strategy:   insufficient     → 当前路由策略不够
routing_basis:      file_type        → 仅基于文件类型，不够
hybrid_need:        hybrid           → 必须混合 rule + AI
chunk_strategy:     boundary_problem → 分块边界有问题
post_processor:     too_aggressive   → 不能过度依赖 post_processor
```

### Jev 概率判断

| 判断 | 概率 | 含义 |
|------|------|------|
| needs_human_review | 0.96 | 必须人工 review |
| has_critical_risk | 0.95 | 存在关键风险 |
| needs_checkpoint | 0.90 | 必须 checkpoint |
| needs_retry_loop | 0.69 | 建议 retry（信心中等） |

---

## 🎯 GKB 实测数据（323 文件）

### 文件分布与路由

| 类型 | 数量 | 平均行 | 复杂度 | 路由 | 状态 |
|------|------|--------|--------|------|------|
| TABLE | 151 | - | 0 | DDL converter | ✅ 已覆盖 |
| PACKAGE_BODY | 70 | 2148 | **100** | AI | 🔴 核心问题 |
| PACKAGE | 70 | 45 | 0 | AI | ✅ 规范无逻辑 |
| FUNCTION | 12 | - | 10-70 | plsql/AI | ✅ 部分覆盖 |
| VIEW | 9 | - | 0 | ❌ 未覆盖 | 🟠 需实现 |
| PROCEDURE | 6 | - | - | plsql/AI | ✅ 部分覆盖 |
| TYPE | 2 | - | 0 | ❌ 未覆盖 | 🔴 需 AI |
| DATA | 2 | - | 0 | ❌ 不处理 | 🟡 ETL |

### PACKAGE_BODY 复杂度特征

```
DBMS_SQL:          2987 次  ← plsql_converter 不支持
EXECUTE IMMEDIATE:  125 次
DBMS_OUTPUT:        67 次
REF CURSOR:         55 次
UTL_FILE:           2 次
```

### 实测 AI 输出问题

| 文件 | CREATE 数 | LANGUAGE 数 | 缺 LANGUAGE | 验证失败 chunks |
|------|-----------|-------------|-------------|-----------------|
| GKBPA00010B | 4 | 5 | 1+ | 4/6 |
| GKBPA00020B | 3 | 3 | 0+ | 5/6 |
| GKBPA00030B | 27 | 17 | **10** | 5/6 |

---

## 🚨 问题优先级矩阵

### 优先级定义

| 级别 | 标准 | 含义 |
|------|------|------|
| P0 | Jev > 3.5 + HANDOFF 实测 | 阻断迁移，必须立即修 |
| P1 | Jev > 2.5 或 HANDOFF 提到 | 影响可用性，应尽快修 |
| P2 | Jev < 2.5 | 锦上添花，可延后 |

### 优先级排序

```
P0 🔴 语法问题
├── 1.1 AI 输出缺 LANGUAGE plpgsql（27 CREATE vs 17 LANGUAGE）
├── 1.2 AI 输出缺 $$ delimiter
├── 1.3 AI 输出 Oracle :param 未去除
└── 1.4 Chunk 边界切碎 BEGIN/END 块

P0 🔴 覆盖缺失
├── 2.1 VIEW 转换器缺失（9 个文件）
├── 2.2 TYPE 转换缺失（2 个文件）
└── 2.3 AI 输出含 thinking/prose

P1 🟠 架构改进
├── 3.1 验证不足（Jev: 1.31/4）
├── 3.2 依赖顺序处理缺失
├── 3.3 Checkpoint 机制缺失
└── 3.4 PACKAGE → 独立函数命名混乱

P1 🟠 路由改进
├── 4.1 路由策略不够（Jev: insufficient）
├── 4.2 仅基于 file_type（Jev: file_type）
└── 4.3 复杂度评估基于字符串匹配

P2 🟡 体验优化
├── 5.1 重试循环（Jev: 0.69，支持但信心低）
├── 5.2 批量测试脚本
└── 5.3 GKB 迁移报告
```

---

## 📋 分阶段实施计划

### Phase 1: 核心修复（阻断问题）⏱️ 2-4 小时

**目标**: 修复 AI 输出语法问题，使生成的 PG 代码能通过基本语法检查

#### 1.1 重写 ChunkPlanner（最高优先）

```python
# 当前问题: 按 subprogram 边界切分，导致 BEGIN/END 不对齐
# 解决方案: 按 CREATE OR REPLACE 切分，保证每个 chunk 完整

# pipeline/stages/stage3_ai/chunk_planner.py
def _split_by_create_statement(source: str) -> List[Chunk]:
    """
    按 CREATE OR REPLACE FUNCTION/PROCEDURE 切分
    每个 chunk 是完整函数，包含完整的 BEGIN...END;
    """
    pattern = r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+'
    # ... 实现
```

#### 1.2 实现 Post-processor（关键但不过度）

```python
# pipeline/stages/stage3_ai/post_processor.py
class PostProcessor:
    def process(self, raw_output: str) -> str:
        # 1. 提取代码块（去除 thinking/prose）
        code = self.extract_code(raw_output)
        
        # 2. 修复 $$ delimiter（如果缺失）
        code = self.fix_dollar_delimiter(code)
        
        # 3. 自动补 LANGUAGE plpgsql
        code = self.add_language_plpgsql(code)
        
        # 4. 去除 Oracle :param 前缀
        code = self.remove_oracle_param_prefix(code)
        
        return code
```

⚠️ **注意**: Jev 评估 post_processor 为 "too_aggressive"，说明不能完全依赖它修正所有问题。核心还是要让 AI 正确生成。

#### 1.3 增强 Stage 1 检测 VIEW/TYPE

```python
# 当解析失败时，标记为 unsupported 并给出明确错误
# 而不是静默返回 cx=0
```

#### 1.4 验证修复效果

```bash
# 测试 GKBPA00030B（最大问题文件）
python -c "
from pipeline.core import MigrationPipeline
ctx = MigrationPipeline().run('gkb/.../GKBPA00030B.SQL')
print(f'LANG: {ctx.output.count(\"LANGUAGE plpgsql\")}')
print(f'CREATE: {ctx.output.count(\"CREATE OR REPLACE\")}')
"
# 期望: LANGUAGE >= CREATE
```

---

### Phase 2: 架构增强 ⏱️ 半天

**目标**: 增强验证、依赖处理、checkpoint

#### 2.1 强化验证层

```python
# stage5_validator.py 增强
class EnhancedValidator:
    def validate_syntax(self, pg_code: str) -> ValidationResult:
        # 1. BEGIN/END 匹配
        # 2. LANGUAGE plpgsql 存在
        # 3. $$ delimiter 存在
        # 4. 无 Oracle :param
        # 5. 实际用 pg_dump/psql 验证
```

#### 2.2 依赖拓扑排序

```python
# 解析 PACKAGE_BODY 中的 pkg.Func() 调用
# 按依赖顺序排序，leaf package 先转
def topological_sort(packages: List[Package]) -> List[Package]:
    # ...
```

#### 2.3 Checkpoint 机制

```python
# 批量处理时支持断点续传
class CheckpointManager:
    def save(self, file: str, status: str):
        # 记录已完成的文件
    def load(self) -> Set[str]:
        # 返回已完成文件列表
    def is_done(self, file: str) -> bool:
        # 检查是否已完成
```

---

### Phase 3: 路由优化 ⏱️ 2-4 小时

**目标**: 从 "file_type" 路由升级为多维度路由

#### 3.1 增强路由策略

```python
# 当前: 仅 file_type
# 改进: file_type + complexity + oracle_features + migration_risk

def determine_routing(metadata: MigrationMetadata) -> RoutingDecision:
    file_type = metadata.file_type
    complexity = metadata.complexity_score
    features = metadata.complexity_flags
    risk = assess_migration_risk(features)
    
    if risk > HIGH:
        return "ai_agent"  # Jev 建议
    elif complexity > THRESHOLD or has_complex_features(features):
        return "ai_agent"
    elif file_type in ("PACKAGE", "PACKAGE_BODY"):
        return "ai_agent"  # 必须 AI
    else:
        return "plsql_converter"
```

#### 3.2 改进复杂度评估

```python
# 当前: 字符串匹配
# 改进: AST 节点计数
def evaluate_complexity_ast(tree: ParseTree) -> ComplexityResult:
    dbms_sql_count = count_dbms_sql_nodes(tree)
    nested_procs = count_nested_subprograms(tree)
    dynamic_sql = count_execute_immediate_nodes(tree)
    # ...
```

---

### Phase 4: 扩展覆盖 ⏱️ 1-2 天

**目标**: 支持 VIEW、TYPE 等未覆盖类型

#### 4.1 VIEW 转换器

```python
# 简单替换
class ViewConverter:
    def convert(self, oracle_view: str) -> str:
        # CREATE OR REPLACE VIEW ... AS SELECT
        # 简单的 SELECT 替换
```

#### 4.2 TYPE → PG TYPE

```python
# Oracle TYPE 转换为 PostgreSQL
# 可能是复杂的，需要 AI
class TypeConverter:
    def convert(self, oracle_type: str) -> str:
        # OBJECT → CREATE TYPE
        # TABLE OF → CREATE TYPE AS TABLE
        # VARRAY → CREATE TYPE
```

#### 4.3 Java 端适配

```python
# pg_naming.py
def oracle_to_pg_name(package: str, func: str) -> str:
    """KKAPK0030.FCTGetR → kkapk0030_fctgetr"""
    return f"{package.lower()}_{func.lower()}"
```

---

### Phase 5: 质量保障 ⏱️ 持续

**目标**: 确保迁移质量，支持长期维护

#### 5.1 批量测试

```bash
# 跑完 70 个 PACKAGE_BODY，生成报告
python batch_test.py --input gkb/.../PACKAGE_BODY/ \
                     --output reports/pb_report.json \
                     --checkpoint checkpoints/pb_checkpoint.json
```

#### 5.2 自动化报告

```python
# 生成迁移质量报告
class MigrationReport:
    def generate(self, results: List[ConversionResult]) -> Report:
        # 统计通过率
        # 识别常见问题
        # 给出改进建议
```

#### 5.3 人工 Review 工作流

```yaml
# review_workflow.yaml
review_required:
  - syntax_risk > 3.5
  - complexity > 80
  - file_type in [PACKAGE_BODY, TYPE]
```

---

## 📅 时间线

```
Week 1 (本周)
├── Phase 1: 核心修复
│   ├── 1.1 ChunkPlanner 重写 [2h]
│   ├── 1.2 Post-processor [2h]
│   ├── 1.3 VIEW/TYPE 检测 [1h]
│   └── 1.4 验证 [1h]
└── 验证: GKBPA00030B 能正确生成

Week 2
├── Phase 2: 架构增强
│   ├── 2.1 强化验证 [2h]
│   ├── 2.2 拓扑排序 [2h]
│   └── 2.3 Checkpoint [2h]
└── Phase 3: 路由优化
    ├── 3.1 路由策略 [2h]
    └── 3.2 复杂度评估 [2h]

Week 3-4
├── Phase 4: 扩展覆盖
│   ├── 4.1 VIEW 转换器 [4h]
│   ├── 4.2 TYPE 转换 [8h]
│   └── 4.3 Java 适配 [4h]
└── 批量测试 70 个 PACKAGE_BODY

Week 5+
├── Phase 5: 质量保障
│   ├── 5.1 批量测试
│   ├── 5.2 报告生成
│   └── 5.3 人工 Review
└── GKB 完整切换
```

---

## ✅ 成功标准

### Phase 1 完成标准

- [ ] GKBPA00030B (27 CREATE) 生成完整 27 个 LANGUAGE plpgsql
- [ ] 无 `Missing $$ delimiter` 警告
- [ ] 无 `Found Oracle parameter prefix ':'` 警告
- [ ] BEGIN/END 匹配
- [ ] 单元测试通过

### Phase 2 完成标准

- [ ] 批量测试 70 个 PACKAGE_BODY 无需人工干预
- [ ] Checkpoint 支持断点续传
- [ ] 依赖顺序正确

### Phase 3 完成标准

- [ ] 路由策略从 "insufficient" 提升
- [ ] 复杂度评估用 AST 而非字符串
- [ ] Jev 评估改善

### 最终目标

- [ ] 323 个 GKB 文件全部转换
- [ ] 通过 Stage 5 五级验证
- [ ] Java 端能正常调用
- [ ] Jev 评估 syntax_risk < 2.0

---

## 🔗 关联文档

| 文档 | 描述 |
|------|------|
| `HANDOFF.md` | GKB 实测数据详细记录 |
| `jev_review_report.md` | Jev AI 模型评估详细报告 |
| `jev_review_analysis.png` | 可视化分析图表 |
| `docs/ROADMAP.md` | **完整实施计划（Phase 1-5，时间线，成功标准）** |
| `docs/chunk-planner-review.md` | **Phase 1 详细实施方案**（visitor 扩展 + 重写 ChunkPlanner） |
| `pipeline/docs/oracle-to-pg-conversion-foundations.md` | Oracle→PG 转换基础 |
| `pipeline/docs/sqlines-oracle-to-postgresql-rules.md` | SQLines 规则参考 |

---

## 🆘 快速启动

```bash
# 1. 验证环境
cd D:/JIP/poc
python -c "from pipeline.core import MigrationPipeline; print('OK')"

# 2. 跑单个测试文件
cd pipeline
python -c "
from core import MigrationPipeline
ctx = MigrationPipeline(enable_logging=True).run(
    file_path='../gkb/01.release/GK_gakumu/GKB_kyoiku/DB/01.SQL/PACKAGE_BODY/GKBPA00030B.SQL'
)
print(f'Status: {ctx.status}')
print(f'Strategy: {ctx.generate_strategy}')
print(f'LANG count: {ctx.generated_code.count(\"LANGUAGE plpgsql\")}')
print(f'CREATE count: {ctx.generated_code.count(\"CREATE OR REPLACE\")}')
"

# 3. 查看日志
ls -la output/logs/
```

---

*文档版本: 1.0*  
*创建时间: 2026-09-22*  
*整合来源: HANDOFF.md + jev_review_report.md + 实测数据*

---

## 实施进度 (2026-09-22)

### Phase 1 ✅ 完成
- [x] visitor.py 扩展 - nested_subprograms 填充
- [x] metadata.py 扩展 - parent_name/parent_kind 字段
- [x] 新增 package_context.py - PackageContextBuilder
- [x] 新增 unit.py - PlsqlUnit 数据类
- [x] 重写 chunk_planner.py - AST-aware semantic chunking
- [x] models.py 扩展 - Chunk 字段扩展

### Phase 2 ✅ 完成
- [x] 新增 post_processor.py - AI 输出后处理
- [x] 修复 VARCHAR2 -> VARCHAR
- [x] 修复 Oracle :param 前缀
- [x] 修复 $$ 和 LANGUAGE 顺序
- [x] 修复 RAISE 语法

### 测试结果 (2026-09-22)
| 文件 | Chunks | 通过 | VARCHAR2 | :param |
|------|--------|------|----------|--------|
| GKBPA00010B | 12 | 7/12 | 0 ✅ | 0 ✅ |
| GKBPA00020B | 11 | 8/11 | 0 ✅ | 0 ✅ |
| GKBPA00030B | 16 | 11/16 | 0 ✅ | 0 ✅ |

### 剩余问题
1. 验证器在 post-processor **之前**运行 - 架构问题
2. AI 生成代码不完整 - 缺少 CREATE 声明
3. 超大 chunks (5000+ tokens) 转换失败

### 提交记录
```
0131f90 feat(stage3_ai): Phase 4-5 - VIEW/TYPE converters, batch test, determinism constraints
04fc336 feat(stage3_ai): Phase 4 - truncation detection + retry
a08c2f3 feat(stage3_ai): add PostProcessor for AI output cleanup
68d7d74 fix(stage3_ai): pass metadata to ChunkPlanner for AST-aware chunking
142eeeb feat(stage3_ai): implement AST-aware Semantic Chunking (Phase 1)
```

### Phase 4-5 完成总结 (2026-09-22)

#### Phase 4: 扩展覆盖 ✅
- **VIEW 转换器** (`convert_view()`): 移除 FORCE, 清理 hint, DECODE → CASE, (+) 外连接标记
- **TYPE 转换器** (`convert_type()`): 移除 OR REPLACE + FORCE, NUMBER → NUMERIC, NVARCHAR2 → VARCHAR, CLOB → TEXT, BLOB → BYTEA, 添加 DROP TYPE IF EXISTS 提示
- **Parser 增强**: `_process_type()` + `_extract_type_name()` 处理 `Create_typeContext`
- **Source fallback**: `_infer_file_type()` 当 ANTLR 未识别 file_type 时用 regex 推断 (避免静默失败)
- **AI 决定性约束**: `temperature=0` (was 0.1), `num_ctx: 8192 → 32768` 处理超大 chunks
- **AI fallback 标记**: 输出含 `⚠⚠⚠ WARNING: AI UNAVAILABLE - FALLBACK USED ⚠⚠⚠` 头部
- **Stage 4 输出**: 不再写入 `Elapsed: {ms}ms` 行 (避免字符串比较失败)

#### Phase 5: 质量保障 ✅
- **batch_test.py**: 批量测试脚本 (Phase 5.1)
  - 支持 `--limit` smoke test、`--resume` 断点续传、`--ai` 启用 AI
  - 实时保存 `checkpoint.json`，聚合指标 (CREATE / LANGUAGE / 残留 / fallback 数 / 策略分布)
- **report.md 生成器** (Phase 5.2): 总览 + 质量指标 + 策略分布 + 失败详情 + 质量警告 + 建议

#### Smoke test 结果
| 输入 | 文件数 | 策略 | 通过率 | 耗时 |
|------|--------|------|--------|------|
| TYPE 目录 | 2 | type_converter | 100% | 2.1s |
| VIEW 目录 (limit 3) | 3 | view_converter | 100% | 29.9s |

#### 测试覆盖
- `pipeline/tests/test_view_type_converter.py`: 18 个测试全部通过 (VIEW: 7, TYPE: 11)Phase 3 (2026-09-22 下午)
- [x] 修复验证器 RAISE 模式 - 不匹配 EXCEPTION
- [x] 改进 PostProcessor - 添加缺失的 $$ delimiter
- [x] 改进 PostProcessor - 正确处理 $$ 和 LANGUAGE 顺序

### 提交记录
```
4b80788 fix(stage3_ai): improve PostProcessor and Validator
a08c2f3 feat(stage3_ai): add PostProcessor for AI output cleanup
68d7d74 fix(stage3_ai): pass metadata to ChunkPlanner
142eeeb feat(stage3_ai): implement AST-aware Semantic Chunking
```

---

## Phase 5 P0 修复 (2026-09-22 下午)

**驱动依据**: `pipeline/output/jev_validation/99_fix_plan.md`

### 9 个 P0 修复完成

| # | 修复项 | 文件 |
|---|--------|------|
| P0-1 | 注释剥离 regex bug | `plsql_converter.py:218-244` |
| P0-2 | `_parse_params` 重写 (平衡括号算法) | `plsql_converter.py:296-373` |
| P0-3 | EXECUTE IMMEDIATE → EXECUTE | `plsql_converter.py:583` |
| P0-4 | 新建 `pg_naming.py` 模块 | `plsql_converter/pg_naming.py` |
| P0-5 | view_converter NVL → COALESCE | `_view_convert_nvl_coalesce()` |
| P0-6 | view_converter SYSDATE → CURRENT_DATE | `_view_convert_sysdate()` |
| P0-7 | assemble_chunks 重写 (concat 而非重组) | `stages/stage3_ai/tools/assemble.py` |
| P0-8 | validator 字符串字面量剥离 | `_strip_string_literals()` |
| P0-9 | NVARCHAR2 → VARCHAR (无括号变体) | `map_oracle_to_pg` + `post_processor` |

### Jev 第一轮 8 样本验证结果

| 样本 | 修复前 | 修复后 |
|------|--------|--------|
| GKBFKGKCDGET (plsql) | ❌ | ✅ |
| GKBFKHTSET (ai) | ✅ | ✅ |
| GKBSKJDOG (ai) | ✅ | 🟡 1/2 chunks |
| GKBPA00030B (PACKAGE) | ❌ 1 CREATE | ✅ **16/16 chunks, 32$$** |
| GKBPB00200B (PACKAGE) | ❌ | 🟡 23/24 chunks |
| GKBWL410R001 (TABLE) | ✅ | ✅ |
| GKBVEUCDVTAISHO (VIEW) | ❌ | ✅ |
| GKBEKENSU_LIST (TYPE) | ✅ | ✅ |

**通过率**: 4/8 (50%) → **6/8 (75%)**

提交: `d324dcd feat: P0 fixes from jev_validation 99_fix_plan.md`

---

## Jev 第二轮验证 (2026-09-22 下午) - 17 样本

**目录**: `pipeline/output/jev_validation_01/`

### 测试结果

| 路由 | 通过率 |
|------|--------|
| ai_agent | 2/13 (15%) |
| view_converter | 0/2 (0%) |
| ddl_converter | 2/2 (**100%**) |
| type_converter | 1/1 (**100%**) |
| **总** | **5/17 (29%)** |

### 发现的 8 个新 P0 问题 (Phase 6 任务)

| ID | 问题 | 影响 | 修复位置 |
|----|------|------|---------|
| **P0-A** | **PACKAGE.FUNC 未改名 (AI 路径)** | **13 文件, 95 次/最大** | **post_processor** |
| P0-B | %ROWTYPE/%TYPE 残留 | 6 文件 | Phase 7 (需 schema) |
| P0-C | Oracle (+) 外连接 | 2 文件 | view_converter |
| P0-D | SYSDATE AI 残留 | 1 文件 | post_processor |
| P0-E | tbl(idx) Oracle 数组 | 2 文件 | post_processor |
| P0-F | AI BEGIN/END 不平衡 | 1 文件 | AST Block 拆分 |
| P0-G | Analyzer 注释误报 | 1 文件 | analyze_equivalence |
| P0-H | FUNCTION/PROCEDURE 路由过激 | 9 文件 | _is_simple() |

### Phase 6 任务清单

#### 6.1 P0-A 修复 (1-2 小时)
**目标**: AI 路径输出走 `convert_package_call` + `convert_array_access`
- `pipeline/stages/stage3_ai/post_processor.py` 集成 `pg_naming`
- 调用顺序: chunk 级后处理 → 最终级后处理

#### 6.2 P0-D/E 修复 (30 分钟)
**目标**: post_processor 加 SYSDATE + tbl(idx) 全局修复
- `SYSDATE → CURRENT_DATE`
- `tbl(idx) → tbl[idx]` (限定在 known_collections)

#### 6.3 P0-G 修复 (30 分钟)
**目标**: analyze_equivalence 检测前先剥 SQL 注释
- 添加 `_strip_sql_comments()` 函数
- 参考 `_strip_string_literals` 实现

#### 6.4 P0-C 修复 (1 小时)
**目标**: view_converter 加 Oracle (+) 检测
- 检测 `(+)` 出现在 WHERE 条件
- 转 LEFT/RIGHT JOIN (需要 SQL 解析)
- 警告: 本期只标记, 不自动转换

#### 6.5 P0-H 优化 (1 小时)
**目标**: 重审 `_is_simple()` 阈值
- `EXCEPTION_HANDLING` 从复杂列表移除 (plsql 支持)
- `NESTED_SUBPROGRAM` 默认放行 (plsql 支持)
- 简化 ai_agent 触发条件

#### 6.6 P0-F 修复 (2-4 小时) - 可选
**目标**: 巨型 chunk (>5K tokens) AST Block 拆分
- 实现 chunk_planner Phase 2 (chunk 内 AST block 切分)
- 提升 AI 输出 BEGIN/END 平衡率

### 验证标准

跑 `pipeline/output/jev_validation_01/run_one.py` 全量, 期望:
- 通过率: 29% → **70%+**
- 至少 P0-A, P0-D, P0-E, P0-G, P0-H 修复后样本绿
- P0-B (schema 依赖) 可保留为已知限制

---

## Phase 7+ (远期)

- P0-B: %ROWTYPE/%TYPE 需要 PACKAGE_SPEC 上下文
- Java 端适配层 (`pg_naming` + Java_Adapter_Guide)
- 依赖拓扑排序
- AI 输出质量的渐进改进


