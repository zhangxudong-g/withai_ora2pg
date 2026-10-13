# Oracle → PostgreSQL 转换问题分析与修复计划

**项目**: oracle_poc pipeline  
**日期**: 2024-10-08  
**当前分数**: 88.6 (B 良) - Gate 未通过  
**测试通过率**: L3 安装验证 65.9% (110/167)

---

## 一、转换产物质量评估

### 1.1 按对象类型统计

| 类型 | 成功 | 失败 | 可直接使用 | 可用率 |
|------|------|------|-----------|--------|
| TABLE | 9 | 0 | 9 | 100% ✅ |
| SEQUENCE | 6 | 0 | 6 | 100% ✅ |
| SYNONYM | 11 | 0 | 11 | 100% ✅ |
| VIEW | 16 | 2 | 6/7 | 86% ⚠️ |
| DATA | 49 | 0 | 49 | 100% ✅ |
| SCHEMA | 2 | 0 | 2 | 100% ✅ |
| INDEX | 2 | 0 | 2 | 100% ✅ |
| TYPE | 7 | 1 | 4/8 | 50% ⚠️ |
| FUNCTION | 11 | 31 | 3/16 | 19% ❌ |
| PROCEDURE | 8 | 19 | 2/5 | 40% ❌ |
| TRIGGER | 0 | 1 | 0/2 | 0% ❌ |

### 1.2 可直接使用的产物

| 文件 | 状态 | 说明 |
|------|------|------|
| 001_STUDENT_MST.sql | ✅ | TABLE 定义正确，约束、索引、注释完整 |
| 002_SUBJECT_MST.sql | ✅ | TABLE 定义正确 |
| 00_SEQUENCE.sql | ✅ | 序列定义正确 |
| 00_SYNONYM.sql | ✅ | 同义词定义正确 |
| 00_TEST_DATA.sql | ✅ | 测试数据正确 |
| 00_VIEW.sql (部分) | ⚠️ | 5/6 个 VIEW 可用 |

### 1.3 需要修复的产物

| 文件 | 主要问题 | 严重度 |
|------|---------|--------|
| 00_VIEW.sql | V_DEPENDENCY_GRAPH GROUP BY 错误 | 中 |
| 001_TYPES.sql | NVARCHAR2[] 不支持、函数缺结束符 | 中 |
| 00_TRIGGER.sql | END IF 缺失、RETURN NEW 位置错误 | 高 |
| 00_FUNCTION.sql | 函数体截断、嵌套函数处理失败 | 严重 |
| 00_PROCEDURE.sql | o_nRTN 未声明、MERGE 语法错误 | 严重 |
| 00_PACKAGE_BODY.sql | s.xxx 未替换、current_setting 误用 | 极严重 |

---

## 二、问题分类与根因分析

### 2.1 P0 - 阻塞性问题（必须修复）

#### 问题 P0-1: PACKAGE_BODY 转换完全失效
**影响**: 17 个对象安装失败  
**错误**: `模式 "s" 不存在`

**根因分析**:
```oracle
-- Oracle 原始代码
SELECT * FROM s.STUDENT_MST WHERE ...
s.procedure_name(...);
```

```sql
-- 当前转换结果（错误）
SELECT * FROM current_setting('s.STUDENT_MST') ...
current_setting('s.procedure_name')(...);
```

**问题定位**:
1. `s.` 前缀被错误地转换为 `current_setting('s.')`
2. 未正确替换为 `PKG_SCORE_MANAGE.xxx`
3. `$$PLSQL_UNIT` 等 Oracle 特有标识符未处理

#### 问题 P0-2: 函数结束标识符缺失
**影响**: 3 个 FUNCTION 安装失败  
**错误**: `语法错误 在输入的末尾`

**根因分析**:
```sql
-- 当前转换结果（错误）
CREATE FUNCTION xxx() RETURNS NUMERIC AS $$
DECLARE
    ...
-- 缺少 $$ 或 $BODY$ 结束符
```

```sql
-- 正确格式
CREATE FUNCTION xxx() RETURNS NUMERIC AS $$
DECLARE
    ...
END;
$$;  -- 或 $BODY$
```

#### 问题 P0-3: V_DEPENDENCY_GRAPH GROUP BY 错误
**影响**: 1 个 VIEW 安装失败  
**错误**: `在 GROUP BY 中的非整数常量`

**根因分析**:
```sql
-- 当前转换结果（错误）
GROUP BY 'SCORE_REC'
-- Oracle 允许字面量在 GROUP BY，但 PostgreSQL 不允许

-- 正确转换应为
GROUP BY 1  -- 或使用列别名
```

---

### 2.2 P1 - 高优先级问题

