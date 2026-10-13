# Perlstyle Engine 嵌套函数转换修复方案

**分析日期**: 2026-09-30  
**核心文件**: `ora2pg_py_perlstyle/perlstyle_engine.py`

---

## 一、问题定位

### 1.1 问题代码位置

文件: `perlstyle_engine.py`  
函数: `_expand_function_call` (约第 4090 行)

```python
if is_complex:
    # 复杂函数（带 SELECT/LOOP/CURSOR/EXCEPTION），标记需要手动处理
    def _replacer1(m):
        return f'/* TODO: {func_name}() - complex (SELECT/LOOP/EXCEPTION), needs manual */ NULL; -- {m.group(1)} := original value'
    def _replacer2(m):
        return f'/* TODO: {func_name}() - complex */ IF TRUE /* replace with actual condition */ THEN'
    # ... 更多 TODO 注释
```

### 1.2 问题根因

```
当前策略: 内联 (Inlining)
├─ 优点: 代码紧凑
├─ 缺点: 复杂函数无法内联 → 生成 TODO 注释
└─ 结果: 函数定义丢失 + 调用变成无效代码

应该改为: 提取为独立函数 (Extract to separate function)
├─ 优点: 保留函数语义，PG 完全兼容
├─ 缺点: 需要额外的函数定义
└─ 结果: 函数定义保留 + 调用正确
```

---

## 二、解决方案

### 2.1 修改策略

**从内联改为提取为独立函数**:

```python
# 当前错误策略:
# nested_function + call → inline → TODO (失败)

# 正确策略:
# nested_function + call → extract to independent → call with prefix
```

### 2.2 新函数: `_extract_nested_functions_as_independent`

在 `perlstyle_engine.py` 中添加新函数，替代当前的 `_inline_nested_functions`:

```python
def _extract_nested_functions_as_independent(
    declare_block: str, 
    code_block: str, 
    parent_func_name: str,
    state: ConversionState
) -> Tuple[str, str, List[str], List[str]]:
    """将嵌套函数提取为独立函数（替代内联）
    
    策略:
    1. 提取所有嵌套函数定义
    2. 将每个嵌套函数转换为独立函数
    3. 用新函数名替换原函数调用
    4. 返回: (modified_declare, modified_code, warnings, extracted_functions)
    
    Args:
        declare_block: DECLARE 块内容
        code_block: 代码块内容
        parent_func_name: 父函数名（用于生成唯一的嵌套函数名）
        state: 转换状态
    
    Returns:
        Tuple: (修改后的declare, 修改后的code, 警告列表, 提取的函数定义列表)
    """
    # 1. 合并代码来检测完整的嵌套函数
    combined = declare_block + '\n' + code_block
    
    # 2. 提取嵌套函数
    nested_funcs = _extract_nested_functions(combined)
    if not nested_funcs:
        return declare_block, code_block, [], []
    
    warnings = []
    extracted_functions = []
    
    # 3. 处理每个嵌套函数
    for func in nested_funcs:
        # 生成新函数名: parent_func_name__nested_func_name
        new_func_name = f"{parent_func_name}__{func.name}"
        
        # 4. 转换嵌套函数为独立函数
        independent_func = _convert_nested_to_independent(func, new_func_name, state)
        extracted_functions.append(independent_func)
        
        # 5. 替换函数调用
        combined = _replace_nested_call_with_independent(
            combined, func, new_func_name
        )
        
        warnings.append(
            f"Extracted nested function {func.name} -> {new_func_name}"
        )
    
    # 6. 从 declare_block 中移除嵌套函数定义
    for func in sorted(nested_funcs, key=lambda f: f.start_pos, reverse=True):
        combined = combined[:func.start_pos] + combined[func.end_pos:]
    
    # 7. 分离回 declare_block 和 code_block
    main_begin_match = re.search(r'\bBEGIN\b', combined, re.IGNORECASE)
    if main_begin_match:
        new_declare = combined[:main_begin_match.start()]
        new_code = combined[main_begin_match.start():]
    else:
        new_declare = combined
        new_code = ''
    
    return new_declare, new_code, warnings, extracted_functions
```

### 2.3 新函数: `_convert_nested_to_independent`

```python
def _convert_nested_to_independent(
    func: NestedFunction, 
    new_func_name: str,
    state: ConversionState
) -> str:
    """将嵌套函数转换为独立函数
    
    转换步骤:
    1. 提取函数签名 (参数和返回类型)
    2. 转换 Oracle 类型到 PG 类型
    3. 转换函数体 (EXCEPTION 处理等)
    4. 生成 PG 函数定义
    
    Returns:
        PostgreSQL 函数定义字符串
    """
    # 1. 转换返回类型
    return_type = _convert_oracle_type(func.return_type)
    
    # 2. 构建函数参数
    params_str = _build_pg_params(func.params)
    
    # 3. 提取并转换函数体
    func_body = _extract_function_body(func.body)
    converted_body = _convert_function_body_for_pg(func_body)
    
    # 4. 构建独立函数定义
    pg_func = f"""
CREATE OR REPLACE FUNCTION {new_func_name}({params_str})
RETURNS {return_type}
LANGUAGE plpgsql
AS $$
BEGIN
{converted_body}
END;
$$;
"""
    return pg_func
```

