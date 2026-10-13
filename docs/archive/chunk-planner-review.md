# ChunkPlanner 重构评审与实施方案

**文档类型**: 技术评审 + 实施计划  
**来源**: GPT AST-aware Semantic Chunking 方案  
**对比**: 现有 `chunk_planner.py` + `visitor.py` + `metadata.py`  
**日期**: 2026-09-22

---

## 1. 现状分析

### 1.1 现有代码架构

```
visitor.py
├── _extract_nested_subprograms()     # FUNCTION/PROCEDURE body
├── _extract_package_members()        # PACKAGE_BODY 成员
└── metadata.subprograms: List[Subprogram]  # 扁平列表 ❌

metadata.py
└── Subprogram
    ├── name, kind, start_line, end_line
    ├── nested_subprograms: List[Subprogram]  # 字段存在但 ❌ 从未填充
    └── parameters, variables, return_type

chunk_planner.py
├── _split_by_boundaries()   # 按子程序边界分块
├── _split_by_structure()    # 按 BEGIN/END 回退
└── 核心问题: 把 nested subprogram 和 parent 当作平行边界切分
```

### 1.2 现有 ChunkPlanner 的致命问题

#### 问题 1: Nested Subprogram 被切碎

```python
# 当前 _split_by_boundaries 逻辑
for start, end, name, kind in boundaries:
    if line_idx + 1 == start:
        # 如果当前块有内容且达到阈值，保存并开始新块
        if len(current_chunk_lines) >= self.max_lines // 2:
            chunks.append((current_chunk_lines, current_sub_info))
```

**问题**: 嵌套函数 `CHECK_DATA` 在 `MAIN_PROC` 内部，但可能被当作独立边界处理：

```
PACKAGE BODY
│
└── MAIN_PROC (lines 100-850)
     │
     └── CHECK_DATA (lines 120-200)  ← 被当作独立边界 ❌
          │
          └── BEGIN...END
```

#### 问题 2: 没有 Package Context

```python
# 当前 Chunk 只有 source，没有任何上下文
Chunk(
    source='PROCEDURE FCTGetR ... IS BEGIN ... END;',
    # ❌ 缺少 g_user_id, g_mode 等变量声明
    # ❌ 缺少其他 member 的签名
)
```

#### 问题 3: BEGIN/END 匹配基于字符串

```python
# 当前 _split_by_structure 使用 regex
if re.match(r'^\s*BEGIN\s*$', stripped, re.IGNORECASE):
    depth_stack.append('BEGIN')
```

**问题**: 不能真正理解 PL/SQL 语法结构。

#### 问题 4: 没有 Chunk Integrity Validator

```python
# 当前直接返回 chunks，不验证结构完整性
return chunks
```

---

## 2. GPT 方案分析

### 2.1 核心思想

> **"任何一个 Function / Procedure / Nested Subprogram 都不能被拆开。"**

### 2.2 推荐的数据结构

```python
# Phase 1: 新增

@dataclass
class PlsqlUnit:
    """PL/SQL 语义单元（不可分割）"""
    name: str
    kind: str  # FUNCTION, PROCEDURE
    start_line: int
    end_line: int
    source: str
    parent: str | None  # 父级 unit 名称
    nesting_level: int
    dependencies: list[str]

@dataclass
class PackageContext:
    """Package 级别的上下文（压缩版）"""
    package_name: str
    variables: list[str]      # g_user_id NUMBER
    constants: list[str]
    types: list[str]
    cursors: list[str]
    signatures: list[str]     # FCTGetR(p_id NUMBER) RETURN NUMBER
    referenced_objects: list[str]

@dataclass
class MigrationChunk:
    """最终交给 AI 的 chunk"""
    chunk_id: str
    unit_name: str
    unit_type: str
    parent_unit: str | None
    source: str
    context: PackageContext
    estimated_tokens: int
    split_level: int  # 0=unit, 1=ast_block
    is_complete_unit: bool
```

### 2.3 三层分块策略

| Level | 触发条件 | 策略 |
|-------|---------|------|
| **Level 0** | 所有 chunk | Package Context 注入 |
| **Level 1** | Unit ≤ target_size | 完整 Function/Procedure |
| **Level 2** | Unit > target_size | AST Block 拆分（Phase 2） |

### 2.4 关键原则

```
P0: 不能切断 AST node
P0: Function/Procedure body 必须完整
P0: Nested subprogram 必须保持父级完整
P0: BEGIN/EXCEPTION/END 结构不能跨 Chunk
P1: 尽量以完整 Function/Procedure 为 Chunk
P1: 注入 Package Context
P1: 保留 dependency metadata
P2: 超大 Unit 才按 AST Block 拆
```

