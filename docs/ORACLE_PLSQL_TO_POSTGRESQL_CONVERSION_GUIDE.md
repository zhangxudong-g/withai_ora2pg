# Oracle PL/SQL 与 PostgreSQL plpgsql 差异对照清单

## 1. 嵌套函数与子过程

### Oracle PL/SQL
```sql
CREATE OR REPLACE FUNCTION outer_func(p1 IN NUMBER)
RETURN NUMBER IS
  -- 嵌套函数定义
  FUNCTION inner_func(p2 IN NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN p2 * 2;
  END inner_func;
BEGIN
  RETURN inner_func(p1);
END outer_func;
/
```

### PostgreSQL plpgsql
```sql
-- 不支持嵌套函数，需要重构
-- 方案1: 提取为独立函数
CREATE OR REPLACE FUNCTION inner_func(p2 NUMERIC)
RETURNS NUMERIC AS $$
BEGIN
  RETURN p2 * 2;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION outer_func(p1 NUMERIC)
RETURNS NUMERIC AS $$
BEGIN
  RETURN inner_func(p1);
END;
$$ LANGUAGE plpgsql;

-- 方案2: 内联展开（简单函数）
-- 函数调用处直接用返回值替换
```

### 转换规则
| 情况 | 转换方式 |
|------|----------|
| 简单函数（单一路径） | 内联展开，用返回值替换调用 |
| 复杂函数（多路径/副作用） | 标记 `/* TODO: func_name() */`，需手动重构 |
| 子过程 | 同上 |

---

## 2. 数据类型对照

### 2.1 基本数据类型

| Oracle PL/SQL | PostgreSQL plpgsql | 说明 |
|---------------|-------------------|------|
| `NUMBER` | `NUMERIC` 或 `BIGINT` | 数值类型 |
| `NUMBER(p,s)` | `NUMERIC(p,s)` | 定点小数 |
| `FLOAT` | `DOUBLE PRECISION` | 浮点数 |
| `BINARY_INTEGER` | `INTEGER` | 整数 |
| `PLS_INTEGER` | `INTEGER` | 整数（Oracle 10g+） |
| `BINARY_FLOAT` | `REAL` | 单精度浮点 |
| `BINARY_DOUBLE` | `DOUBLE PRECISION` | 双精度浮点 |
| `DECIMAL` | `NUMERIC` | 十进制 |
| `INT`, `INTEGER` | `INTEGER` | 整数 |
| `SMALLINT` | `SMALLINT` | 小整数 |
| `BIGINT` | `BIGINT` | 大整数 |

### 2.2 字符串类型

| Oracle PL/SQL | PostgreSQL plpgsql | 说明 |
|---------------|-------------------|------|
| `VARCHAR2(n)` | `VARCHAR(n)` 或 `TEXT` | 可变长度字符串 |
| `VARCHAR(n)` | `VARCHAR(n)` 或 `TEXT` | Oracle 也支持 |
| `CHAR(n)` | `CHAR(n)` | 定长字符串 |
| `NCHAR(n)` | `CHAR(n)` | 国家字符集字符串 |
| `NVARCHAR2(n)` | `VARCHAR(n)` | 国家字符集可变长 |
| `LONG` | `TEXT` | 大文本（Oracle 旧类型） |
| `CLOB` | `TEXT` | 字符大对象 |
| `NCLOB` | `TEXT` | 国家字符集 CLOB |

### 2.3 日期时间类型

| Oracle PL/SQL | PostgreSQL plpgsql | 说明 |
|---------------|-------------------|------|
| `DATE` | `TIMESTAMP` 或 `DATE` | 日期时间 |
| `TIMESTAMP` | `TIMESTAMP` | 时间戳 |
| `TIMESTAMP(n)` | `TIMESTAMP(n)` | 精度时间戳 |
| `TIMESTAMP WITH TIME ZONE` | `TIMESTAMPTZ` | 带时区时间戳 |
| `TIMESTAMP WITH LOCAL TIME ZONE` | `TIMESTAMPTZ` | 本地时区时间戳 |
| `INTERVAL YEAR TO MONTH` | `INTERVAL` | 年月间隔 |
| `INTERVAL DAY TO SECOND` | `INTERVAL` | 日时分秒间隔 |

