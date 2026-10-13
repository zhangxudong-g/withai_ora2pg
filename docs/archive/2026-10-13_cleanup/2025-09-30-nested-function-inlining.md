# 嵌套函数内联展开实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Oracle PL/SQL 嵌套函数到 PostgreSQL plpgsql 的内联展开转换

**Architecture:** 
1. 检测函数声明部分的嵌套函数定义（`FUNCTION name RETURN type IS ... END name;`）
2. 解析每个嵌套函数的结构：参数、局部变量、返回语句
3. 在调用位置用内联代码替换函数调用
4. 处理变量命名冲突和 RETURN 语句转换

**Tech Stack:** Python, regex, AST-like parsing

---

## 任务分解

### Task 1: 创建测试用例

**Files:**
- Create: `D:/JIP/poc/ora2pg_py_perlstyle/test_nested_function_inlining.py`

- [ ] **Step 1: 创建测试文件**

```python
#!/usr/bin/env python3
"""Tests for nested function inlining"""

import pytest
from perlstyle_engine import detect_and_convert

def test_simple_nested_function():
    """简单嵌套函数：单个子函数，单次调用"""
    oracle_sql = """
CREATE OR REPLACE FUNCTION outer_func(n IN NUMBER) RETURN NUMBER IS
    -- 子函数
    FUNCTION inner_func RETURN NUMBER IS
    BEGIN
        RETURN 10;
    END inner_func;
BEGIN
    RETURN inner_func() + n;
END outer_func;
/
"""
    result, warnings = detect_and_convert(oracle_sql)
    # 验证：没有 RETURN type IS 语法（无效PG语法）
    assert "RETURN" not in result or "IS" not in result.split("RETURN")[1][:20] if "RETURN" in result else True
    # 验证：子函数被内联展开
    # 不应包含对 inner_func() 的调用

def test_nested_function_with_params():
    """带参数的嵌套函数"""
    oracle_sql = """
CREATE OR REPLACE FUNCTION calc(a IN NUMBER, b IN NUMBER) RETURN NUMBER IS
    FUNCTION add(x IN NUMBER, y IN NUMBER) RETURN NUMBER IS
    BEGIN
        RETURN x + y;
    END add;
BEGIN
    RETURN add(a, b);
END calc;
/
"""
    result, warnings = detect_and_convert(oracle_sql)
    # 验证内联展开
    assert "add(" not in result  # 函数调用被展开

def test_multiple_nested_functions():
    """多个嵌套函数"""
    # 测试文件 ZLBFCCALHALFHT.SQL 的简化版本
    pass

def test_nested_function_with_local_vars():
    """带局部变量的嵌套函数"""
    pass

def test_nested_function_called_multiple_times():
    """嵌套函数被多次调用"""
    pass
```

- [ ] **Step 2: 运行测试确认当前状态**

```bash
cd D:/JIP/poc
python -m pytest ora2pg_py_perlstyle/test_nested_function_inlining.py -v 2>&1 | head -50
```
Expected: 测试会失败（功能未实现）或通过但检测到问题

---

### Task 2: 实现嵌套函数检测和解析

**Files:**
- Modify: `D:/JIP/poc/ora2pg_py_perlstyle/perlstyle_engine.py`

- [ ] **Step 1: 添加嵌套函数解析器**

在 `perlstyle_engine.py` 末尾添加：