---

## 3. 对比分析

### 3.1 visitor.py 分析

| 功能 | 当前 | GPT 需求 | 差距 |
|------|------|---------|------|
| nested_subprograms 填充 | ❌ 字段存在但为空 | ✅ 必须填充树形结构 | **关键差距** |
| Package Context | ❌ 无 | ✅ 需要 declarations/variables | **关键差距** |
| line range | ✅ 有 | ✅ 必须有 | OK |
| dependency tracking | 部分 | ✅ 完整 | 需增强 |

### 3.2 metadata.py 分析

| 功能 | 当前 | GPT 需求 | 差距 |
|------|------|---------|------|
| Subprogram.nested | ❌ 空 | ✅ 树形结构 | **需添加** |
| Subprogram.parent | ❌ 无 | ✅ 父级引用 | **需添加** |
| Package Context | ❌ 无 | ✅ 新 class | **需新增** |

### 3.3 chunk_planner.py 分析

| 功能 | 当前 | GPT 需求 | 差距 |
|------|------|---------|------|
| 分块策略 | 按边界/行数 | 按 Unit 边界 | **需重写** |
| Package Context | ❌ 无 | ✅ 必须注入 | **需添加** |
| Nested 处理 | ❌ 扁平 | ✅ 树形保持 | **需重写** |
| Integrity Check | ❌ 无 | ✅ 必须有 | **需添加** |

### 3.4 models.py 分析

| 功能 | 当前 | GPT 需求 | 差距 |
|------|------|---------|------|
| Chunk 字段 | 基本字段 | 需要 context, unit_name 等 | **需扩展** |

---

## 4. 实施计划

### Phase 1: 核心修复（本周）

**目标**: 修复 Nested Subprogram 切碎 + Package Context 缺失问题

#### Step 1.1: 扩展 visitor.py

```python
# parsers/visitor.py

def _extract_package_members(self, ctx) -> List[Subprogram]:
    """
    提取包体成员（带嵌套结构）
    返回扁平的 subprograms 列表，但嵌套结构存储在 nested_subprograms
    """
    subprograms = []
    
    # 顶层 Function/Procedure
    for func_ctx in self._find_all(ctx, "Function_bodyContext"):
        sub = self._build_subprogram(func_ctx, "FUNCTION")
        # 递归提取嵌套子程序
        sub.nested_subprograms = self._extract_nested_recursive(func_ctx)
        subprograms.append(sub)
    
    # ... 类似处理 Procedure
    
    return subprograms

def _extract_nested_recursive(self, ctx) -> List[Subprogram]:
    """递归提取嵌套子程序，保持树形结构"""
    nested = []
    for func_ctx in self._find_all(ctx, "Function_bodyContext"):
        sub = self._build_subprogram(func_ctx, "FUNCTION")
        sub.nested_subprograms = self._extract_nested_recursive(func_ctx)
        nested.append(sub)
    for proc_ctx in self._find_all(ctx, "Procedure_bodyContext"):
        sub = self._build_subprogram(proc_ctx, "PROCEDURE")
        sub.nested_subprograms = self._extract_nested_recursive(proc_ctx)
        nested.append(sub)
    return nested
```

#### Step 1.2: 新增 PackageContextBuilder

```python
# pipeline/stages/stage3_ai/package_context.py

@dataclass
class PackageContext:
    package_name: str
    variables: list[str]
    constants: list[str]
    types: list[str]
    cursors: list[str]
    signatures: list[str]
    referenced_objects: list[str]

class PackageContextBuilder:
    def build(self, metadata: MigrationMetadata, source: str) -> PackageContext:
        ctx = PackageContext()
        ctx.package_name = metadata.object_name
        
        # 从 metadata.variables 提取
        for var in metadata.variables:
            ctx.variables.append(f"{var.name} {var.data_type}")
        
        # 从 subprograms 提取签名
        for sub in metadata.subprograms:
            sig = f"{sub.name}({', '.join(p.name + ' ' + p.data_type for p in sub.parameters)})"
            if sub.return_type:
                sig += f" RETURN {sub.return_type}"
            ctx.signatures.append(sig)
        
        return ctx
```

#### Step 1.3: 重写 ChunkPlanner