### 2.4 布尔类型

| Oracle PL/SQL | PostgreSQL plpgsql | 说明 |
|---------------|-------------------|------|
| `BOOLEAN` | `BOOLEAN` | 布尔类型 |

**注意**: Oracle `BOOLEAN` 只有在 PL/SQL 中可用，表列不能使用；PostgreSQL 两者都支持。

### 2.5 二进制类型

| Oracle PL/SQL | PostgreSQL plpgsql | 说明 |
|---------------|-------------------|------|
| `RAW(n)` | `BYTEA` | 定长二进制 |
| `LONG RAW` | `BYTEA` | 变长二进制（Oracle 旧类型） |
| `BLOB` | `BYTEA` 或 `BLOB` | 二进制大对象 |

### 2.6 其他类型

| Oracle PL/SQL | PostgreSQL plpgsql | 说明 |
|---------------|-------------------|------|
| `ROWID` | 不支持 | 行标识符 |
| `UROWID` | 不支持 | 通用行标识符 |
| `REF CURSOR` | `REFCURSOR` | 游标引用 |
| `%TYPE` | 通过表结构推断 | 列类型引用 |
| `%ROWTYPE` | `RECORD` + 表名%ROWTYPE | 行类型引用 |

---

## 3. 语法差异

### 3.1 变量声明

#### Oracle PL/SQL
```sql
DECLARE
  v_name VARCHAR2(100);
  v_count NUMBER := 0;
  v_date DATE DEFAULT SYSDATE;
BEGIN
  NULL;
END;
/
```

#### PostgreSQL plpgsql
```sql
DECLARE
  v_name VARCHAR(100);
  v_count NUMERIC := 0;
  v_date TIMESTAMP := CURRENT_TIMESTAMP;
BEGIN
  -- NULL;
END;
$$;
```

### 3.2 赋值语句

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `v_var := value;` | `v_var := value;` |

### 3.3 字符串连接

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `'str1' \|\| 'str2'` | `'str1' \|\| 'str2'` 或 `CONCAT()` |

### 3.4 NULL 判断

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `v_var IS NULL` | `v_var IS NULL` |
| `v_var IS NOT NULL` | `v_var IS NOT NULL` |

### 3.5 条件表达式

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `NVL(expr1, expr2)` | `COALESCE(expr1, expr2)` |
| `DECODE(expr, val1, res1, ..., default)` | `CASE WHEN expr = val1 THEN res1 ... ELSE default END` |
| `NVL2(expr, val1, val2)` | `CASE WHEN expr IS NOT NULL THEN val1 ELSE val2 END` |

### 3.6 类型转换

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `TO_CHAR(d, fmt)` | `TO_CHAR(d, fmt)` |
| `TO_NUMBER(str, fmt)` | `str::NUMERIC` 或 `CAST(str AS NUMERIC)` |
| `TO_DATE(str, fmt)` | `str::DATE` 或 `TO_DATE(str, fmt)` |
| `TO_TIMESTAMP(str, fmt)` | `str::TIMESTAMP` |

---

## 4. 函数与过程

### 4.1 函数定义

#### Oracle PL/SQL
```sql
CREATE OR REPLACE FUNCTION func_name(
  p_param1 IN NUMBER,
  p_param2 OUT VARCHAR2
) RETURN NUMBER IS
  v_result NUMBER;
BEGIN
  v_result := p_param1 * 2;
  p_param2 := 'result';
  RETURN v_result;
END func_name;
/
```

#### PostgreSQL plpgsql
```sql
CREATE OR REPLACE FUNCTION func_name(
  p_param1 NUMERIC,
  OUT p_param2 VARCHAR
) RETURNS NUMERIC AS $$
DECLARE
  v_result NUMERIC;
BEGIN
  v_result := p_param1 * 2;
  p_param2 := 'result';
  RETURN v_result;
END;
$$ LANGUAGE plpgsql;
```