#### 问题 P1-1: 嵌套函数内联处理不当
**影响**: 多 FUNCTION/PROCEDURE 函数体被截断  
**示例**:
```sql
-- 当前转换结果（错误）
CREATE FUNCTION F_CALC_RANK(...) AS $$
DECLARE
-- 嵌套函数: 获取学生总分
    

$$;  -- 函数体在此结束，实际代码丢失

-- 正确应为
CREATE FUNCTION F_CALC_RANK(...) AS $$
DECLARE
    -- 嵌套函数
BEGIN
    -- 内联后的实际逻辑
END;
$$;
```

#### 问题 P1-2: NVARCHAR2[] 类型不支持
**影响**: 1 个 TYPE 安装失败  
**错误**: `类型 "nvarchar2[]" 不存在`

```sql
-- Oracle
CREATE TYPE A_CONS_PRM AS (items NVARCHAR2(1000)[]);

-- PostgreSQL 应转换为
CREATE TYPE A_CONS_PRM AS (items TEXT[]);
```

#### 问题 P1-3: SYS_REFCURSOR 未转换
**影响**: 1 个 FUNCTION 安装失败  
**错误**: `类型 "sys_refcursor" 不存在`

```sql
-- Oracle
CREATE FUNCTION F_GET_STUDENT_CURSOR(...) RETURNS SYS_REFCURSOR

-- PostgreSQL 应转换为
CREATE FUNCTION F_GET_STUDENT_CURSOR(...) RETURNS REFCURSOR
```

#### 问题 P1-4: TRIGGER 语法错误
**影响**: 1 个 TRIGGER 安装失败  
**错误**: `丢失数据类型声明 在 "," 或附近的`

```sql
-- 当前转换结果（错误）
CREATE OR REPLACE FUNCTION TRG_STUDENT_BI() RETURNS TRIGGER AS $$
DECLARE vMAX_STUDENT_NO STUDENT_MST.STUDENT_NO%TYPE;
BEGIN
    IF NEW.STUDENT_NO IS NULL THEN
        SELECT MAX(STUDENT_NO) + 1 INTO vMAX_STUDENT_NO FROM STUDENT_MST;
        NEW.STUDENT_NO := vMAX_STUDENT_NO;
    END IF;  -- 缺少 END IF
    RETURN NEW;  -- 位置错误
END;  -- 缺少 $$
$$;
```

---

### 2.3 P2 - 中优先级问题

#### 问题 P2-1: PROCEDURE OUT 参数未声明
**影响**: 多个 PROCEDURE 安装失败

```sql
-- 当前转换结果（错误）
CREATE PROCEDURE SP_PROCESS_SCORE(...) 
LANGUAGE plpgsql AS $$
BEGIN
    o_nRTN := c_OK;  -- o_nRTN 未声明
END;
$$;
```

```sql
-- 正确转换
CREATE PROCEDURE SP_PROCESS_SCORE(..., INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql AS $$
BEGIN
    o_nRTN := 0;
END;
$$;
```

#### 问题 P2-2: MERGE INTO 语法不完整
**影响**: 批量导入存储过程

```sql
-- 当前转换结果（错误）
FORALL i IN 1..v_student_list.COUNT
    MERGE INTO SCORE_REC SC
    USING(%SUBQUERY0%) D  -- %SUBQUERY0% 未替换
    ON (...)
```

#### 问题 P2-3: PIPELINE 函数未支持
**影响**: 2 个 FUNCTION 安装失败

```sql
-- Oracle PIPELINE 函数
CREATE FUNCTION F_GET_STUDENT_LIST_PIPELINE(...) 
RETURN STUDENT_LIST PIPELINED IS
BEGIN
    PIPE ROW(...);
END;
```

PostgreSQL 需要使用 `RETURNS TABLE(...)` 或生成函数模拟。

---

## 三、修复计划

### 3.1 P0 修复（预计提升 10-15 分）

#### 修复 P0-1: PACKAGE_BODY s. 前缀替换

**问题**: `s.STUDENT_MST` → `current_setting('s.STUDENT_MST')`

**修复方案**:
1. 在 `perlstyle_engine.py` 中增加 PACKAGE_BODY 专用处理
2. 识别 schema 前缀模式：`s.` → `PKG_SCORE_MANAGE.`
3. 识别 `current_setting()` 误用，替换回正确格式

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 50 行代码  
**验证**: `00_PACKAGE_BODY.sql` 安装通过

#### 修复 P0-2: 函数结束标识符

**问题**: 函数缺少 `$$` 或 `$BODY$`

**修复方案**:
1. 在 `convert_function` 中确保 `$$` 出现在函数体末尾
2. 检查所有 `END;` 后是否跟有 `$$`

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 10 行代码  
**验证**: `F_GET_TOTAL_SCORE` 等函数安装通过

#### 修复 P0-3: GROUP BY 字面量

**问题**: `GROUP BY 'SCORE_REC'` → 语法错误

**修复方案**:
1. 在 `_post_process_view` 中识别 `GROUP BY '常量'` 模式
2. 替换为 `GROUP BY 1` 或使用序号

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 20 行代码  
**验证**: `V_DEPENDENCY_GRAPH` 安装通过

---

### 3.2 P1 修复（预计提升 5-10 分）