```python
# pipeline/stages/stage3_ai/chunk_planner.py (重构)

class ChunkPlanner:
    """
    AST-aware Semantic Chunking
    
    核心原则: Function/Procedure/Nested Subprogram 不能被切分
    """
    
    def plan(
        self,
        source: str,
        metadata: MigrationMetadata,
    ) -> ChunkPlan:
        # 1. 构建 Package Context
        pkg_ctx = self._build_package_context(metadata)
        
        # 2. 提取语义单元（带嵌套结构）
        units = self._extract_semantic_units(metadata, source)
        
        # 3. 按 Unit 分块
        chunks = self._create_chunks(units, pkg_ctx, source)
        
        # 4. 完整性验证
        chunks = self._validate_chunks(chunks)
        
        return ChunkPlan(chunks=chunks, ...)
    
    def _extract_semantic_units(
        self,
        metadata: MigrationMetadata,
        source: str,
    ) -> List[PlsqlUnit]:
        """
        提取语义单元（不切分 nested subprogram）
        
        返回扁平的 units 列表，但每个 unit 记录 parent 信息
        """
        units = []
        lines = source.split('\n')
        
        def process_subprogram(sub: Subprogram, parent_name: str | None, level: int):
            # 创建 Unit（使用精确的 line range）
            unit = PlsqlUnit(
                name=sub.name,
                kind=sub.kind,
                start_line=sub.start_line,
                end_line=sub.end_line,
                source='\n'.join(lines[sub.start_line-1:sub.end_line]),
                parent=parent_name,
                nesting_level=level,
                dependencies=[],
            )
            units.append(unit)
            
            # 递归处理嵌套子程序（作为同一个 chunk 的一部分）
            for nested in sub.nested_subprograms:
                process_subprogram(nested, sub.name, level + 1)
        
        # 处理顶层 subprograms
        for sub in metadata.subprograms:
            process_subprogram(sub, None, 0)
        
        return units
    
    def _create_chunks(
        self,
        units: List[PlsqlUnit],
        pkg_ctx: PackageContext,
        source: str,
    ) -> List[Chunk]:
        """
        按语义单元创建 chunks
        每个单元作为一个不可分割的 chunk
        """
        chunks = []
        
        for unit in units:
            # 计算 token 估计
            tokens = self._estimate_tokens(unit.source)
            
            if tokens > self.max_tokens:
                # Phase 2: 按 AST block 拆分
                sub_chunks = self._split_by_ast_block(unit, pkg_ctx)
                chunks.extend(sub_chunks)
            else:
                chunk = Chunk(
                    index=len(chunks),
                    start_line=unit.start_line,
                    end_line=unit.end_line,
                    source=unit.source,
                    context_before=self._format_package_context(pkg_ctx),
                    can_split=False,  # 语义单元不可切分
                )
                chunks.append(chunk)
        
        return chunks
    
    def _validate_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """
        验证 chunk 完整性
        """
        validated = []
        for chunk in chunks:
            if not self._is_complete_unit(chunk.source):
                # 记录警告但不丢弃（交给 AI 处理）
                chunk.error_message = "Chunk may be incomplete unit"
            validated.append(chunk)
        return validated
    
    def _is_complete_unit(self, source: str) -> bool:
        """检查是否是完整的 Function/Procedure"""
        has_create = re.search(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)',
            source,
            re.IGNORECASE
        )
        has_is_or_as = re.search(r'\b(IS|AS)\b', source, re.IGNORECASE)
        has_begin = re.search(r'\bBEGIN\b', source, re.IGNORECASE)
        has_end = re.search(r'\bEND\b', source, re.IGNORECASE)
        
        return all([has_create, has_is_or_as, has_begin, has_end])
```

### Phase 2: 增强验证（下周）

- Chunk Integrity Validator（完整版）
- 超大 Unit 的 AST Block 拆分
- Dependency tracking 增强

---

## 5. 关键代码变更清单

### 5.1 parsers/visitor.py

```diff
  class MigrationVisitor:
-    def _extract_package_members(self, ctx) -> List[Subprogram]:
+    def _extract_package_members(self, ctx) -> List[Subprogram]:
+        # 返回带 nested_subprograms 填充的完整树形结构
     
+    def _extract_nested_recursive(self, ctx) -> List[Subprogram]:
+        # 递归提取嵌套子程序
```

### 5.2 parsers/metadata.py

```diff
  @dataclass
  class Subprogram:
      name: str = ""
      kind: str = ""
      parameters: List[Parameter] = field(default_factory=list)
      return_type: Optional[str] = None
      start_line: int = 0
      end_line: int = 0
      variables: List[Variable] = field(default_factory=list)
      nested_subprograms: List['Subprogram'] = field(default_factory=list)
+     parent_name: Optional[str] = None  # 新增
+     parent_kind: Optional[str] = None  # 新增
```

### 5.3 新增文件