### 4.2 参数模式

| Oracle | PostgreSQL | 说明 |
|--------|------------|------|
| `IN` (默认) | `IN` (默认) 或省略 | 输入参数 |
| `OUT` | `OUT` | 输出参数 |
| `IN OUT` | `INOUT` | 输入输出参数 |

### 4.3 过程调用

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `proc_name(args);` | `CALL proc_name(args);` |
| 在 PL/SQL 中直接调用 | 使用 `PERFORM` 执行但不返回结果 |

---

## 5. 控制结构

### 5.1 IF 语句

#### Oracle PL/SQL
```sql
IF condition THEN
  statements;
ELSIF condition2 THEN
  statements2;
ELSE
  statements3;
END IF;
```

#### PostgreSQL plpgsql
```sql
IF condition THEN
  statements;
ELSIF condition2 THEN
  statements2;
ELSE
  statements3;
END IF;
```

### 5.2 CASE 语句

#### Oracle PL/SQL
```sql
CASE expr
  WHEN value1 THEN result1;
  WHEN value2 THEN result2;
  ELSE default_result;
END CASE;
```

#### PostgreSQL plpgsql
```sql
CASE expr
  WHEN value1 THEN result1;
  WHEN value2 THEN result2;
  ELSE default_result;
END CASE;
```

### 5.3 循环

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `LOOP ... END LOOP;` | `LOOP ... END LOOP;` |
| `FOR i IN 1..10 LOOP ... END LOOP;` | `FOR i IN 1..10 LOOP ... END LOOP;` |
| `FOR rec IN cursor LOOP ... END LOOP;` | `FOR rec IN cursor LOOP ... END LOOP;` |
| `WHILE condition LOOP ... END LOOP;` | `WHILE condition LOOP ... END LOOP;` |
| `EXIT WHEN condition;` | `EXIT WHEN condition;` |
| `CONTINUE WHEN condition;` | `CONTINUE WHEN condition;` |

### 5.4 GOTO 语句

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `GOTO label;` | 不支持 |

---

## 6. 异常处理

### Oracle PL/SQL
```sql
BEGIN
  -- code
EXCEPTION
  WHEN DUP_VAL_ON_INDEX THEN
    -- handle
  WHEN OTHERS THEN
    -- handle
    RAISE;
END;
/
```

### PostgreSQL plpgsql
```sql
BEGIN
  -- code
EXCEPTION
  WHEN UNIQUE_VIOLATION THEN
    -- handle
  WHEN OTHERS THEN
    -- handle
    RAISE;
END;
$$;
```

### 常见异常对照

| Oracle 异常 | PostgreSQL 异常 |
|-------------|----------------|
| `DUP_VAL_ON_INDEX` | `UNIQUE_VIOLATION` |
| `NO_DATA_FOUND` | `NO_DATA_FOUND` |
| `TOO_MANY_ROWS` | `TOO_MANY_ROWS` |
| `ZERO_DIVIDE` | `DIVISION_BY_ZERO` |
| `INVALID_NUMBER` | `INVALID_TEXT_REPRESENTATION` |
| `OTHERS` | `OTHERS` |

### 抛出异常

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `RAISE_APPLICATION_ERROR(-20001, 'msg');` | `RAISE EXCEPTION '%', 'msg';` |
| `RAISE exception_name;` | `RAISE EXCEPTION USING ERRCODE = '22012';` |

---

## 7. 集合类型

### 7.1 数组类型

#### Oracle PL/SQL
```sql
DECLARE
  TYPE t_arr IS TABLE OF NUMBER INDEX BY BINARY_INTEGER;
  v_arr t_arr;
BEGIN
  v_arr(1) := 100;
  v_arr(2) := 200;
END;
/
```

#### PostgreSQL plpgsql
```sql
DECLARE
  v_arr NUMERIC[];
BEGIN
  v_arr := ARRAY[100, 200];
  v_arr[1] := 100;
END;
$$;
```