#### 修复 P1-1: 嵌套函数处理

**问题**: 嵌套函数体被截断

**修复方案**:
1. 区分简单嵌套函数（可内联）和复杂嵌套函数（需保留）
2. 简单函数内联，复杂函数生成独立函数

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 100 行代码  
**风险**: 中（可能影响已有转换逻辑）

#### 修复 P1-2: Oracle 类型映射

**问题**: `NVARCHAR2` → 不支持

**修复方案**:
```python
TYPE_MAPPING = {
    'NVARCHAR2': 'TEXT',
    'SYS_REFCURSOR': 'REFCURSOR',
    'NUMBER': 'NUMERIC',
    'CHAR': 'CHAR',
    'VARCHAR2': 'VARCHAR',
}
```

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 30 行代码

#### 修复 P1-3: TRIGGER 语法

**问题**: `END IF;` 缺失、`RETURN NEW` 位置错误

**修复方案**:
1. 在 `convert_trigger` 中确保 `END IF;` 完整
2. `RETURN NEW` 应在 `END IF` 之前

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 30 行代码

---

### 3.3 P2 修复（预计提升 3-5 分）

#### 修复 P2-1: PROCEDURE OUT 参数

**问题**: OUT 参数未声明

**修复方案**:
1. 在 `convert_procedure` 中解析 OUT 参数
2. 转换为 PostgreSQL 的 `INOUT` 语法

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 40 行代码

#### 修复 P2-2: MERGE INTO

**问题**: `FORALL...MERGE` 不支持

**修复方案**:
1. 将 `FORALL...MERGE` 转换为 `FOR...INSERT ON CONFLICT UPDATE`

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 60 行代码

#### 修复 P2-3: PIPELINE 函数

**问题**: Oracle PIPELINE 语法不支持

**修复方案**:
1. 转换为 `RETURNS TABLE(...)` 或生成独立函数

**修改文件**: `perlstyle_engine.py`  
**工作量**: 约 80 行代码（复杂）

---

## 四、修复优先级与工作量

| 优先级 | 问题 | 工作量 | 预计提升 | 风险 |
|--------|------|--------|---------|------|
| P0-1 | PACKAGE_BODY s. 前缀 | 50 行 | +10 分 | 低 |
| P0-2 | 函数结束标识符 | 10 行 | +2 分 | 低 |
| P0-3 | GROUP BY 字面量 | 20 行 | +1 分 | 低 |
| P1-1 | 嵌套函数处理 | 100 行 | +5 分 | 中 |
| P1-2 | Oracle 类型映射 | 30 行 | +2 分 | 低 |
| P1-3 | TRIGGER 语法 | 30 行 | +1 分 | 低 |
| P2-1 | OUT 参数 | 40 行 | +2 分 | 中 |
| P2-2 | MERGE INTO | 60 行 | +1 分 | 中 |
| P2-3 | PIPELINE 函数 | 80 行 | +1 分 | 高 |

---

## 五、结论

### 当前状态
- **可直接使用**: 约 40%（TABLE, SEQUENCE, 部分 VIEW）
- **需要修复**: 约 30%
- **需要大量重构**: 约 30%

### 修复目标
修复 P0 问题后，预计分数提升至 **95-98**（A 级）。

修复全部问题后，预计分数提升至 **99-100**（满分）。

### 建议行动
1. **立即修复 P0-1**（PACKAGE_BODY s. 前缀）- 影响最大
2. **修复 P0-2, P0-3**（小改动，高收益）
3. **修复 P1 问题**（中等工作量）
4. **评估 P2 问题**（根据时间和优先级决定）

---

## 附录: 错误日志

### L3 失败统计（30 个独特错误 → 57 个对象失败）

| 错误 | 数量 | 影响对象 |
|------|------|---------|
| 模式 "s" 不存在 | 17 | PKG_SCORE_MANAGE.* |
| 语法错误 在输入的末尾 | 3 | F_GET_TOTAL_SCORE 等 |
| 类型 "nvarchar2[]" 不存在 | 1 | A_CONS_PRM |
| 对于表"t",丢失FROM子句项 | 1 | V_STUDENT_SCORE |
| 在 GROUP BY 中的非整数常量 | 1 | V_DEPENDENCY_GRAPH |
| 丢失数据类型声明 | 1 | TRG_STUDENT_BI |
| 语法错误 在 "v_student.CLASS_NO" | 1 | F_GET_STUDENT_LIST_PIPELINE |
| 语法错误 在 "(" | 1 | F_GET_SCORE_LIST_PIPELINE |
| 类型 "sys_refcursor" 不存在 | 1 | F_GET_STUDENT_CURSOR |
| "v_score" 不是一个已知变量 | 1 | F_GET_WEIGHTED_AVG |
| 语法错误 在 ";" | 1 | CLEAN_VALUE |
| 因为OUT参数，函数结果类型必须是numeric | 1 | VALIDATE_SCORE_VALUE |