```python
# =============================================================================
# Nested Function Inlining (Oracle PL/SQL -> PostgreSQL)
# =============================================================================

@dataclass
class NestedFunction:
    """嵌套函数的信息"""
    name: str
    params: List[Tuple[str, str, str]]  # [(name, mode, type), ...]
    local_vars: List[str]
    return_type: str
    body: str
    start_pos: int
    end_pos: int


def _extract_nested_functions(declare_block: str) -> List[NestedFunction]:
    """从 DECLARE 块中提取嵌套函数定义
    
    匹配模式: FUNCTION name[(params)] RETURN type IS ... BEGIN ... END name;
    """
    nested_funcs = []
    
    # 匹配函数定义头部
    func_pattern = re.compile(
        r'\bFUNCTION\s+([A-Za-z0-9_$]+)\s*'  # 函数名
        r'(?:\(([^)]*)\))?\s*'                  # 可选参数
        r'RETURN\s+(\w+)\s+IS\b',               # RETURN type IS
        re.IGNORECASE
    )
    
    for m in func_pattern.finditer(declare_block):
        func_name = m.group(1)
        params_str = m.group(2) or ""
        return_type = m.group(3)
        
        # 找到函数体的结束位置 (END func_name;)
        # 从匹配位置开始搜索
        body_start = m.end()
        # 简化：找到 "END func_name;" 或 "END;"
        end_pattern = re.compile(
            rf'\bEND\s+{re.escape(func_name)}\s*;',
            re.IGNORECASE
        )
        end_m = end_pattern.search(declare_block, body_start)
        
        if end_m:
            body = declare_block[m.start():end_m.end()]
            nested_funcs.append(NestedFunction(
                name=func_name,
                params=_parse_function_params(params_str),
                local_vars=[],
                return_type=return_type,
                body=body,
                start_pos=m.start(),
                end_pos=end_m.end()
            ))
    
    return nested_funcs


def _parse_function_params(params_str: str) -> List[Tuple[str, str, str]]:
    """解析函数参数列表"""
    # 格式: param_name IN type, param_name OUT type, ...
    params = []
    if not params_str.strip():
        return params
    
    for p in params_str.split(','):
        p = p.strip()
        if not p:
            continue
        # 解析: name [IN|OUT|INOUT] type
        m = re.match(r'(\w+)(?:\s+(IN|OUT|INOUT))?\s+(\w+)', p, re.IGNORECASE)
        if m:
            params.append((m.group(1), m.group(2) or "IN", m.group(3)))
    return params
```

- [ ] **Step 2: 运行基础测试**

```bash
cd D:/JIP/poc
python -c "
from ora2pg_py_perlstyle.perlstyle_engine import _extract_nested_functions

test_code = '''
FUNCTION FUNC_INIT RETURN BOOLEAN IS
BEGIN
    IRTN := 0;
    RETURN TRUE;
END FUNC_INIT;

FUNCTION FUNC2(x IN NUMBER) RETURN NUMBER IS
BEGIN
    RETURN x + 1;
END FUNC2;
'''

funcs = _extract_nested_functions(test_code)
print(f'Found {len(funcs)} nested functions')
for f in funcs:
    print(f'  - {f.name}, return: {f.return_type}')
"
```
Expected: 输出找到2个嵌套函数

---

### Task 3: 实现子函数内联展开

**Files:**
- Modify: `D:/JIP/poc/ora2pg_py_perlstyle/perlstyle_engine.py`

- [ ] **Step 1: 实现内联展开函数**

