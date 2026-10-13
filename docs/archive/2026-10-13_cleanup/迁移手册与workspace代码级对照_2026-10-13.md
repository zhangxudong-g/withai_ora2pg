# 迁移手册 × workspace 代码级逐条对照

- **日期**：2026-10-13
- **对照基准**：`docs/oracle_pg_migration_guide.md`（29 节）× **workspace 实际代码**
- **代码基准**：HEAD `563e642`；`ora2pg_py_perlstyle/perlstyle_engine.py` 6622 行；`workspace/` 43 个 .py
- **性质**：只读对照，未修改任何代码
- **配套文档**：`docs/工作区实现评审与优化建议_2026-10-13.md`（验证/门禁层视角）。本文是**转换规则层**视角。

---

## 〇、本文的方法：加了"咬不咬人"这一列

只统计"引擎里有没有这个 token"会得出误导性结论——有些转换没实现但源里根本没用到。

因此本文对每一项同时给三个数：

| 列 | 含义 |
|---|---|
| **引擎命中** | 该模式在 `perlstyle_engine.py` 中出现次数（0 = 完全无处理，原样穿透） |
| **源命中** | 该 Oracle 特性在 `oracle_poc/01.SQL`（13 文件 / 128,246 字符）中出现次数 |
| **判定** | 三值：✅ 已实现 / ⚠️ 实现了但有语义偏差或静默降级 / ❌ 无处理 |

**❌ 且源命中 > 0 = 立即咬人的缺口**（这是最该看的一类）。

源命中为 0 的 ❌ 项单独归入第五节——**它们不阻塞 oracle_poc，但会阻塞 GKB**（手册 §24.1 基线：323 个 SQL 文件、150,333 行 PACKAGE_BODY）。

---

## 一、对照总表（按手册章节）

### §5 数据类型映射

| Oracle | 手册要求 | 引擎实现（`perlstyle_engine.py`） | 引擎 | 源 | 判定 |
|---|---|---|---|---|---|
| `VARCHAR2(n CHAR/BYTE)` | varchar/text | `:2460` → `VARCHAR(n)` | ✅ | — | ✅ |
| `NUMBER(p,0)` | **`smallint`/`integer`/`bigint`/`numeric`——"根据真实范围和应用类型选，**不要猜**"** | `:2464` → **一律 `BIGINT`** | ❌ | — | ❌ **违反"不要猜"** |
| `NUMBER(p)` | 同上，需先统计最小最大值 | `:2465` → **一律 `BIGINT`** | ❌ | — | ❌ |
| `NUMBER(p,s)` | `numeric(p,s)` | `:2466` → `NUMERIC(p,s)` | ✅ | — | ✅ |
| 无精度 `NUMBER` | "先评估 numeric，**不要无依据改成浮点**" | `:2468` → `NUMERIC` | ✅ | — | ✅ |
| `PLS_INTEGER`/`BINARY_INTEGER` | integer | `:2469-2470` → `INTEGER` | ✅ | — | ✅ |
| `CLOB`/`NCLOB` | text | `:2471-2472` → `TEXT` | ✅ | — | ✅ |
| `BLOB` | bytea | `:2473` → `BYTEA` | ✅ | — | ✅ |
| `RAW(n)` | bytea，**"长度和十六进制转换"需验证** | `:2474` → `BYTEA`（丢长度约束） | ⚠️ | 0 | ⚠️ |
| `LONG`/`LONG RAW` | 专项处理 | `:2475-2476` | ⚠️ | 0 | ⚠️ |
| Oracle `DATE` | **"含时分秒，不能一律映射成 date"** | `:2477` → `TIMESTAMP` | ✅ | — | ✅ **做对了** |
| `TIMESTAMP WITH TIME ZONE` | `timestamptz` | 无 | ❌ | 0 | ❌ |
| `TIMESTAMP WITH LOCAL TIME ZONE` | **"专项设计，会话时区语义需重建"** | 无 | ❌ | 0 | ❌ |
| `%ROWTYPE` | 目标行类型/复合类型 | `:2485` 剥标记保留表名 → PG 支持表行类型做变量 | ✅ | 6 | ✅ |
| `%TYPE` | 目标字段类型 | 引擎 `:4547` 直接丢弃 → 靠 P2-2 post-pass 补 | ⚠️ | 7 | ⚠️ |

### §6 表、约束、索引与序列

| 项 | 手册要求 | 实现 | 源 | 判定 |
|---|---|---|---|---|
| 序列语法 | 转换后需校准 | `convert_sequence` `:1775-1797`，`INCREMENT BY→INCREMENT`、`START WITH→START`、`MAX_VALUE→MAXVALUE` | — | ✅ |
| `NEXTVAL`/`CURRVAL` | **"搜索所有 NEXTVAL、CURRVAL、触发器赋值和应用取号逻辑"；"`currval()` 要求当前会话先调用 `nextval()`，**不能盲目替换**" | 引擎内 `NEXTVAL`/`CURRVAL` 命中 **0**；由 `rules/sequence_synonym.py` + P2-4 post-pass 覆盖 | **3** | ⚠️ 能力存在但不在引擎层，且未见"会话内先 nextval"检查 |
| Sequence Cache / MAXVALUE | 迁移手册 §6.1 要求核对并发与间隙 | `:1785-1788` 有 PG bigint 上限截断 + warning | — | ✅ **做得好** |
| 分区/虚拟列/函数约束/延迟约束 | 需逐项核对 | 无 | 0 | ❌ GKB 会咬 |