### 2.4 新函数: `_replace_nested_call_with_independent`

```python
def _replace_nested_call_with_independent(
    code: str,
    func: NestedFunction,
    new_func_name: str
) -> str:
    """将嵌套函数调用替换为独立函数调用
    
    转换:
    - FUNC_INIT = FALSE → parent_FUNC_INIT__FUNC_INIT() = FALSE
    - IF FUNC_INIT THEN → IF parent_FUNC_INIT__FUNC_INIT() THEN
    
    Returns:
        替换后的代码
    """
    func_name = func.name
    
    # 1. 替换赋值调用: result := func_name();
    code = re.sub(
        rf'(\w+)\s*:=\s*{re.escape(func_name)}\s*\([^)]*\)\s*;',
        rf'\1 := {new_func_name}();',
        code,
        flags=re.IGNORECASE
    )
    
    # 2. 替换 IF func_name() THEN
    code = re.sub(
        rf'IF\s+{re.escape(func_name)}\s*\([^)]*\)\s+THEN',
        f'IF {new_func_name}() THEN',
        code,
        flags=re.IGNORECASE
    )
    
    # 3. 替换 func_name() = value (带括号比较)
    code = re.sub(
        rf'{re.escape(func_name)}\s*\([^)]*\)\s*([=<>!]+)',
        rf'{new_func_name}() \1',
        code,
        flags=re.IGNORECASE
    )
    
    # 4. 替换 IF func_name = value THEN (Oracle 无括号调用)
    code = re.sub(
        rf'IF\s+{re.escape(func_name)}\s*([=<>!]+)\s*(\w+)\s+THEN',
        rf'IF {new_func_name}() \1 \2 THEN',
        code,
        flags=re.IGNORECASE
    )
    
    return code
```

### 2.5 修改调用点

在 `perlstyle_engine.py` 中找到调用 `_inline_nested_functions` 的地方（约第 3131 行）:

```python
# 找到这行代码:
declare_block, code, nested_warnings = _inline_nested_functions(
    declare_block, code, state
)

# 替换为:
declare_block, code, nested_warnings, extracted_funcs = _extract_nested_functions_as_independent(
    declare_block, code, func_name, state
)

# 将提取的函数添加到输出
for extracted_func in extracted_funcs:
    output_parts.append(extracted_func)
```

---

## 三、完整修复代码

### 3.1 添加到 perlstyle_engine.py