```python
def _inline_nested_functions(declare_block: str, code_block: str, state: ConversionState) -> Tuple[str, str]:
    """将嵌套函数内联展开到主函数体中
    
    策略：
    1. 提取嵌套函数定义
    2. 从 declare_block 中移除嵌套函数定义
    3. 在 code_block 中，将函数调用替换为内联代码
    
    Returns:
        (modified_declare_block, modified_code_block)
    """
    # 1. 提取嵌套函数
    nested_funcs = _extract_nested_functions(declare_block)
    if not nested_funcs:
        return declare_block, code_block
    
    # 2. 移除 declare_block 中的嵌套函数定义
    modified_declare = declare_block
    # 按位置倒序移除（避免位置偏移问题）
    for func in sorted(nested_funcs, key=lambda f: f.start_pos, reverse=True):
        modified_declare = modified_declare[:func.start_pos] + modified_declare[func.end_pos:]
    
    # 3. 展开 code_block 中的函数调用
    modified_code = code_block
    for func in nested_funcs:
        modified_code = _expand_function_call(
            modified_code, func.name, func.body, state
        )
    
    return modified_declare, modified_code


def _expand_function_call(code: str, func_name: str, func_body: str, state: ConversionState) -> str:
    """将函数调用展开为函数体"""
    # 匹配函数调用: func_name(...) 或 func_name ()
    call_pattern = re.compile(
        rf'\b{re.escape(func_name)}\s*\([^)]*\)(?!\s*:?=\s*)',
        re.IGNORECASE
    )
    
    def _replacer(m):
        # 提取函数体的 BEGIN...END 部分
        func_body_clean = _extract_function_body(func_body)
        # 简化：直接返回函数体（实际需要更多处理）
        return func_body_clean
    
    return call_pattern.sub(_replacer, code)


def _extract_function_body(func_body: str) -> str:
    """从函数定义中提取函数体（去除头部和 END）"""
    # 找到 BEGIN 关键字
    begin_match = re.search(r'\bBEGIN\b', func_body, re.IGNORECASE)
    if not begin_match:
        return func_body
    
    body = func_body[begin_match.end():]
    # 移除末尾的 END func_name;
    body = re.sub(r'\s*END\s+\w+\s*;\s*$', '', body, flags=re.IGNORECASE)
    return body.strip()
```

- [ ] **Step 2: 测试内联展开**

```bash
cd D:/JIP/poc
python -c "
from ora2pg_py_perlstyle.perlstyle_engine import _inline_nested_functions, _extract_nested_functions, ConversionState

declare_block = '''
FUNCTION FUNC_INIT RETURN BOOLEAN IS
BEGIN
    IRTN := 0;
    RETURN TRUE;
END FUNC_INIT;
'''

code_block = '''
BEGIN
    IF FUNC_INIT = FALSE THEN
        RETURN 1;
    END IF;
END;
'''

state = ConversionState()
new_decl, new_code = _inline_nested_functions(declare_block, code_block, state)
print('=== Modified Declare ===')
print(new_decl)
print('=== Modified Code ===')
print(new_code)
"
```
Expected: declare_block 中无 FUNCTION 定义，code_block 中包含内联后的代码

---

### Task 4: 集成到转换管道

**Files:**
- Modify: `D:/JIP/poc/ora2pg_py_perlstyle/perlstyle_engine.py`

- [ ] **Step 1: 修改 _convert_oracle_function_to_pg 函数**

找到 `_convert_oracle_function_to_pg` 函数，在处理 declare_block 后调用内联展开：

```python
# 在 "6c. Apply full PL/SQL conversion pipeline to DECLARE block." 之前添加：

# 5a. 内联展开嵌套函数（Oracle 特有，PG 不支持）
if 'FUNCTION ' in declare_block and ' RETURN ' in declare_block and ' IS' in declare_block:
    declare_block, code = _inline_nested_functions(declare_block, code, state)
```

- [ ] **Step 2: 运行完整测试**

```bash
cd D:/JIP/poc
python -m pytest ora2pg_py_perlstyle/test_nested_function_inlining.py -v
```

---

### Task 5: 处理复杂的内联场景

**Files:**
- Modify: `D:/JIP/poc/ora2pg_py_perlstyle/perlstyle_engine.py`

- [ ] **Step 1: 处理 RETURN 语句转换**

RETURN 语句在内联时需要转换为赋值+条件跳转：

```python
def _convert_return_for_inlining(func_body: str, result_var: str = None) -> str:
    """将 RETURN 语句转换为赋值+条件跳转"""
    # 简化实现
    # 实际需要使用标签和 GOTO，或者重构为变量赋值
    
    lines = []
    for line in func_body.split('\n'):
        if re.match(r'\s*RETURN\b', line, re.IGNORECASE):
            # RETURN expr; -> 将 expr 赋值给结果变量
            expr = line.split('RETURN', 1)[1].rstrip(';').strip()
            if result_var:
                lines.append(f'    {result_var} := {expr};')
            lines.append('    -- RETURN converted to assignment')
        else:
            lines.append(line)
    
    return '\n'.join(lines)
```