### §7 SQL 语法和语义差异（**本次对照的重点**）

| Oracle 特性 | 手册要求 | 实现位置 | 引擎 | 源 | 判定 |
|---|---|---|---|---|---|
| `NVL` → `COALESCE` | 注意类型推断 | `:779-780` | ✅ | — | ✅ |
| `NVL2` → CASE | 返回类型推断 | `:781-788` | ✅ | 0 | ✅ |
| **`DECODE` 含 NULL** | §7.1 明确警告：**"Oracle `DECODE(a,NULL,b,c)` 中 a 与 NULL 相等时返回 b，`CASE` 的 `=` 对 NULL 不成立"**；§26.2 给了正确写法 | `_decode_to_case` `:1036` **无条件生成 `WHEN {expr} = {search}`** | ❌ | 0 | ❌ **静默语义错误**（详见 §2.1） |
| **`SYSTIMESTAMP`** | §7.1：→ `clock_timestamp()` 或 `CURRENT_TIMESTAMP`，**"墙钟时间与事务时间不同"**；§7.2 重复强调 | `:892-893` 与 `:1115` → **`CURRENT_TIMESTAMP`** | ❌ | **17** | ❌ **实测证实语义改变**（详见 §2.2） |
| `SYSDATE` | 先确认要日期还是时间戳 | `:896-897` → `CURRENT_TIMESTAMP` | ✅ | 1 | ✅ 可辩护（Oracle SYSDATE 本就是事务时间） |
| `ROWNUM` | 排序前后语义不同 | `:3376` 注释"P8 ROWNUM→LIMIT" | ✅ | 0 | ✅ |
| `DUAL` | 通常省略 FROM | 有处理 | ✅ | 4 | ✅ |
| `MINUS` → `EXCEPT` | 集合语义 | **0** | ❌ | 0 | ❌ |
| `LISTAGG` → `string_agg` | 排序/NULL/溢出 | **0** | ❌ | 0 | ❌ |
| `ADD_MONTHS` | **"月末行为差异必须测试"** | 6 处提及 | ⚠️ | 0 | ⚠️ |
| `MONTHS_BETWEEN` | **"不可简单按天数换算"** | 4 处 | ⚠️ | 0 | ⚠️ |
| `REGEXP_LIKE` | 正则语法差异 | 2 处 | ⚠️ | 0 | ⚠️ |
| `LAST_DAY` | 日期边界 | 4 处 | ⚠️ | 0 | ⚠️ |
| **`CONNECT BY`** | → `WITH RECURSIVE`，层级/循环检测/伪列 | `_replace_connect_by` `:2864-3130`，实现完整（含 LEVEL、ORDER SIBLINGS BY） | ✅ | 0 | ✅ **实现质量高** |
| **`START WITH`（单独使用）** | §7.1 单列：**"递归 CTE 初始查询，保留根节点选择语义"** | `_replace_connect_by` **仅在 `CONNECT BY` 存在时触发**（`:2895`） | ❌ | **6** | ❌ **6 处 START WITH 无对应 CONNECT BY，属"按根节点过滤"语义，完全未处理** |
| `(+)` → ANSI JOIN | 外连接过滤条件放 WHERE 还是 ON | 引擎内建 + P2-3 post-pass | ✅ | **12** | ✅ **11/12 已改写，残留 1**（`PACKAGE_BODY:775`） |
| `PIVOT`/`UNPIVOT` | 动态列需求 | 无 | ❌ | 0 | ❌ |
| Oracle SQL Hint | PG 不保证接受 | 无 | ❌ | 0 | ❌ |
| `FOR UPDATE WAIT n` | PG 无等价 | 无 | ❌ | 0 | ❌ |

### §8 PL/SQL → PL/pgSQL 基础规则

| 项 | 手册要求 | 实现 | 判定 |
|---|---|---|---|
| `RETURN`→`RETURNS`、`IS`→`DECLARE`、`LANGUAGE plpgsql`、定界符 | §8.2 | 已实现 | ✅ |
| **`SELECT INTO` 严格性** | §8.4 核心：**"如果源程序依赖'无行抛 NO_DATA_FOUND、多行抛 TOO_MANY_ROWS'，目标代码需要显式保留……需要时使用 `INTO STRICT`"** | `INTO STRICT` 命中 **0** | ❌ 见 §2.3 |
| `AUTHID DEFINER` → `SECURITY DEFINER` | §8.1 表；§16 要求审查 `search_path` | `AUTHID`/`SECURITY DEFINER` 命中 **0** | ❌ 源中 0，但 GKB 会有 |
| volatility（IMMUTABLE/STABLE/VOLATILE） | §8.5：**"必须根据实际副作用和数据依赖声明，不能为了性能猜测"** | 命中 **0** —— 一律不声明（等价 VOLATILE） | ⚠️ **保守但安全**；可作为优化项 |
| `OUT`/`IN OUT` | 支持但不能机械替换 | 已支持 | ✅ |
| 变量/列名冲突 | §7.3：**"参数统一 `p_` 前缀，局部变量 `v_` 前缀"**；"不要用全局 `plpgsql.variable_conflict` 掩盖大量歧义" | **无命名规范、无冲突检测** | ❌ 见 §4.3 |