### 7.2 集合方法

| Oracle 方法 | PostgreSQL 等价 |
|-------------|----------------|
| `arr.COUNT` | `array_length(arr, 1)` |
| `arr.FIRST` | `array_lower(arr, 1)` |
| `arr.LAST` | `array_upper(arr, 1)` |
| `arr.DELETE(i)` | `arr[i] := NULL` |
| `arr.EXISTS(i)` | `arr[i] IS NOT NULL` |

---

## 8. 游标

### 8.1 显式游标

#### Oracle PL/SQL
```sql
DECLARE
  CURSOR c1 IS SELECT col1, col2 FROM t1;
  rec c1%ROWTYPE;
BEGIN
  OPEN c1;
  LOOP
    FETCH c1 INTO rec;
    EXIT WHEN c1%NOTFOUND;
  END LOOP;
  CLOSE c1;
END;
/
```

#### PostgreSQL plpgsql
```sql
DECLARE
  rec RECORD;
BEGIN
  FOR rec IN SELECT col1, col2 FROM t1 LOOP
    -- process rec
  END LOOP;
END;
$$;
```

### 8.2 REF CURSOR

| Oracle | PostgreSQL |
|--------|------------|
| `SYS_REFCURSOR` | `REFCURSOR` |
| `OPEN cursor FOR SELECT` | `OPEN cursor FOR SELECT` |

---

## 9. 包 (Package)

### Oracle PL/SQL
```sql
CREATE OR REPLACE PACKAGE pkg_name AS
  v_global_var NUMBER;
  FUNCTION func1 RETURN NUMBER;
END pkg_name;
/

CREATE OR REPLACE PACKAGE BODY pkg_name AS
  FUNCTION func1 RETURN NUMBER IS
  BEGIN
    RETURN v_global_var;
  END func1;
END pkg_name;
/
```

### PostgreSQL plpgsql
```sql
-- 包体转为 Schema + 函数集合
CREATE SCHEMA pkg_name;

CREATE OR REPLACE FUNCTION pkg_name.func1()
RETURNS NUMERIC AS $$
BEGIN
  RETURN NULL;  -- 需处理全局变量
END;
$$ LANGUAGE plpgsql;
```

### 转换规则
| Oracle Package 特性 | PostgreSQL 转换方式 |
|--------------------|-------------------|
| 全局变量 | 使用 Schema 变量或临时表 |
| 包级类型 | 使用 Schema 级类型定义 |
| 私有函数/过程 | 转换为独立函数 |

---

## 10. 触发器

### 10.1 行级触发器

#### Oracle PL/SQL
```sql
CREATE OR REPLACE TRIGGER trig1
AFTER INSERT OR UPDATE ON t1
FOR EACH ROW
BEGIN
  :NEW.col1 := UPPER(:NEW.col1);
END trig1;
/
```

#### PostgreSQL plpgsql
```sql
CREATE OR REPLACE FUNCTION trig1_func()
RETURNS TRIGGER AS $$
BEGIN
  NEW.col1 := UPPER(NEW.col1);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trig1
AFTER INSERT OR UPDATE ON t1
FOR EACH ROW
EXECUTE FUNCTION trig1_func();
```

### 10.2 触发器谓词

| Oracle | PostgreSQL |
|--------|------------|
| `IF INSERTING THEN` | `TG_OP = 'INSERT'` |
| `IF UPDATING THEN` | `TG_OP = 'UPDATE'` |
| `IF DELETING THEN` | `TG_OP = 'DELETE'` |

### 10.3 伪记录

| Oracle | PostgreSQL |
|--------|------------|
| `:NEW` | `NEW` |
| `:OLD` | `OLD` |

---

## 11. 事务控制

| Oracle PL/SQL | PostgreSQL plpgsql |
|---------------|-------------------|
| `COMMIT` | `COMMIT` |
| `ROLLBACK` | `ROLLBACK` |
| `SAVEPOINT name` | `SAVEPOINT name` |
| `PRAGMA AUTONOMOUS_TRANSACTION` | 不支持 |