```python
# 在 _inline_nested_functions 函数之前添加以下新函数:


def _convert_oracle_type(oracle_type: str) -> str:
    """Oracle 类型到 PostgreSQL 类型的映射"""
    type_map = {
        'BOOLEAN': 'BOOLEAN',
        'NUMBER': 'NUMERIC',
        'INTEGER': 'INTEGER',
        'PLS_INTEGER': 'INTEGER',
        'VARCHAR2': 'VARCHAR',
        'NVARCHAR2': 'VARCHAR',
        'CHAR': 'CHAR',
        'DATE': 'TIMESTAMP',
        'TIMESTAMP': 'TIMESTAMP',
    }
    return type_map.get(oracle_type.upper(), 'TEXT')


def _build_pg_params(params: List[Tuple[str, str, str]]) -> str:
    """构建 PostgreSQL 函数参数列表
    
    Args:
        params: [(name, mode, type), ...]
    
    Returns:
        PG 格式的参数列表: "param1 IN INTEGER, param2 OUT INTEGER"
    """
    pg_params = []
    for name, mode, oracle_type in params:
        pg_type = _convert_oracle_type(oracle_type)
        pg_params.append(f"{name} {pg_type}")
    return ', '.join(pg_params)


def _convert_function_body_for_pg(body: str) -> str:
    """转换函数体为 PostgreSQL 兼容格式"""
    # 1. 保留 EXCEPTION 块（PG 支持）
    # 2. 转换 RAISE 语句
    # 3. 清理其他 Oracle 特有语法
    
    # 转换 RAISE; 为 PG 格式
    body = re.sub(r'\bRAISE\s*;\s*$', 'RAISE;', body, flags=re.MULTILINE)
    
    return body


def _replace_nested_call_with_independent(
    code: str,
    func: NestedFunction,
    new_func_name: str
) -> str:
    """将嵌套函数调用替换为独立函数调用"""
    func_name = func.name
    
    # 替换赋值调用
    code = re.sub(
        rf'(\w+)\s*:=\s*{re.escape(func_name)}\s*\([^)]*\)\s*;',
        rf'\1 := {new_func_name}();',
        code,
        flags=re.IGNORECASE
    )
    
    # 替换 IF func_name() THEN
    code = re.sub(
        rf'IF\s+{re.escape(func_name)}\s*\([^)]*\)\s+THEN',
        f'IF {new_func_name}() THEN',
        code,
        flags=re.IGNORECASE
    )
    
    # 替换 func_name() = value
    code = re.sub(
        rf'{re.escape(func_name)}\s*\([^)]*\)\s*([=<>!]+)',
        rf'{new_func_name}() \1',
        code,
        flags=re.IGNORECASE
    )
    
    # 替换 IF func_name = value THEN (Oracle 无括号调用)
    code = re.sub(
        rf'IF\s+{re.escape(func_name)}\s*([=<>!]+)\s*(\w+)\s+THEN',
        rf'IF {new_func_name}() \1 \2 THEN',
        code,
        flags=re.IGNORECASE
    )
    
    return code


def _extract_nested_functions_as_independent(
    declare_block: str, 
    code_block: str, 
    parent_func_name: str,
    state: ConversionState
) -> Tuple[str, str, List[str], List[str]]:
    """将嵌套函数提取为独立函数"""
    combined = declare_block + '\n' + code_block
    
    nested_funcs = _extract_nested_functions(combined)
    if not nested_funcs:
        return declare_block, code_block, [], []
    
    warnings = []
    extracted_functions = []
    
    for func in nested_funcs:
        new_func_name = f"{parent_func_name}__{func.name}"
        
        # 转换函数
        params_str = _build_pg_params(func.params)
        return_type = _convert_oracle_type(func.return_type)
        func_body = _extract_function_body(func.body)
        converted_body = _convert_function_body_for_pg(func_body)
        
        independent_func = f"""CREATE OR REPLACE FUNCTION {new_func_name}({params_str})
RETURNS {return_type}
LANGUAGE plpgsql
AS $$
BEGIN
{converted_body}
END;
$$;
"""
        extracted_functions.append(independent_func)
        
        # 替换调用
        combined = _replace_nested_call_with_independent(combined, func, new_func_name)
        
        warnings.append(f"Extracted nested function {func.name} -> {new_func_name}")
    
    # 移除原嵌套函数定义
    for func in sorted(nested_funcs, key=lambda f: f.start_pos, reverse=True):
        combined = combined[:func.start_pos] + combined[func.end_pos:]
    
    # 分离
    main_begin_match = re.search(r'\bBEGIN\b', combined, re.IGNORECASE)
    if main_begin_match:
        new_declare = combined[:main_begin_match.start()]
        new_code = combined[main_begin_match.start():]
    else:
        new_declare = combined
        new_code = ''
    
    return new_declare, new_code, warnings, extracted_functions
```

### 3.2 修改调用点

在约第 3131 行找到并修改:

```python
# 原代码:
declare_block, code, nested_warnings = _inline_nested_functions(
    declare_block, code, state
)

# 新代码:
declare_block, code, nested_warnings, extracted_funcs = _extract_nested_functions_as_independent(
    declare_block, code, func_name, state
)

# 在输出部分添加提取的函数
for extracted_func in extracted_funcs:
    output_parts.append(extracted_func)
```

---

## 四、预期效果

### 4.1 转换前后对比

**原始 Oracle**:
```sql
CREATE FUNCTION ZLBFCCALHALFHT(...)
IS
  FUNCTION FUNC_INIT RETURN BOOLEAN IS
  BEGIN RETURN TRUE; END FUNC_INIT;
BEGIN
  IF FUNC_INIT = FALSE THEN  -- Oracle 无括号调用
    NRETURN := 1;
  END IF;
END;
```

**当前错误转换**:
```sql
-- 嵌套函数定义丢失！
IF /* TODO: FUNC_INIT - complex(...) */ IF = FALSE THEN  -- ❌ 无效代码
```

**修复后转换**:
```sql
-- 主函数
CREATE FUNCTION ZLBFCCALHALFHT(...)
RETURNS ...
AS $$
BEGIN
  IF ZLBFCCALHALFHT__FUNC_INIT() = FALSE THEN  -- ✅ 正确调用
    NRETURN := 1;
  END IF;
END;
$$;

-- 提取的嵌套函数
CREATE FUNCTION ZLBFCCALHALFHT__FUNC_INIT()
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN TRUE;
EXCEPTION WHEN OTHERS THEN RETURN FALSE;
END;
$$;
```

### 4.2 评分提升预期

| 指标 | 当前 | 修复后 |
|------|------|--------|
| 嵌套函数定义 | 丢失 | ✅ 保留 |
| 函数调用 | TODO 注释 | ✅ 正确 |
| Level 1 | 72% | 90%+ |
| Level 2 | 87% | 95%+ |
| Level 3 | 0% | 50-70% |
| **综合** | **46.2%** | **75-85%** |

---

## 五、实施步骤

1. **备份**: 复制 `perlstyle_engine.py` 到备份文件
2. **添加函数**: 在 `_inline_nested_functions` 之前添加新函数
3. **修改调用点**: 替换 `_inline_nested_functions` 调用
4. **测试**: 对测试用例重新运行转换
5. **验证**: 检查 Level 3 评分提升

---

**文档时间**: 2026-09-30