### §9 嵌套子程序 —— **本项目做得最好的一块**

| 项 | 手册要求 | 实现 | 判定 |
|---|---|---|---|
| 提为独立函数、外层变量显式传参 | §9.1 表格 | `_lift_nested_subprograms`（`perlstyle_engine.py:3466` 区域）+ P4-2d/e 修复 | ✅ |
| 读写集合不明确时标 `MANUAL_REVIEW` | §9.3 | 无此状态 | ❌ 见 §2.4 |
| 数据流清单（读/写/递归/游标/异常） | §9.3 要求逐项建立 | 无 | ❌ |

> 实施路径 §1.4 记录的"skipping lift 7→0、Lifted nested 0→18"是真实修复，与手册 §9 方向一致。

### §10 PACKAGE / PACKAGE BODY

| 项 | 手册要求 | 实现 | 源 | 判定 |
|---|---|---|---|---|
| 拆 Schema + 独立函数 | §10.2 | 已实现（`CREATE SCHEMA` + 独立函数） | — | ✅ |
| **包级变量/游标必须重构** | §10.3 **加粗**："独立函数调用间不会自动共享 Oracle package state" | 有 `pkg_global_state` 处理，但**无状态表/会话隔离设计**，无并发/连接池复用考量 | — | ⚠️ §10.4 的四种方案（显式传参/状态表/应用上下文/临时表）**均未落地** |
| 包初始化 `INITIALIZE` 块 | §10.3 | 无 | 0 | ❌ |
| 命名映射表 | §10.5"命名规则由统一映射表生成" | 无统一映射表 | — | ❌ |

### §11 游标、动态 SQL 与 DBMS_SQL

| 项 | 手册要求 | 实现 | 源 | 判定 |
|---|---|---|---|---|
| **`DBMS_SQL` 全套** | §11.2 处理矩阵；§3 列为 **C 类最高风险**；"**不是简单的 `EXECUTE IMMEDIATE` 文本替换**" | 引擎命中 **0** | 0 | ❌ GKB 阻塞项 |
| `EXECUTE IMMEDIATE` → `EXECUTE` | §11.1 推荐绑定参数 + `format('%I',...)` | 引擎命中 **0**；仅 `agent/prompt_builder.py:40` 的 LLM 提示表里有映射 | **7** | ❌ 见 §2.5 |
| **动态标识符安全** | §11.1：**"`%I` 用于标识符，`USING` 用于数据值，不能混用"**；"不允许把未信任输入直接拼进 SQL" | 无 | — | ❌ |
| **`SQL%ROWCOUNT`** | §11.5：`GET DIAGNOSTICS v_count := ROW_COUNT`，**"必须紧跟目标语句，后续 SQL 会覆盖"** | `GET DIAGNOSTICS`/`ROW_COUNT` 命中 **0** | **3** | ❌ 见 §2.6 |
| `%NOTFOUND`/`%ISOPEN`/`%FOUND` | 游标属性需映射 | 引擎 0 | — | ❌ 走残留扫描器报 major |
| `REF CURSOR` | §11.3 事务/会话生命周期 | `SYS_REFCURSOR`→`REFCURSOR` 有映射（`:4261`） | **19** | ⚠️ 类型转了，但生命周期/连接池未验证 |
| `DBMS_OUTPUT` | §11.4 → 日志/`RAISE NOTICE`，**"不能当业务结果"** | `:899-902` PUT_LINE 已转 | 7 | ✅ |

### §12 集合、批处理与 FORALL

| 项 | 手册要求 | 实现 | 源 | 判定 |
|---|---|---|---|---|
| **`FORALL`** | §12.3 **加粗警告**："**FORALL 不能一律转成循环**……用逐行 `LOOP` 替代可能产生重大性能差异"；§12.3 给出 `UPDATE ... FROM` 集合化改写 | `_convert_forall` `:3210` **无条件转 FOR LOOP，无 warning** | **10** | ❌ **直接违反手册明文** |
| `SAVE EXCEPTIONS` | §12.4 需逐行+错误收集机制 | **0** | 0 | ❌ |
| `BULK COLLECT` | §12.2 三方案，**"不能直接改成逐行 LOOP 而忽略性能"** | 仅 2 处 | **10** | ❌ 基本穿透 |
| **`INDEX BY`（关联数组）** | §12.1：**"选择方案时检查稀疏下标、`FIRST/LAST/NEXT/PRIOR/COUNT/DELETE`、元素结构"** | `:2488` **直接删除整个 `INDEX BY ...` 子句** | **7** | ❌ 见 §2.7 |
| `TABLE OF` → array | `:4103-4110` 仅当元素是 PG 内建类型才映射；复合类型留空 | 11 | ⚠️ |
| `RECORD` | §5"字段结构运行时确定，字段访问可能失败" | 15 处提及 | **14** | ⚠️ |

### §13 异常与事务 —— **缺口最集中的一章**