---

## 12. SQL 执行

### 12.1 动态 SQL

#### Oracle PL/SQL
```sql
EXECUTE IMMEDIATE 'INSERT INTO t1 VALUES(:1)' USING v_value;
```

#### PostgreSQL plpgsql
```sql
EXECUTE 'INSERT INTO t1 VALUES($1)' USING v_value;
```

### 12.2 BULK COLLECT

#### Oracle PL/SQL
```sql
SELECT col1 BULK COLLECT INTO v_arr FROM t1;
```

#### PostgreSQL plpgsql
```sql
SELECT ARRAY_AGG(col1) INTO v_arr FROM t1;
```

---

## 13. 系统函数对照

### 13.1 日期函数

| Oracle | PostgreSQL |
|--------|------------|
| `SYSDATE` | `NOW()` |
| `SYSTIMESTAMP` | `NOW()` |
| `ADD_MONTHS(d, n)` | `d + (n \|\| ' months')::INTERVAL` |
| `TRUNC(d)` | `d::DATE` |

### 13.2 字符串函数

| Oracle | PostgreSQL |
|--------|------------|
| `LENGTH(str)` | `LENGTH(str)` |
| `SUBSTR(str, pos, len)` | `SUBSTRING(str FROM pos FOR len)` |
| `REPLACE(str, old, new)` | `REPLACE(str, old, new)` |
| `TRIM(str)` | `TRIM(str)` |
| `NVL(a,b)` | `COALESCE(a,b)` |

### 13.3 数值函数

| Oracle | PostgreSQL |
|--------|------------|
| `ABS(n)` | `ABS(n)` |
| `CEIL(n)` | `CEIL(n)` |
| `FLOOR(n)` | `FLOOR(n)` |
| `ROUND(n, d)` | `ROUND(n, d)` |
| `MOD(n1, n2)` | `MOD(n1, n2)` |
| `POWER(m, n)` | `POWER(m, n)` |

---

## 14. 其他差异

### 14.1 空字符串与 NULL

| Oracle | PostgreSQL |
|--------|------------|
| `''` 等同于 `NULL` | `''` 与 `NULL` 不同 |

### 14.2 ROWNUM vs LIMIT

| Oracle | PostgreSQL |
|--------|------------|
| `ROWNUM <= 10` | `LIMIT 10` |

### 14.3 CONNECT BY

| Oracle | PostgreSQL |
|--------|------------|
| `CONNECT BY PRIOR` | `WITH RECURSIVE` |

---

## 15. 快速参考表

| Oracle | → | PostgreSQL |
|--------|---|------------|
| `:=` | → | `:=` |
| `IS NULL` | → | `IS NULL` |
| `\|\|` | → | `\|\|` |
| `SYSDATE` | → | `NOW()` |
| `NVL(a,b)` | → | `COALESCE(a,b)` |
| `ROWNUM` | → | `ROW_NUMBER() OVER()` |
| `NUMBER` | → | `NUMERIC` |
| `VARCHAR2` | → | `VARCHAR` |
| `EXECUTE IMMEDIATE` | → | `EXECUTE` |
| `BULK COLLECT` | → | `ARRAY_AGG()` |

---

## 16. 更多代码示例

### 16.1 完整函数转换示例

#### Oracle PL/SQL
```sql
CREATE OR REPLACE FUNCTION get_employee_info(p_emp_id IN NUMBER)
RETURN VARCHAR2 IS
    v_emp_name VARCHAR2(100);
BEGIN
    SELECT employee_name INTO v_emp_name
    FROM employees WHERE employee_id = p_emp_id;
    RETURN v_emp_name;
EXCEPTION
    WHEN NO_DATA_FOUND THEN RETURN 'Not found';
END get_employee_info;
/
```

