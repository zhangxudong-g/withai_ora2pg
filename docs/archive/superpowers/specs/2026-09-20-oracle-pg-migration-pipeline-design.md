# Oracle → PostgreSQL 迁移 Pipeline 设计

> 创建时间: 2026-09-20
> 更新时间: 2026-09-20
> 状态: **Stage 1-2 已完成**, Stage 3-5 待实现

## 1. 架构概览

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Oracle SQL  │────▶│  Stage 1    │────▶│  Stage 2    │
│    源码      │     │   Parser    │     │  Analyzer   │
└─────────────┘     └─────────────┘     └─────────────┘
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    ▼                                                   ▼
            ┌───────────────┐                                   ┌───────────────┐
            │  Stage 3A    │                                   │  Stage 3B    │
            │  规则引擎     │                                   │  AI 增强     │
            │ (score < 30) │                                   │(score >= 30) │
            └───────────────┘                                   └───────────────┘
                    │                                                   │
                    └─────────────────────────┬─────────────────────────┘
                                              ▼
                                    ┌─────────────┐
                                    │  Stage 4    │
                                    │  Generator  │
                                    └─────────────┘
                                              │
                                              ▼
                                    ┌─────────────┐
                                    │  Stage 5    │
                                    │  Validator  │
                                    └─────────────┘
                                              │
                                              ▼
                                    ┌─────────────┐
                                    │  PostgreSQL │
                                    │    代码     │
                                    └─────────────┘
```

## 2. Stage 1: Parser (✅ 已完成)

### 职责
- 词法分析: Oracle SQL → Token 流
- 语法分析: Token 流 → AST
- 元数据提取: 参数、变量、子程序、代码范围

### 实现位置
```
parsers/plsql/
├── __init__.py          # parse_plsql() 导出
├── metadata.py          # MigrationMetadata 数据结构
├── visitor.py           # AST 遍历器
├── parser.py            # 解析器入口
└── grammar/             # ANTLR4 语法
    ├── PlSqlLexer.py    # 2480 tokens
    └── PlSqlParser.py   # 21335 rules
```

### 输出结构 (MigrationMetadata)

```python
@dataclass
class MigrationMetadata:
    # 基本信息
    file_type: str              # FUNCTION, PROCEDURE, PACKAGE_BODY, TRIGGER
    object_name: str            # 对象名称
    
    # 结构信息
    parameters: List[Parameter]   # 参数
    variables: List[Variable]    # 变量
    subprograms: List[Subprogram]  # 子程序（含嵌套）
    
    # 代码范围
    start_line: int
    end_line: int
    body_start: int
    body_end: int
    
    # 依赖关系
    table_references: List[TableReference]
    call_relationships: List[CallRelationship]
    
    # 复杂度评估
    complexity_score: int        # 0-100
    complexity_flags: List[str] # DYNAMIC_SQL, REF_CURSOR, etc.
    dynamic_sql_count: int      # EXECUTE IMMEDIATE 次数
    
    # 额外信息 (JSON)
    extra: Dict[str, Any]       # dependencies, trigger_info, etc.
```

### 使用示例

```python
from parsers.plsql import parse_plsql

metadata = parse_plsql(source_code)

print(f"类型: {metadata.file_type}")
print(f"名称: {metadata.object_name}")
print(f"参数: {len(metadata.parameters)}")
print(f"复杂度: {metadata.complexity_score}/100")
print(f"标志: {metadata.complexity_flags}")

# 依赖信息
deps = metadata.extra.get("dependencies", {})
print(f"表: {deps.get('tables', [])}")
print(f"包: {list(deps.get('packages', {}).keys())}")
print(f"序列: {deps.get('sequences', [])}")

# 路由决策
if metadata.complexity_score < 30:
    print("→ 规则引擎转换")
else:
    print("→ AI 增强转换")