| 项 | 手册要求 | 引擎命中 | 源命中 | 判定 |
|---|---|---|---|---|
| **`WHEN OTHERS`** | §13.1 **加粗**："可使用，但**必须明确记录、恢复或重新抛出，不得静默吞掉**"；§21.2 禁止"把所有异常改为 `WHEN OTHERS THEN NULL`" | **0** | **112** | ❌❌ **最大缺口**（见 §2.3） |
| `SQLCODE`/`SQLERRM` | §13.1 → `SQLSTATE`/`GET STACKED DIAGNOSTICS` | **0 / 0** | **12 / 13** | ❌ 两者 PG 都没有，原样穿透必然报错 |
| `NO_DATA_FOUND`/`TOO_MANY_ROWS` | §13.1 | **0 / 0** | **1 / 0** | ❌ 已在产物中（`00_FUNCTION.sql:1007`） |
| `DUP_VAL_ON_INDEX`→`unique_violation` | §13.1 | **0** | 0 | ❌ |
| `SUBSCRIPT_OUT_OF_RANGE` | §13.1 | **0** | 0 | ❌ |
| `RAISE_APPLICATION_ERROR` | §13.1 → `RAISE EXCEPTION ... USING ERRCODE, MESSAGE` | `:2134-2136` | 1 | ⚠️ **ERRCODE 公式是自造的**（见 §3.4） |
| `COMMIT`/`ROLLBACK` | §13.2 函数内不能结束事务 | 无 | — | ❌ |
| 自治事务 | §13.3 **"无直接等价物，需重新设计"** | 无 | **2** | ✅ 计划已定"保留 TODO 桩"——**符合手册要求** |
| `DBMS_LOCK` | → advisory lock | 无 | 0 | ❌ |

### §14 日期、数字、NULL 与字符

| 项 | 手册要求 | 实现 | 源 | 判定 |
|---|---|---|---|---|
| **空串 vs NULL** | §7.2/§14 **"不能全局替换；必须按字段级制定策略，统计影响行数"** | **无任何检查或策略** | — | ❌ 见 §3.2 |
| **Oracle DATE 时分秒** | §14 必须检查样本 | `DATE`→`TIMESTAMP` ✅ 但**无数据样本校验** | — | ⚠️ |
| `ADD_MONTHS` 月末 | §14"月末、闰年、负月份单独测试" | 有转换无测试 | 0 | ⚠️ |
| NLS 参数 | §7.2 collation/排序 | 无 | 0 | ❌ GKB 阻塞 |
| 舍入规则（银行家舍入） | §14 | 无处理 | — | ❌ |

### §15 触发器、视图、类型 —— **实现质量较好**

| 项 | 手册要求 | 实现 | 判定 |
|---|---|---|---|
| 先 trigger function 再 trigger | §15.1 | `:2180-2192` | ✅ |
| `:NEW`/`:OLD` → `NEW`/`OLD` | §15.1 | ✅ | ✅ |
| `INSERTING/UPDATING/DELETING` → `TG_OP` | §15.1 | `:2127-2130` ✅（`UPDATING('col')` → `TG_ARGV[0]`，很细致） | ✅ |
| `RETURN NEW`/`RETURN OLD` | §15.1 | `:2160-2177` 按 ROW/STATEMENT 分别处理 | ✅ |
| `BEFORE`/`AFTER`/`INSTEAD OF` | §15.1 | `:2063` | ✅ |
| 行级 vs 语句级 | §15.1 | `:2077` | ✅ |
| **多行 DML 时行级触发器执行次数** | §15.1 **加粗**"可能性能差异" | 无检查无 warning | ❌ |
| **触发器执行顺序** | §15.1 "触发器之间的调用顺序和相互引用" | 无 | ❌ 源中 6 个触发器并存，顺序未验证 |
| **递归触发 `pg_trigger_depth()`** | §15.1 | 无 | ❌ |
| `WITH CHECK OPTION`/可更新视图 | §15.2 | 无 | ❌ 源 0 |
| COMPOUND TRIGGER | §4/§10 | `compound_trigger.py` 完整（BEFORE/AFTER × STATEMENT/STATE 共 35 处 COMPOUND、18 处 STATE） | ✅ |

### §16 权限、安全与外部能力

| 项 | 手册要求 | 实现 | 判定 |
|---|---|---|---|
| **`search_path` 必须显式限定** | §16 **加粗**："未限定的对象名解析可能因 schema 顺序变化导致指向错误对象，**必须在所有函数和触发器中显式限定**" | 引擎 `search_path` 命中 **1 次，且是注释**（`:5001` 讨论 SYNONYM 替代方案）。产物中仅驱动脚本 `00_CREATE_ALL_PG.sql:14` 设了 | ❌❌ 见 §3.1 |
| `SECURITY DEFINER` + `search_path` 审查 | §16 | 0 | ❌ |
| 最小权限 | §16 | 无权限转换 | ❌ |
| `UTL_FILE` | §15.4 受控应用文件服务 | 0 | ❌ 计划已定"出方案待业务决策"——**符合手册** |

### §19–§22 AI 流水线 / 分块 / 后处理 / 验证