#### PostgreSQL plpgsql
```sql
CREATE OR REPLACE FUNCTION get_employee_info(p_emp_id NUMERIC)
RETURNS VARCHAR AS $$
DECLARE
    v_emp_name VARCHAR(100);
BEGIN
    SELECT employee_name INTO v_emp_name
    FROM employees WHERE employee_id = p_emp_id;
    RETURN v_emp_name;
EXCEPTION
    WHEN NO_DATA_FOUND THEN RETURN 'Not found';
END;
$$ LANGUAGE plpgsql;
```

### 16.2 游标处理

#### Oracle
```sql
CURSOR c1 IS SELECT * FROM t1;
FOR rec IN c1 LOOP
  -- process
END LOOP;
```

#### PostgreSQL
```sql
FOR rec IN SELECT * FROM t1 LOOP
  -- process
END LOOP;
```

### 16.3 递归查询

#### Oracle
```sql
SELECT * FROM t1 START WITH id = 1
CONNECT BY PRIOR id = parent_id;
```

#### PostgreSQL
```sql
WITH RECURSIVE cte AS (
  SELECT * FROM t1 WHERE id = 1
  UNION ALL
  SELECT t1.* FROM t1 JOIN cte ON t1.parent_id = cte.id
)
SELECT * FROM cte;
```

---

## 17. 特定场景转换

### 17.1 批处理迁移

| Oracle | PostgreSQL |
|--------|------------|
| `BULK COLLECT` | `ARRAY_AGG()` |
| `FORALL` | `WHERE ... IN (SELECT unnest(arr))` |

### 17.2 OUT 参数处理

#### Oracle
```sql
PROCEDURE get_val(OUT p_result OUT NUMBER) IS
BEGIN p_result := 100; END;
```

#### PostgreSQL
```sql
CREATE OR REPLACE FUNCTION get_val(OUT p_result NUMERIC)
AS $$
BEGIN p_result := 100; END;
$$ LANGUAGE plpgsql;
```

---

## 18. 已知限制

### 18.1 完全不支持的特性

| Oracle 特性 | 替代方案 |
|-------------|----------|
| `PRAGMA AUTONOMOUS_TRANSACTION` | dblink 或应用层 |
| `DBMS_LOCK` | `pg_advisory_lock()` |
| `DBMS_OUTPUT` | `RAISE NOTICE` |
| `UTL_FILE` | `pg_read_binary_file()` |
| `OBJECT` 类型 | 组合类型 |
| `NESTED TABLE` | 数组或 JSON |

### 18.2 常见陷阱

#### NULL 与空字符串
```sql
-- Oracle: '' = NULL
-- PostgreSQL: 需要 '' OR IS NULL
IF v_str = '' OR v_str IS NULL THEN
```

---

## 19. 数据类型边界值

### 19.1 数值类型

| Oracle | PostgreSQL | 范围 |
|--------|------------|------|
| `NUMBER(38)` | `NUMERIC(38)` | 任意精度 |
| `INTEGER` | `INTEGER` | ±2,147,483,647 |
| `BIGINT` | `BIGINT` | ±9×10^18 |

### 19.2 字符串类型

| Oracle | PostgreSQL | 大小 |
|--------|------------|------|
| `VARCHAR2(4000)` | `VARCHAR(4000)` | 4KB |
| `CLOB` | `TEXT` | 1GB |

### 19.3 日期范围

| Oracle | PostgreSQL |
|--------|------------|
| 4713 BC ~ 5874897 AD | 4713 BC ~ 294276 AD |

---

## 20. 版本差异与兼容性

### 20.1 PostgreSQL 版本特性支持

| 特性 | PG 9.4 | PG 10 | PG 11+ |
|------|-------|-------|---------|
| JSONB | ✓ | ✓ | ✓ |
| RECURSIVE | ✓ | ✓ | ✓ |
| PROCEDURE | - | - | ✓ |
| 并行查询 | - | ✓ | ✓ |
| JIT 编译 | - | - | ✓ |

### 20.2 Oracle 兼容扩展 (orafce)

```sql
CREATE EXTENSION IF NOT EXISTS orafce;

-- 兼容函数
SELECT ADD_MONTHS(CURRENT_DATE, 1);
SELECT NVL(NULL, 'default');
SELECT TRUNC(123.456);
```

