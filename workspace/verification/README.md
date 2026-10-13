# 验证框架

Oracle → PostgreSQL 转换质量的独立 4 层验证系统。

## 架构

```
verification/
├── __init__.py          # 统一导出 + 向后兼容
├── models.py            # 数据模型 (Issue, LevelResult, CaseResult 等)
├── patterns.py           # 静态检测规则表
├── sqlsplit.py          # dollar-quote 感知语句切分
├── static_check.py       # L1: 静态分析
├── syntax_check.py       # L2: 语法验证
├── pg_sandbox.py         # L3: PG 沙箱
├── behavior_check.py     # L4: 行为等价
├── framework.py          # 编排器 / 评分 / Gate
└── report.py            # 报告渲染
```

## 验证层级

| Level | 名称 | 权重 | 检测内容 |
|-------|------|------|----------|
| L1 | 静态分析 | 15% | Oracle 残留 (VARCHAR2, SYSDATE, ROWNUM)、逻辑错误 (IF NULL)、占位符 (TODO, NEEDS_MANUAL) |
| L2 | 语法验证 | 20% | pglast 逐语句解析、BEGIN/END 块配对 |
| L3 | 安装验证 | 25% | 在隔离 schema 中真实建对象、带 SQLSTATE |
| L4 | 行为等价 | 40% | 金标准用例断言 (scalar/rows/procedure) |

## 使用方式

### 基础用法

```python
from verification import (
    Verifier, VerificationConfig, Artifact,
    static_analysis, check_pglast,
    render_report, render_suite
)

# 配置
config = VerificationConfig(
    use_pg=True,           # 是否启用 PG 验证
    schema="ora2pg_verify", # 沙箱 schema
    drop_schema_on_exit=True,
)

# 单制品验证
with Verifier(config) as v:
    report = v.verify_code("my_function", pg_code)
    print(f"Score: {report.overall_score}% - {report.grade}")
    print(f"Gate: {'PASS' if report.gate_passed else 'FAIL'}")
```

### 项目级验证

```python
from verification import Artifact, load_cases

artifacts = [
    Artifact("table.sql", "CREATE TABLE t(id INT);"),
    Artifact("func.sql", "CREATE FUNCTION f() RETURNS INT ... ;"),
]
cases = load_cases("golden_cases.json")

with Verifier(config) as v:
    suite = v.verify_suite("my_project", artifacts, cases=cases)

# 报告
from verification import write_json, write_text, render_suite
write_json(suite, "report.json")
write_text(render_suite(suite), "report.md")
```

### 仅 L1/L2（不连 PG）

```python
config = VerificationConfig(use_pg=False)
with Verifier(config) as v:
    report = v.verify_code("test", pg_code)
    # L1 和 L2 结果在 report.levels
```

### 独立使用各组件

```python
# L1 静态分析
from verification import static_analysis, score_static, summarize
issues = static_analysis(code)
score = score_static(issues, line_count)
summary = summarize(issues)

# L2 语法检查
from verification import check_pglast, check_structure, score_syntax
pglast_result = check_pglast(code)
structure = check_structure(code)
score = score_syntax(pglast_result, structure)

# SQL 工具
from verification import split_statements, classify, extract_object_name
stmts = split_statements(sql)
kind = classify("CREATE TABLE ...")
name = extract_object_name("CREATE TABLE foo ...")

# PG 沙箱
from verification import PGSandbox
with PGSandbox(schema="test_schema") as sb:
    sb.reset()
    installs = sb.install_script(ddl_sql)
    print(f"Installed: {sum(1 for i in installs if i.installed)}")
```

## 评分算法

### L1 评分
```
score = max(0, 100 - penalty / (line_count / 100))
penalty = Σ(count × severity_weight)
severity_weight: critical=12, major=5, minor=1.5, info=0.5
```
存在 critical 问题封顶 40 分。

### L2 评分
```
score = 100 - 80 × (failed / total) - structure_penalty
```

### L3 评分
```
score = 100 × (installed / total)
```

### L4 评分
```
score = 100 × (passed / applicable)
applicable = pass + fail + error (不含 skipped)
```

### 总分
跳过层按比例归一化权重。

## Gate 判定

| 条件 | 结果 |
|------|------|
| L1 有 critical 逻辑错误 | FAIL |
| L2 有解析失败 | FAIL |
| L3 有安装失败 | FAIL |
| L4 无可执行用例 | FAIL |
| L4 覆盖率 < 50% | FAIL |
| L4 有用例失败 | FAIL |
| 转换无产出 | FAIL |

## 数据模型

```python
@dataclass
class Issue:
    code: str       # oracle_residual / logic_error / placeholder / invalid_syntax
    name: str
    severity: str   # critical / major / minor / info
    count: int
    samples: List[str]

@dataclass
class LevelResult:
    level: str      # L1 / L2 / L3 / L4
    name: str
    score: float
    weight: float
    passed: bool
    skipped: bool
    details: dict

@dataclass
class VerificationReport:
    target: str
    levels: List[LevelResult]
    cases: List[CaseResult]
    overall_score: float
    grade: str
    gate_passed: bool
    gate_failures: List[str]
```

## 向后兼容

```python
# 旧 API
from verification import full_verification, LEGACY_ALIASES
result = full_verification(input_code, output_code, use_pg=True)

# 兼容别名
LEGACY_ALIASES = {
    'check_pg_syntax_via_pglast': 'check_pglast',
    'PGExecutor': 'PGSandbox',
    'score_level1': 'score_static',
    'score_level2': 'score_syntax',
}
```