| 项 | 手册要求 | 实现 | 判定 |
|---|---|---|---|
| **Feature Manifest**（§19.4） | 13 个特征 + `DETECTED/NOT_DETECTED/UNKNOWN` 三态；"**分析器没有识别出的特征，不能被当成不存在**" | ❌ **完全没有**。`integrity.py` 只做对象名计数 | ❌❌ 见 §4.1 |
| 可追溯性（§19.5） | 源哈希、规则版本、prompt 版本、模型参数、尝试次数 | ❌ record 无 `source_hash` | ❌ |
| **禁止简单正则切块**（§20.1） | "**不能用简单正则或 BEGIN/END 字符计数判断嵌套边界**" | `chunker_ast.py` ANTLR 切块 ✅ + 正则降级兜底 | ✅ 方向正确 |
| 七层验证（§22.1） | L1 文件 / L2 语法 / **L3 结构** / **L4 依赖** / L5 语义 / L6 业务 / L7 性能 | workspace 五层：L1 静态 / L2 语法 / L3 安装 / L3.5 冒烟 / L4 行为 | ⚠️ **缺"依赖完整性"层**——手册 §22.4 把它列为阻塞发布项 |
| 转换覆盖率（§22.3） | 特征级覆盖 | ❌ | ❌ |

### §25 规则库设计

| 项 | 手册要求 | 实现 | 判定 |
|---|---|---|---|
| 规则元数据（§25.1） | `rule_id`/severity/detection/conversion/validation/`manual_review_when`/责任人 | `workspace/rules/*.py` 是**纯函数，无任何元数据** | ❌ |
| 支持状态五档（§25.4） | `SUPPORTED_AUTO`/`SUPPORTED_WITH_REVIEW`/`REQUIRES_REFACTOR`/`UNSUPPORTED`/`NOT_APPLICABLE`；**"不能笼统宣称已支持"** | ❌ 无此枚举 | ❌❌ 本文就是这件事的证据 |
| 三类资产分离（§25.5） | `feature_detector` / `conversion_rules` / `validation_rules` | 部分：`parsers/`=detector ✅、`verification/`=validation ✅、**`conversion_rules` 散在引擎正则里** ❌ | ⚠️ |

---

## 二、立即咬人的缺口（❌ 且源命中 > 0）

> 按危害排序。这一节是本文最该被行动的部分。

### 2.1 `DECODE` 含 NULL —— 静默返回错误结果

**代码**：`perlstyle_engine.py:1036`

```python
out.append(f"WHEN {expr} = {search} THEN {result}")
```

**问题**：无条件用 `=`。Oracle `DECODE(x, NULL, 'unknown', y)` 中 `x` 为 NULL 时返回 `'unknown'`；PG 的 `x = NULL` 恒为 unknown → 走 `ELSE` → **结果静默错误，不报任何错**。

**实测验证**（live PG 15.19）：
```
CASE WHEN NULL = NULL  ->  nomatch     (Oracle DECODE 会给 match)
```

**手册依据**：§7.1、§26.2 明确点名此坑并给了正确写法 `CASE WHEN a IS NULL THEN ...`。

**当前风险**：源中 `DECODE` 命中 0，**oracle_poc 不咬**；但 GKB 必有。

### 2.2 `SYSTIMESTAMP` → `CURRENT_TIMESTAMP` —— 墙钟时间变事务时间（源中 17 处）

**代码**：`:892-893`、`:1115`

**实测验证**（live PG 15.19，同一事务内 sleep 2s）：
```
txn CURRENT_TIMESTAMP frozen: True   |  clock_timestamp advances: True
```

**问题**：Oracle `SYSTIMESTAMP` 是**墙钟时间**（含时区）；PG `CURRENT_TIMESTAMP` 是**事务开始时间**。长事务中记录日志/审计时间会得到过期时间戳，且时区信息丢失。

**手册依据**：§7.1「墙钟时间与事务时间不同」、§7.2「`CURRENT_TIMESTAMP` 是事务开始时间；`clock_timestamp()` 是实际墙钟时间」。

**源中 17 处命中 → 这是当前最可能造成生产数据错误的语义偏差之一。**

### 2.3 `WHEN OTHERS` × 112 + `SQLCODE` × 12 + `SQLERRM` × 13 —— 异常体系整体缺失

**代码**：引擎中 `WHEN OTHERS`/`SQLCODE`/`SQLERRM`/`GET DIAGNOSTICS` 命中**全部为 0**。

**问题**：
- PG **没有** `SQLCODE`/`SQLERRM` 这两个变量，也没有 `%ROWTYPE` 式的异常码访问方式。原样穿透 → 这些异常块在**首次执行时**报 `column "sqlcode" does not exist`；
- 这正好是 Agent memory 里记录的 plpgsql 延迟报错模式——L1/L2 全绿、L3 安装成功，**首调才炸**；
- 手册 §13.1 给了整张映射表；§13.1 加粗"**必须明确记录、恢复或重新抛出，不得静默吞掉**"。

**注意**：`residuals.py` **已经报出** SQLCODE/SQLERRM（实测 major 里有 21 条），所以这一项**不是没看见**，是"看见了但没修"——它属于 P2 阶段未覆盖的规则类。

### 2.4 `SELECT INTO` 缺 `STRICT` —— 无行/多行行为反转

**代码**：`INTO STRICT` 命中 0。

**手册依据**：§8.4——Oracle 无条件抛 `NO_DATA_FOUND`/`TOO_MANY_ROWS`，PG 默认不抛，需显式 `INTO STRICT`。

**产物中已出现**：`FUNCTION/00_FUNCTION.sql:1007` `WHEN NO_DATA_FOUND THEN`——这行代码在 PG 里**永远不会进入**（除非用了 STRICT），是典型的"看起来转了、语义反了"。