---

## 21. 高级特性转换

### 21.1 自治事务

#### Oracle
```sql
PRAGMA AUTONOMOUS_TRANSACTION;
```

#### PostgreSQL 替代方案
```sql
-- 方案1: dblink
PERFORM dblink_exec('dbname=mydb', 'INSERT INTO log VALUES ($1)', msg);

-- 方案2: NOTIFY/LISTEN
NOTIFY logger_channel, msg;
```

### 21.2 动态 SQL

#### Oracle
```sql
DBMS_SQL.PARSE(cursor, sql);
```

#### PostgreSQL
```sql
EXECUTE format('UPDATE %I SET col = $1', table_name) USING value;
```

### 21.3 分区表

#### Oracle
```sql
PARTITION BY RANGE (dt) (
  PARTITION p1 VALUES LESS THAN (date '2024-01-01')
)
```

#### PostgreSQL
```sql
PARTITION BY RANGE (dt);
CREATE TABLE p1 PARTITION OF t FOR VALUES FROM ('2024-01-01') TO ('2024-04-01');
```

---

## 22. 测试与验证

### 22.1 转换质量评估

| 指标 | 目标 |
|------|------|
| 语法正确率 | > 95% |
| 语义相似度 | > 90% |
| 函数覆盖率 | > 90% |

### 22.2 测试框架

```sql
CREATE TABLE test_results (
    test_name VARCHAR,
    passed BOOLEAN,
    expected TEXT,
    actual TEXT
);

-- 性能测试
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM your_function(param);
```

### 22.3 监控查询

```sql
-- 检查慢函数
SELECT * FROM pg_stat_user_functions
WHERE total_time > 1000
ORDER BY total_time DESC;

-- 检查执行计划
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM function_name(params);
```

---

## 23. 迁移最佳实践

### 23.1 迁移阶段

```
评估 → 转换 → 验证 → 部署
```

### 23.2 优先级排序

| 优先级 | 类型 | 说明 |
|--------|------|------|
| P0 | 核心业务函数 | 密集测试 |
| P1 | 重要辅助函数 | 单元测试 |
| P2 | 报表函数 | 功能验证 |
| P3 | 工具函数 | 抽查 |

### 23.3 回滚机制

```sql
-- 备份原始函数
CREATE TABLE rollback_backup AS
SELECT proname, prosrc FROM pg_proc WHERE proname = 'func_name';

-- 回滚
CREATE OR REPLACE FUNCTION func_name() AS
  SELECT prosrc FROM rollback_backup WHERE proname = 'func_name'
$$ LANGUAGE SQL;
```

### 23.4 迁移检查清单

- [ ] 代码扫描与复杂度评估
- [ ] 自动转换所有简单函数
- [ ] 标记需手动转换的复杂函数
- [ ] 单元测试 + 集成测试
- [ ] 性能测试达标
- [ ] 小批量灰度发布
- [ ] 监控与告警

---

## 24. 工具与资源

### 24.1 迁移工具

| 工具 | 用途 |
|------|------|
| Ora2Pg | Oracle → PostgreSQL 迁移 |
| pgloader | 数据迁移 |
| AWS DMS | 云数据库迁移 |

### 24.2 学习资源

| 资源 | 链接 |
|------|------|
| PostgreSQL 文档 | https://www.postgresql.org/docs/ |
| Ora2Pg 文档 | https://ora2pg.darold.net/ |
| PostgreSQL 中文社区 | https://postgres.cn/ |

---

## 附录: 转换工具输出标记说明

### 嵌套函数标记
- `/* INLINED: func_name */` - 简单函数已内联
- `/* TODO: func_name() - needs manual */` - 复杂函数需手动实现

### 转换警告
- `WARNING: Inlined nested functions: ...` - 嵌套函数已被处理
- `WARNING: OUT params converted: n -> RETURNS TABLE` - OUT 参数转换说明

---

*文档版本: 1.1*
*最后更新: 2025*