| 文件 | 内容 |
|------|------|
| `pipeline/stages/stage3_ai/package_context.py` | PackageContext + PackageContextBuilder |
| `pipeline/stages/stage3_ai/unit.py` | PlsqlUnit 数据类 |

### 5.4 重写文件

| 文件 | 变更 |
|------|------|
| `pipeline/stages/stage3_ai/chunk_planner.py` | 完全重写为 AST-aware semantic chunking |
| `pipeline/stages/stage3_ai/models.py` | 扩展 Chunk 添加 context 等字段 |

---

## 6. 验证计划

### 6.1 单元测试

```python
def test_nested_subprogram_not_split():
    """嵌套子程序不能被切分"""
    source = """
    CREATE OR REPLACE PACKAGE BODY PKG_TEST AS
        PROCEDURE MAIN AS
            FUNCTION INNER_Func RETURN NUMBER IS
            BEGIN RETURN 1; END;
        BEGIN NULL; END;
    END PKG_TEST;
    """
    metadata = parse(source)
    planner = ChunkPlanner()
    plan = planner.plan(source, metadata)
    
    # 应该只有 1 个 chunk（MAIN 包含 INNER）
    assert len(plan.chunks) == 1
    assert 'INNER_Func' in plan.chunks[0].source

def test_package_context_injected():
    """Package Context 必须注入"""
    source = """
    CREATE OR REPLACE PACKAGE BODY PKG_TEST AS
        g_var NUMBER;
        PROCEDURE P1 IS BEGIN NULL; END;
    END PKG_TEST;
    """
    metadata = parse(source)
    planner = ChunkPlanner()
    plan = planner.plan(source, metadata)
    
    assert 'g_var NUMBER' in plan.chunks[0].context_before
```

### 6.2 GKB 集成测试

```python
def test_gkbpa00030b():
    """GKBPA00030B 实测"""
    ctx = MigrationPipeline().run('gkb/.../GKBPA00030B.SQL')
    
    # Phase 1 目标
    lang_count = ctx.output.count('LANGUAGE plpgsql')
    create_count = ctx.output.count('CREATE OR REPLACE')
    
    assert lang_count >= create_count, f"LANG({lang_count}) < CREATE({create_count})"
    
    # Jev 验证
    assert ctx.validation.warnings < 5  # 从 5+ 降到 <5
```

### 6.3 A/B 对比实验

```
旧 ChunkPlanner
    ↓
AI → validation

vs

新 AST ChunkPlanner
    ↓
AI → validation
```

指标：
- validation warnings 数量
- missing LANGUAGE count
- BEGIN/END mismatch count

---

## 7. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ANTLR 解析失败（某些 GKB 语法） | 中 | 高 | 回退到简单分块 |
| Package Context 太大（token 爆炸） | 低 | 中 | 压缩到 signature 级别 |
| 嵌套层数过深 | 低 | 低 | 限制递归深度 |
| Phase 2 AST Block 拆分复杂 | 高 | 中 | Phase 2 再做，先保证 Phase 1 |

---

## 8. 时间线

```
Week 1 (本周)
├── Day 1-2: 扩展 visitor.py（nested_subprograms 填充）
├── Day 3-4: 新增 package_context.py
├── Day 5: 重写 chunk_planner.py (Phase 1)
└── Day 5: 单元测试 + GKB 集成测试

Week 2
├── 修复 Phase 1 发现的问题
├── 验证 GKBPA00010B/20B/30B 改善
└── A/B 对比实验

Week 3+ (Phase 2)
├── AST Block 拆分
├── Chunk Integrity Validator 完整版
└── 依赖追踪增强
```

---

## 9. 成功标准

### Phase 1 完成标准

- [ ] 嵌套子程序不被切分（GKBPA00030B 中嵌套函数完整）
- [ ] Package Context 注入到每个 chunk
- [ ] ChunkIntegrityValidator 检查通过率 > 80%
- [ ] GKBPA00030B: LANGUAGE count >= CREATE count
- [ ] validation warnings 从 5+ 降到 < 3

### Phase 2 完成标准

- [ ] 超大 Unit (>30K tokens) 能按 AST Block 正确拆分
- [ ] 拆分后的 block 能正确重组
- [ ] 完整 GKB 测试套件通过率 > 90%

---

## 10. 参考资料

- GPT AST-aware Semantic Chunking 方案（用户提供的方案）
- `HANDOFF.md` - GKB 实测数据
- `jev_review_report.md` - Jev AI 评估（chunk_strategy = boundary_problem）
- `docs/ROADMAP.md` - 整体实施计划

---

*文档版本: 1.0*  
*创建时间: 2026-09-22*  
*评审依据: GPT 方案 vs 现有代码对比*