### 2.5 `EXECUTE IMMEDIATE` × 7 —— 引擎零处理，且部分藏在字符串里

**代码**：引擎命中 0；全仓库仅 `workspace/agent/prompt_builder.py:40` 的 LLM 提示表含映射。

**关键发现**：这 7 处中有多处是**拼接的动态 SQL 字符串字面量**，例如（源文件实测）：
```
'WHERE S.STUDENT_NO = SC.STUDENT_NO(+) ' || 'AND S.GRADE = ' || i_nGRD
```
字符串里的 Oracle 语法（含未改写的 `(+)`）原样进入 PG。

**危险等级**：这类字符串在 `CREATE FUNCTION` 时**语法合法**（只是个字符串），安装会成功；**只有该函数被调用时才炸**。这正是 L3.5「首调冒烟」要抓的那一类，而当前 L3.5 因 Gate 豁免（见配套文档 A3）也抓不住。

### 2.6 `SQL%ROWCOUNT` × 3 —— 穿透且必须"紧跟"改写

**代码**：`GET DIAGNOSTICS`/`ROW_COUNT` 命中 0。

**手册依据**：§11.5 —— `SQL%ROWCOUNT` → `GET DIAGNOSTICS v_count := ROW_COUNT`，且**"必须紧跟目标语句，后续 SQL 会覆盖"**。

这条比"改个函数名"难：需要在目标语句后**插入**一条语句，且插入位置错误会静默取到错误行数。

### 2.7 `INDEX BY` × 7 —— 关联数组被静默降级为稠密数组

**代码**：`:2488`

```python
(re.compile(r'\s+INDEX\s+BY\s+(?:BINARY_INTEGER|PLS_INTEGER|VARCHAR2\s*\(\s*\d+\s*\)|...)'), '')
```

**问题**：直接**删除整个 `INDEX BY` 子句**。Oracle 关联数组按键稀疏索引，删掉后变成 0-based 稠密数组，**按业务键查找（`arr(key)`）全部失效**。

**手册依据**：§12.1 要求检查"稀疏下标、FIRST/LAST/NEXT/PRIOR/COUNT/DELETE、元素结构"；§9.3 要求"读写集合不明确时标记 `MANUAL_REVIEW`，**禁止模型自行假定作用域**"。

**这是"删除即语义改变"的典型，与手册 §21.2 禁止的"通过增加/删除 END 使文本表面平衡"是同一类错误。**

### 2.8 `START WITH` × 6（无 CONNECT BY）—— 根节点过滤语义丢失

**代码**：`_replace_connect_by:2895` 仅在匹配到 `CONNECT BY` 时才进入转换。

**问题**：Oracle 中 `START WITH` 可单独用于"只取根节点行"，与 `CONNECT BY` 无关。引擎把它绑死在 `CONNECT BY` 分支里 → 这 6 处**完全未处理**。

**手册依据**：§7.1 把 `CONNECT BY` 和 `START WITH` **分列两行**，`START WITH` 的说明是"递归 CTE 初始查询，**保留根节点选择语义**"。

---

## 三、已实现但存在语义偏差（比"没做"更危险，因为看起来做了）

### 3.1 `search_path` —— 全项目只在注释里出现过

- 引擎 `search_path` 命中 **1 次，是注释**（`:5001`，讨论 SYNONYM 替代方案时提到）
- 产物中 `SET search_path` **0 处**，只有驱动脚本 `00_CREATE_ALL_PG.sql:14`
- 手册 §16 **加粗**："必须在所有函数和触发器中显式限定"

**后果**：所有对象装进自定义 schema 后，函数体内的对象解析依赖**调用方 session 的 `search_path`**。同一套产物，换连接串/换用户就可能解析到错误对象——手册 §28.1 把这条列为风险「`search_path` 变化导致指向错误对象」。

### 3.2 空串 vs NULL —— 手册反复强调"不能全局替换"，但项目里既无策略也无检查

手册在 §7.2、§14、§17.4 三处强调需要"字段级策略 + 统计影响行数"。当前：无策略、无检查、无数据层对账（配套文档 D8）。

### 3.3 `NUMBER(p)` → `BIGINT` —— 手册明文说"不要猜"

`NUMBER(3)` 也变 BIGINT：语义安全但存储浪费，且**掩盖了"该用 smallint"的优化信号**。真正的问题是手册 §5 要求的"先统计最小最大值"这一步完全没有。

### 3.4 `RAISE_APPLICATION_ERROR` 的 ERRCODE 是自造公式

`:2134-2136`：`ERRCODE = abs({code}%50000) + 50000`

Oracle 的 `RAISE_APPLICATION_ERROR(-20001, ...)` 用 -20000..-20999 区间。该公式生成的 SQLSTATE 与 Oracle 错误码**无对应关系**。手册 §18 要求"Oracle 专属错误码需要建立 **PostgreSQL SQLSTATE 到业务异常的映射**"——**没有映射表**，应用层按 ORA-20001 捕获的逻辑会失效。

### 3.5 类型映射表在引擎里重复了 4 遍

| 位置 | 形式 |
|---|---|
| `_TYPE_MAP` `:2459-2489` | 有序 `(正则, 替换)` 列表 |
| `_convert_return_type` `:4255-4262` | dict |
| 参数类型正则串 `:4298-4303` | 5 个独立 `re.sub` |
| `_ORACLE_TO_PG_TYPES` `:5487+` | dict |
| （外）`rules/percent_type.build_type_map_from_table_sql` | 从产物建表 |