```

---

## 3. Stage 2: Analyzer (✅ 已完成)

### 职责
- 依赖解析: 表引用、函数调用、包依赖、序列引用
- 类型推断: 基于 AST 推断变量类型
- 复杂度评估: 综合评分 + 路由决策

### 复杂度评分规则

| 特性 | 加分 | 说明 |
|------|------|------|
| 嵌套子程序 | +10/个 | 最多 +50 |
| 动态 SQL | +20 | EXECUTE IMMEDIATE |
| DBMS_SQL | +30 | 使用 DBMS_SQL 包 |
| REF CURSOR | +15 | 游标变量 |
| %ROWTYPE | +10 | 行类型引用 |
| %TYPE | +5 | 列类型引用 |
| BULK COLLECT | +20 | 批量收集 |
| FORALL | +25 | 批量操作 |
| 自治事务 | +15 | PRAGMA AUTONOMOUS_TRANSACTION |
| 异常处理 | +10 | EXCEPTION HANDLER |

### 路由决策

| 复杂度分数 | 路由 | 说明 |
|------------|------|------|
| < 20 | 规则引擎 | 直接转换 |
| 20-50 | 规则 + 验证 | 转换后加强验证 |
| 50-80 | AI 增强 | 需要 AI 辅助 |
| > 80 | 人工介入 | 建议人工处理 |

---

## 4. Stage 3: Transform (🚧 待实现)

### 智能路由

```
Stage 2 复杂度评估
        ↓
    ┌───┴───┐
    ↓       ↓
score < 30   score >= 30
    ↓           ↓
Stage 3A     Stage 3B
规则引擎     AI 增强
```

### Stage 3A: 规则转换

**适用场景**: 静态 SQL、简单数据类型映射、标准关键字替换

**规则示例**:

| Oracle | PostgreSQL |
|--------|------------|
| `NVL(a,b)` | `COALESCE(a,b)` |
| `NUMBER` | `NUMERIC` |
| `VARCHAR2(n)` | `VARCHAR(n)` |
| `SYSDATE` | `CURRENT_DATE` |
| `ROWNUM` | `LIMIT` |
| `\|\|` | `CONCAT()` |
| `DECODE()` | `CASE WHEN` |
| `TRUNC(date)` | `DATE_TRUNC('day', date)` |

### Stage 3B: AI 转换

**适用场景**: 动态 SQL、复杂业务逻辑、语义等价性要求高

**AI 输入格式**:
```json
{
  "source_code": "...",
  "metadata": {
    "file_type": "FUNCTION",
    "object_name": "...",
    "complexity_score": 65,
    "complexity_flags": ["DYNAMIC_SQL", "REF_CURSOR"]
  },
  "dependencies": {
    "tables": ["GKBTGAKUREIBO"],
    "columns": {"GKBTGAKUREIBO.SHOGAKUKBN": "VARCHAR2"}
  }
}
```

---

## 5. Stage 4: Generator (🚧 待实现)

**职责**: 中间表示 → PostgreSQL 语法

---

## 6. Stage 5: Validator (🚧 待实现)

### 验证层级

| 层级 | 检查内容 | 技术 |
|------|----------|------|
| V1 | 语法验证 | PG 解析器能否接受 |
| V2 | 依赖验证 | 引用的表/列是否存在 |
| V3 | 结构验证 | 函数签名是否正确 |
| V4 | 语义验证 | 输入输出是否等价 |
| V5 | 业务验证 | 核心逻辑是否正确 |

---

## 7. Oracle 特性支持矩阵

| 特性 | 支持状态 | 优先级 |
|------|----------|--------|
| 静态 SQL | ✅ Stage 3A | P0 |
| 动态 SQL | 🟡 Stage 3B | P0 |
| 显式/隐式游标 | ✅ Stage 3A | P0 |
| 包 (Package) | ✅ Stage 1-2 | P0 |
| 嵌套子程序 | ✅ Stage 1-2 | P0 |
| REF CURSOR | 🟡 Stage 3B | P1 |
| %TYPE / %ROWTYPE | 🟡 Stage 2 | P1 |
| BULK COLLECT | ⚠️ 待定 | P2 |
| 触发器 | ✅ Stage 1 | P2 |
| 自治事务 | ✅ Stage 3A | P1 |

---

## 8. 实现状态

| Stage | 状态 | 路径 |
|-------|------|------|
| Stage 1 | ✅ 完成 | `parsers/plsql/` |
| Stage 2 | ✅ 完成 | `parsers/plsql/visitor.py` |
| Stage 3A | 🚧 设计中 | 待创建 `convert/rules_engine/` |
| Stage 3B | 🚧 设计中 | 待创建 `convert/ai_enhancer/` |
| Stage 4 | 🚧 待实现 | - |
| Stage 5 | 🚧 待实现 | - |

---

## 9. 下一步计划

1. **Stage 3A**: 实现规则引擎转换
2. **Stage 4**: 实现代码生成器
3. **Stage 5**: 实现验证器
4. **Stage 0**: 补充预处理
5. **Stage 3B**: 补充 AI 增强接口