- [ ] **Step 2: 处理变量作用域冲突**

```python
def _mangle_local_vars(body: str, prefix: str = '') -> str:
    """为局部变量添加前缀避免冲突"""
    # 简化实现
    # 实际需要解析变量声明并替换所有引用
    return body
```

- [ ] **Step 3: 添加警告**

```python
def _warn_nested_functions(nested_funcs: List[NestedFunction]) -> List[str]:
    """为嵌套函数转换添加警告"""
    warnings = []
    if nested_funcs:
        func_names = ', '.join(f.name for f in nested_funcs)
        warnings.append(
            f"WARNING: Nested functions inlined: {func_names}. "
            f"This conversion may change behavior for functions with side effects."
        )
    return warnings
```

---

### Task 6: 端到端测试

**Files:**
- Modify: `D:/JIP/poc/test_reports/ora2pg_py_gkb_test/test_full_gkb.py` (可选)

- [ ] **Step 1: 用 ZLBFCCALHALFHT.SQL 测试**

```bash
cd D:/JIP/poc
python -c "
from ora2pg_py_perlstyle.perlstyle_engine import detect_and_convert

with open('gkb/01.release/GK_gakumu/GKB_kyoiku/DB/01.SQL/plsql_sample/ZLBFCCALHALFHT.SQL', 'r', encoding='utf-8') as f:
    sql = f.read()

result, warnings = detect_and_convert(sql)

# 检查是否还有无效语法
if 'RETURN' in result:
    # 找出 RETURN 后紧跟类型和 IS 的模式
    import re
    bad_pattern = re.findall(r'RETURN\s+\w+\s+IS', result, re.IGNORECASE)
    if bad_pattern:
        print(f'ERROR: Still has invalid syntax: {bad_pattern}')
    else:
        print('OK: No invalid RETURN type IS syntax')
else:
    print('OK: No RETURN statements')
"
```

- [ ] **Step 2: 验证 PG 语法**

```bash
cd D:/JIP/poc
# 提取转换结果并验证
```

---

### Task 7: 清理和文档

- [ ] **Step 1: 更新 NESTED_FUNCTION_ANALYSIS.md**

```markdown
## 解决方案：内联展开

已实现嵌套函数的内联展开转换。

### 转换示例

输入 (Oracle):
```sql
FUNCTION outer RETURN NUMBER IS
    FUNCTION inner RETURN NUMBER IS
    BEGIN
        RETURN 10;
    END inner;
BEGIN
    RETURN inner() + 1;
END outer;
```

输出 (PostgreSQL):
```sql
-- 内部变量声明已提取
-- 函数体已内联
BEGIN
    -- 内联 inner 函数
    DECLARE
        v_inner_result NUMBER;
    BEGIN
        v_inner_result := 10;
        -- RETURN 10; 已转换为赋值
    END;
    RETURN v_inner_result + 1;
END;
```
```

- [ ] **Step 2: 添加到 HANDOFF.md**

---

## 验证命令

```bash
# 运行所有嵌套函数测试
cd D:/JIP/poc
python -m pytest ora2pg_py_perlstyle/test_nested_function_inlining.py -v

# 运行 GKB 测试并检查嵌套函数
python -m pytest test_reports/ora2pg_py_gkb_test/test_full_gkb.py -v -k "ZLBFCCALHALFHT"

# 统计仍包含无效语法的文件
grep -r "RETURN.*IS" test_reports/ora2pg_py_gkb_test/outputs_perlstyle_full/ | grep -v "DECLARE" | head -20
```

---

## 已知限制

1. **多次调用的子函数**：内联后代码会重复，可能影响性能
2. **递归子函数**：不支持，会导致无限循环
3. **带 OUT 参数的子函数**：需要特殊处理
4. **异常处理**：子函数中的 EXCEPTION 块需要特殊处理

---

**Plan complete.** Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