同一个知识散在 5 处。手册 §25.5 要求把 `conversion_rules` 作为**一类可维护资产**统一管理。

### 3.6 `FORALL` → `FOR LOOP` 是手册点名禁止的做法

手册 §12.3 **加粗**："**FORALL 不能一律转成循环**……用逐行 `LOOP` 替代可能产生重大性能差异"，并给出 `UPDATE ... FROM` 集合化方案。

引擎 `_convert_forall:3210` 无条件转换、**不加 warning**、无 `SAVE EXCEPTIONS` 处理。源中 10 处命中。

---

## 四、结构性问题（与手册方法论相关）

### 4.1 缺 Feature Manifest —— 手册把它放在架构核心

手册 §19.4 给出了完整 JSON schema（13 个特征 + 三态 + provenance），并强调：

> **"分析器没有识别出的特征，不能被当成不存在。"**

当前 workspace 只有 `integrity.py` 的**对象名计数**（DoD-7），**没有特征级建模**。后果：

- 无法回答"源里 112 个 `WHEN OTHERS` 处理了几个"——而这正是 §2.3 的核心问题；
- §22.3 的转换覆盖率指标无从计算；
- 手册 §22.4 的"C 类对象逐个验收"没有筛选依据。

**建议**：把本文第二节那张表直接做成 `feature_detector` 的规则表——每个特征输出 `DETECTED/NOT_DETECTED/UNKNOWN` + 源位置 + 产物位置。这是手册 §25.5 的第一类资产，落点 `workspace/parsers/feature_detector.py`。

### 4.2 引擎里有一处不可达代码

`_convert_forall` `:3264` 有一行 `return pattern.sub(to_for_loop, sql)`，其后 `:3265-3286` 又重新定义了一遍 pattern 并继续写转换逻辑——**全部不可达**。

### 4.3 无变量命名规范与冲突检测

手册 §7.3 给了明确方案：`p_`/`v_`/`r_` 前缀 + 表别名消歧，并警告"不要用全局 `plpgsql.variable_conflict` 掩盖大量歧义"。

当前引擎无命名规范化。考虑到"一次搬回隔离**命名空间**"的做法本身就依赖全库无重名——这个前置条件没有机器校验。

### 4.4 规则支持状态未声明

手册 §25.4：**"不能笼统宣称'已支持'，每类规则必须声明支持的具体子集和例外。"**

本文第三节之前的对照表，实际上就是当前项目**缺失的那份规则支持状态声明**。建议把这张表固化为 `workspace/rules/catalog.yaml`（手册 §25.2 已给出 YAML 结构范例，`§24.5` 建议的 `rules/catalog.yaml` 路径也正是这个）。

---

## 五、本语料暂不咬、但 GKB 会咬的 ❌ 项

手册 §24.1 的 GKB 基线：**323 个 SQL 文件、PACKAGE_BODY 约 150,333 行、最大约 8,741 行**，且"已识别 PACKAGE_BODY 特征：DBMS_SQL、EXECUTE IMMEDIATE、DBMS_OUTPUT、REF CURSOR、UTL_FILE、FORALL"。

| 缺失能力 | 手册章节 | GKB 风险 |
|---|---|---|
| `DBMS_SQL` 全套 | §11.2 | **最高**——动态列/游标生命周期无法自动等价 |
| 动态 SQL 绑定参数 + `format('%I')` | §11.1 | 高——SQL 注入面 |
| `SAVE EXCEPTIONS` / `SQL%BULK_*` | §12.4 | 高 |
| 分区表 / 虚拟列 / 延迟约束 | §6.1 | 中高 |
| `NLS_*` collation | §7.2、§14 | 中高（影响索引与比较） |
| `AUTHID` / `SECURITY DEFINER` | §8.1、§16 | 中 |
| `PIVOT`/`UNPIVOT` | §7.1 | 中 |
| `TIMESTAMP WITH [LOCAL] TIME ZONE` | §5 | 中 |
| `MERGE`（引擎有 6 处，源中 1 处） | §7 | 低，但需验证 |

---

## 六、优化建议（按投入产出排序）

### 第一梯队：把手册里"明文写了、且源里就在用"的补上

| # | 动作 | 手册依据 | 源命中 | 预计 |
|---|---|---|---|---|
| **G1** | 异常体系：`SQLCODE`→`SQLSTATE`、`SQLERRM`→`GET STACKED DIAGNOSTICS MESSAGE_TEXT`；`WHEN OTHERS` 保留 + 强制记录 + 重新抛出 | §13.1 | 112+25 | 1.5 天 |
| **G2** | `SYSTIMESTAMP` → `clock_timestamp()`（可配置开关）；`SYSDATE` 保持 `CURRENT_TIMESTAMP` 并在注释说明理由 | §7.1、§7.2 | **17** | 0.5 天 |
| **G3** | `SELECT INTO` 按源语义加/不加 `STRICT`；至少对能判定的加，其余出 warning | §8.4 | — | 1 天 |
| **G4** | `SQL%ROWCOUNT` → 在目标语句后插入 `GET DIAGNOSTICS` | §11.5 | 3 | 1 天 |
| **G5** | `EXECUTE IMMEDIATE` → `EXECUTE`，字符串内的 Oracle 语法同步处理或标 `MANUAL_REVIEW` | §11.1 | 7 | 1 天 |
| **G6** | `INDEX BY` 关联数组：不再静默删除，转 `JSONB` 或显式报 `MANUAL_REVIEW` | §12.1 | **7** | 1 天 |

> G1 是最大头：源里 112 个异常块，产物里 SQLCODE/SQLERRM 21 处全部穿透。

### 第二梯队：消除"静默语义偏差"（这类最危险，因为看起来已经做了）

| # | 动作 | 依据 |
|---|---|---|
| **G7** | `DECODE` 生成前判断 `search` 是否为 `NULL` 字面量 → 改用 `IS NULL` 分支；`NVL2` 已有正确实现可参照 | §7.1、§26.2 |
| **G8** | 所有函数/触发器统一注入 `SET search_path`（可按 schema 参数化） | §16 |
| **G9** | `INDEX BY` 之外，`_decode_to_case` / `_replace_connect_by` / `_convert_forall` 的**所有提前返回路径**改为产出 warning——当前"不支持就原样返回"是静默的 | §19.4 "分析器没识别 ≠ 不存在" |
| **G10** | `RAISE_APPLICATION_ERROR` 错误码映射表（Oracle 码 → PG SQLSTATE），并落成可查表 | §13.1、§18 |

### 第三梯队：补横向能力（手册方法论）

| # | 动作 | 依据 |
|---|---|---|
| **G11** | **建 `feature_detector`**：本文第二、五节的表直接落地为特征规则，输出三态 + 源/产物位置 | §19.4、§25.5 |
| **G12** | 建 `workspace/rules/catalog.yaml`：每条规则的 `rule_id`/severity/detection/conversion/validation/`manual_review_when`/支持状态五档 | §25.1、§25.2、§25.4 |
| **G13** | 合并 5 处类型映射为单一真源 | §25.5 |
| **G14** | 转换记录加 `source_hash` + 规则版本 | §2、§19.5 |
| **G15** | 补"依赖完整性"验证层（悬空调用/表/视图引用） | §22.1 L4、§22.4 |

---

## 七、需要你决策的问题

1. **异常体系（G1）是否并入当前阶段？** 它涉及 112 处异常块，是本次对照中源命中最多的一项。但它属于"规则层系统缺陷"，按实施路径应落在 P2 阶段——**而 P2 阶段的 P2-1~P2-4 清单里没有异常映射这一项**。是否需要新增 P2-5？
2. **GKB 是否现在就要求 DBMS_SQL？** 手册 §24.1 把它列为已识别特征，但它是 §3 的 C 类最高风险项。若 GKB 全量迁移在计划内，`workspace/rules/dbms_sql.py` 需要尽早立项（手册建议按 §11.2 的生命周期矩阵分设计，工作量不小）。
3. **本文的对照表是否固化为 `rules/catalog.yaml`？** 它既是手册 §25.4 要求的"支持状态声明"，也能防止后续再次出现"看起来做了"（§3）的偏差。

---

## 附录：本文的实测证据

```powershell
# 引擎 token 命中（对 perlstyle_engine.py 全文 270,769 字符）
#   EXECUTE IMMEDIATE / SQLCODE / SQLERRM / WHEN OTHERS / DBMS_SQL / UTL_FILE
#   GET DIAGNOSTICS / INTO STRICT / AUTHID / SECURITY DEFINER / SAVE EXCEPTIONS
#   MINUS / LISTAGG / CLOCK_TIMESTAMP  —— 均为 0
#   search_path —— 1 次，位于 :5001 注释中

# 源语料特征命中（oracle_poc/01.SQL，13 文件 / 128,246 字符）
#   WHEN OTHERS 112 | SYS_REFCURSOR 19 | SYSTIMESTAMP 17 | RECORD 14
#   SQLERRM 13 | SQLCODE 12 | TABLE OF 11 | BULK COLLECT 10 | FORALL 10
#   UTL_FILE 8 | INDEX BY 7 | EXECUTE IMMEDIATE 7 | START WITH 6
#   %TYPE 7 | %ROWTYPE 6 | FROM DUAL 4 | NEXTVAL 3 | SQL%ROWCOUNT 3
#   (+) 12  →  产物残留 1

# live PG 15.19 语义验证
#   EXECUTE PROCEDURE  : ACCEPTED（PG 11+ 仍向后兼容，非破坏性问题，仅为弃用写法）
#   EXECUTE FUNCTION   : ACCEPTED
#   CASE WHEN NULL = NULL  ->  nomatch    （证实 DECODE-NULL 转换会静默出错）
#   txn CURRENT_TIMESTAMP frozen: True | clock_timestamp advances: True
#                          （证实 SYSTIMESTAMP→CURRENT_TIMESTAMP 改变语义）
```

> **关于 `EXECUTE PROCEDURE`**：引擎 `:2191` 生成的是 PG 10 及更早的写法。实测 PG 15.19 **仍然接受**（向后兼容保留），因此**不是安装阻塞项**，仅为弃用写法、建议统一为 `EXECUTE FUNCTION`。此处特意实测而非断言，以免误报。

---

*本文为只读代码对照，未修改任何代码或产物。所有数字来自对源码与产物的实际检索，以及对 live PG 15.19 的实际验证。*