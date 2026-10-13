# workspace 转换产物逐文件可用性分析

> 分析对象：`D:/JIP/poc/workspace/output/` 下 13 个 SQL 文件（oracle_poc Oracle → PostgreSQL 转换产物）
> 分析基准：4-Level 验证框架最新结果（`results/latest/`，总分 87.0 B 良，L3 安装 141/164）
> 环境：真实 PostgreSQL 8.4（localhost:5432）
> 说明：**本文件仅做分析与评级，不包含任何代码修复。** 所有"运行时"结论均经真实 PG 安装 + 调用验证。

## 可用性判定标准

| 等级 | 含义 |
|---|---|
| ✅ 可直接使用 | 安装成功且实测行为正确 |
| 🟡 有限可用 | 可安装，但存在行为偏差、静默回退或功能缺失，使用前须知晓风险 |
| ❌ 不可直接使用 | 安装失败或运行时必然报错/返回错误数据 |

---

## 总览

| # | 文件 | 对象 | 安装 (L3) | 运行时实测 | 判定 |
|---|------|------|-----------|-----------|------|
| 1 | 001_STUDENT_MST.sql | 1 TABLE + 2 INDEX | 3/3 | INSERT/查询正常 | ✅ |
| 2 | 002_SUBJECT_MST.sql | 7 TABLE + 1 TEMP | 8/8 | 查询正常 | ✅ |
| 3 | 00_SEQUENCE.sql | 6 SEQUENCE | 6/6 | native nextval 正常；**Oracle 风格 `.NEXTVAL` 调用失败** | ✅（带使用注意） |
| 4 | 001_TYPES.sql | 8 TYPE + 8 FUNCTION | 16/16 | 复合类型 OK；**6 个 SELF 方法函数运行时必炸** | 🟡 |
| 5 | 00_VIEW.sql | 7 VIEW | 7/7 | 全链路查询实测通过 | ✅ |
| 6 | 00_TRIGGER.sql | **1/6** TRIGGER | 2/2 | TRG_STUDENT_BI 实测工作正常；**5 个触发器静默丢失** | 🟡（功能严重缺失） |
| 7 | 00_FUNCTION.sql | 16 FUNCTION | 9/16 | 已安装的多个**静默返回回退常量** | ❌ |
| 8 | 00_PROCEDURE.sql | 5 PROCEDURE | 1/5 | 已安装的 1 个含 COMMIT/ROLLBACK，**运行时必失败** | ❌ |
| 9 | 00_PACKAGE.sql | 13 stub | 13/13 | 桩可装；实现依赖 BODY | 🟡（依赖 BODY） |
| 10 | 00_PACKAGE_BODY.sql | 28 实现 | 16/28 | 装上的多个**静默返回错误数据**；跨函数调用需特定 search_path | 🟡（质量最差的大文件） |
| 11 | 00_SYNONYM.sql | 11 VIEW | 11/11 | 查询/INSERT 实测通过 | ✅（语义降级） |
| 12 | 00_TEST_DATA.sql | 49 INSERT | 49/49 | 数据完整 | ✅ |
| 13 | CREATE_ALL.sql | 驱动脚本 | 跳过 | SQL*Plus 元命令未转换，psql 无法执行 | ❌ |

**结论一句话**：库结构层（表/序列/类型/视图/数据/同义词）基本可直接使用；**PL/SQL 行为层（函数/过程/包/触发器）整体不可信**——不仅 23 个对象安装失败，更危险的是已安装的多个对象会**静默返回错误数据或回退常量而不报错**。

---

## 逐文件详细分析

### 1. 001_STUDENT_MST.sql — ✅ 可直接使用

- 安装：3/3（表 + 2 索引），L1/L2 均 100 分。
- 验证：真实 PG 插入、查询、触发器联动全部正常。
- 细节问题（不影响使用）：
  - Oracle `NUMBER(10,0)` → `BIGINT`（有损精度语义变化：BIGINT 8.4 实际是 int8，行为等价，可接受）；
  - `BIRTH_DATE DATE` → `BIGINT`（日期以 YYYYMMDD 数字存储，是原 Oracle 代码风格，PG 侧一致）；
  - 表定义中残留孤立注释行 `--`（第 20 行），美观问题。
- **判定：可直接使用。**

### 2. 002_SUBJECT_MST.sql — ✅ 可直接使用（TEMP 表有注意事项）

- 安装：8/8，含 7 张表 + 1 张临时表，主键/默认值完整。
- `TEMP_SCORE_BATCH` 使用 `CREATE TEMPORARY TABLE ... ON COMMIT PRESERVE ROWS`：
  - ⚠️ 注意：这是**会话级**临时表，每个数据库会话首次访问时才创建；批处理代码若跨会话共享则不符合 Oracle `GLOBAL TEMPORARY` 语义。当前 POC 场景（单会话批处理）可用。
- **判定：可直接使用（共享多会话场景需人工确认）。**

### 3. 00_SEQUENCE.sql — ✅ 可直接使用（调用方式注意）

- 安装 6/6，参数（START/INCREMENT/MAX/MIN/CYCLE/CACHE）转换完整，`SEQ_LOG_ID MAXVALUE 9223372036854775807` 等边界正确。
- ⚠️ **使用注意**：应用/代码必须使用 PG 原生 `nextval('seq')`。**Oracle 风格 `SEQ.X.NEXTVAL` / `FROM DUAL` 在 PG 无法工作**（实测报 `relation "dual" does not exist`）。转换产物中 `F_GET_JOBNUM` 就是因未转换此写法而运行时失败（见文件 7）。
- **判定：序列定义本身可直接使用。**

### 4. 001_TYPES.sql — 🟡 有限可用

- 安装：16/16（8 复合类型 + 8 函数）。
- ✅ 复合类型（`STUDENT_RECORD`、`SCORE_RECORD`、`STUDENT_LIST (items ...[])` 等）定义合法，字段名保留，可直接作为构造类型使用。
- ❌ **TYPE BODY 的 MEMBER 方法转成的 6 个独立函数全部不可用**：
  - `GET_AGE_of_STUDENT_RECORD`、`GET_GENDER_NAME_of_STUDENT_RECORD`、`GET_RANK_STR_of_STUDENT_RECORD`、`GET_STATUS_of_SCORE_RECORD`、`GET_LETTER_GRADE_of_SCORE_RECORD`：函数体内引用 `SELF_SCORE_RECORD_IS_PASS` 等**从未声明的变量**（转换把 `SELF.col` 改写成 `SELF_<type>_<col>`，但没给函数添加复合类型参数，也没声明变量）。
  - 实测：`SELECT GET_STATUS_of_SCORE_RECORD()` → `column "self_score_record_is_pass" does not exist`。
  - 危险点：plpgsql 在 CREATE 时不校验列引用，所以**安装全部成功**，只有调用才失败 → 调用方若没有异常处理会拿到报错。
- ⚠️ 3 个 REF CURSOR 函数（`OPEN_*_of_REF_CURSOR_T`）语法可安装，但 `RETURN vCURSOR` 依赖客户端游标支持；原 Oracle 方法的签名参数被丢弃（`of_` 命名后无入参），与原接口不兼容。
- **判定：类型可用；6 个方法函数不可直接使用，需要重写为接受复合类型参数的普通函数。**

### 5. 00_VIEW.sql — ✅ 可直接使用

- 安装 7/7，L2 100 分。
- 运行时全链路实测通过：`V_STUDENT_SCORE`（1728 行，含 LEFT JOIN 语义转换）、`V_STUDENT_STATS`(10)、`V_SCORE_RANK`(10，窗口函数 RANK/DENSE_RANK/NTILE/LAG/LEAD 全部可用)、`V_SUBJECT_STATS`、`V_TEACHER_STATS`、`V_DEPENDENCY_GRAPH`(3)、`V_STUDENT_WITH_SCORE`。
- 说明：Oracle `(+)` 外连接已转为 `LEFT JOIN ... ON`，条件归属此前已修复并验证。
- **判定：可直接使用。**

### 6. 00_TRIGGER.sql — 🟡 有限可用（严重功能缺失）

- ⚠️ **重大发现：源文件 `00_TRIGGER.sql` 定义 6 个触发器，产物只含 1 个（TRG_STUDENT_BI），其余 5 个被静默丢弃**：
  | 丢失的触发器 | 丢失的行为 |
  |---|---|
  | TRG_STUDENT_AU | 学生状态变更自动写 PROCESS_LOG 日志（含 PRAGMA AUTONOMOUS_TRANSACTION + COMMIT） |
  | TRG_SCORE_BIU | INSERT/UPDATE 成绩时自动计算 IS_PASS / GRADE_LEVEL |
  | TRG_SYSTEM_CONFIG_BD | 禁止删除 type=1 系统配置（`RAISE_APPLICATION_ERROR`） |
  | TRG_SCORE_COMPOUND | 复合触发器统计（PG 无对应概念，需人工重构） |
  | TRG_V_STUDENT_IO | 视图 INSTEAD OF 触发器（视图可更新性） |
  后果：`SCORE_REC` 插入后不会自动计算等级、`SYSTEM_CONFIG` 可以随意删除关键配置——**业务行为与 Oracle 源不一致且无任何报错提示**。
- ✅ 保留的 TRG_STUDENT_BI 实测**完全正常**：INSERT 后自动填充 `STATUS=0`、`DELETE_FLG=0`、`REC_CREATE_DATE`（实测 row: `(10000, 0, 0, '2026-10-09 09:49:11.748')`），空编号自动生成逻辑保留。函数体内 Oracle 内建（NVL→COALESCE、SYSTIMESTAMP→CURRENT_TIMESTAMP）已正确转换。
- ⚠️ 触发器函数体内仍残留 `NVL` 的注释说明不影响；`TO_CHAR(CURRENT_TIMESTAMP, ...)` 已验证可用。
- **判定：已转换的 1 个触发器可用；但缺失 5 个触发器意味着业务行为不完整，交付前必须补齐或书面确认。**

### 7. 00_FUNCTION.sql — ❌ 不可直接使用

- 安装：9/16。7 个失败（`F_GET_AVG_SCORE`、`F_CALC_RANK`、`F_GET_STUDENT_CURSOR`、`F_GET_WEIGHTED_AVG`、`CLEAN_VALUE`、2 个 PIPELINE），错误为嵌套函数 inlining 残留（`END ADD_COND;`、`: IS NULL;`、`i_vsql 未知变量`）。
- **更严重的是"装上了也不对"**：
  1. **静默返回回退常量（最危险）**：`F_GET_TOTAL_SCORE`/`F_GET_GRADE_LEVEL` 安装成功，但体内嵌套函数（`VALIDATE_STUDENT_NO`、`BUILD_SQL`、`GET_THRESHOLD`、`DETERMINE_LEVEL`）被内联处理时**整体丢弃**（只留 `/* TODO: complex body */` 注释），调用全部落入 `WHEN OTHERS THEN RETURN 回退值`：
     - 实测 `F_GET_TOTAL_SCORE(10001)` → **-1**（应为总分）
     - 实测 `F_GET_GRADE_LEVEL(95)` → **4**（95 分应为 1 优秀）
     - `CALC_AVG/CALC_RANK` → **0**
     - 即：**调用不报错，永远拿错数据，且带测试数据（49 条）做黑盒验证也不容易发现**。
  2. **未转换的内建残留**：`F_GET_JOBNUM` 体内 `SELECT SEQ_JOBNUM.NEXTVAL INTO ... FROM DUAL` 未转换（DUAL 不存在、`.NEXTVAL` 非 PG 语法），运行时落入异常分支**恒定返回 1**，序列不推进（实测 `LAST_VALUE` 不变化）。
  3. **`EXECUTE IMMEDIATE`** 为 Oracle 语法，PG 应为 `EXECUTE`；plpgsql 对动态 SQL 不预校验，安装成功，**首调必失败**。
  4. **PIPELINE 函数**：`PIPE ROW(...)`、`RETURN;`（单值函数空返回）、`v_cursor%NOTFOUND`、`v_student STUDENT_MST`（无此类型）均为 Oracle 专有能力/不存在构造，即使语法修通也需要整体重写为 `RETURNS SETOF ... RETURN NEXT`。
  5. 提升出的"嵌套"全局函数（`CALC_AVG`、`GET_SUBJECT_NAME`、`VALIDATE_FILTER` 等）与包内同名对象（`PKG_SCORE_MANAGE.*`）形成两套 API，命名空间混乱，调用方容易引错。
- **判定：16 个函数中 0 个可以按 Oracle 语义直接使用。已装 9 个里至少 4 个静默返回错误数据。**

### 8. 00_PROCEDURE.sql — ❌ 不可直接使用

- 安装：1/5（仅 SP_PROCESS_SCORE 通过）。
- 4 个失败根因（均为已知转换能力缺口）：
  - `SP_CALC_RANK`/`SP_GEN_SCORE_REPORT`：`CURSOR CUR_STUDENT FOR SELECT ...` 显式游标声明——PG 8.4 **仅 plpgsql 函数**支持，存储过程不支持 → `语法错误 附近 "FOR"`；
  - `SP_BATCH_IMPORT_SCORE`/`SP_BATCH_DELETE_SCORE`：`BULK COLLECT INTO` 未转换 → `语法错误 附近 "COLLECT"`。
- ⚠️ **已安装的 SP_PROCESS_SCORE 运行时也必然失败**：函数体含 `COMMIT;`/`ROLLBACK;`，而 **PG 8.4 的 plpgsql 不支持事务语句**（PG 11 才加入）→ `invalid transaction termination`。Oracle 自治事务（PRAGMA AUTONOMOUS_TRANSACTION）语义在 PG 8.4 无等价物，需 dblink/pg_background 等外部方案。
- **判定：不可直接使用。5 个存储过程无一可用（4 个装不上 + 1 个运行时失败）。**

### 9. 00_PACKAGE.sql — 🟡 有限可用（只是"签名壳"）

- 安装 13/13：转换策略是 `PACKAGE → SCHEMA + 桩对象`（每个 spec 声明变成 `-- TODO: implement` 的空实现 / `RETURN NULL`）。
- 桩本身合法，作用是提供兼容签名 + schema 命名空间，让依赖该包的对象（同义词、调用方）能编译安装。
- ⚠️ 风险点：若 00_PACKAGE_BODY.sql 的对应实现**安装失败**（实际 12 个失败），调用会落到**桩的空实现（静默返回 NULL / 什么都不做）**，而不是报错——"包行为静默消失"。
- **判定：作为结构壳可用；业务语义完全依赖 BODY 文件，见下条。**

### 10. 00_PACKAGE_BODY.sql — 🟡 有限可用（全产物质量最差的大文件）

- 安装：16/28。12 个失败按根因：
  | 根因 | 对象 |
  |---|---|
  | `DBMS_SESSION.SET_CONFIG` 未映射（残留 `set_config` 裸调用） | FUNC_INIT、PBATCH、P_GEN_REPORT |
  | OUT 参数 + 函数返回类型冲突（`i_nVALUE INOUT` 与 `RETURNS NUMERIC` 并存） | FUNC_GET_CONFIG、VALIDATE_SCORE_VALUE |
  | 嵌套子程序/游标残留语法 | FUNC_SETLOG、FUNC_CALC_RANK、PROC_INSERT_WORK、PROC_BATCH_SCORE_PROCESS、PROC_BATCH_RANK_UPDATE、P_UPDATE_RANK、F_GET_SCORE_LIST |
  | `UTL_FILE` 无 PG 对应 | PROC_EXPORT_CSV |
  | 包级嵌套类型引用 | PROC_BATCH_SCORE_PROCESS（`t_score_list` 已映射 TEXT[]，但嵌套函数 `i_sWS IN VARCHAR` 语法残留） |
- **已安装的 16 个中，运行时行为验证发现多处静默错误**（真实 PG 实测）：
  1. `FUNC_FORMAT_DATE(20261009)` → 返回 **`' '`（单个空格）**，应为 `2026-10-09`——日期格式化转换丢失，静默返回几乎空值；
  2. `FUNC_GET_GRADE_LEVEL(95)` → **4**（应为 1）——`set_config` 阈值读取失败后走回退，静默给错等级；
  3. `FUNC_CALC_TOTAL(10001, NULL)` → **0**（分数为 0 时的回退值，无法与真实 0 分区分）；
  4. `F_GET_TOTAL`/`F_GET_AVG` 等"桥接函数"调用 `FUNC_CALC_TOTAL(i_nSTUDENT_NO, NULL)` 裸名：plpgsql 未限定函数名按**调用时 search_path** 解析，实测报 `function func_calc_total(numeric, unknown) does not exist`。即**包内跨函数调用要求部署时把 `pkg_score_manage` 加入 search_path，否则全部失败**（框架验证恰好因沙箱路径包含该 schema 而"通过"安装，运行时差异未被 L3 暴露）；
  5. 已装过程（P_PROCESS_SCORE 等）体内含 `COMMIT`，同文件 8 的结论——PG 8.4 运行时必失败；
  6. L1 静态检查仅 70.8 分，L2 仅 15 分，与上述实测一致。
- **判定：不可按 Oracle 语义直接使用。可安装部分中存在多处"不报错但给错值"的隐蔽缺陷，属于验收一票否决级问题。**

### 11. 00_SYNONYM.sql — ✅ 可直接使用（语义降级，已知代价）

- 安装 11/11。转换策略：`PUBLIC SYNONYM → 同 schema 视图`（`sm_student_mst AS SELECT * FROM ...`）。
- 实测：查询正常；简单列映射视图支持 INSERT（实测经 `sm_student_mst` 插入成功并落到基表）。
- ⚠️ 与 Oracle SYNONYM 的已知偏差：
  - 不透明于 DDL/运维（TRUNCATE 同义词等不可用）；
  - 3 个"序列同义词"（`sm_seq_student` 等）只是 `SELECT * FROM 序列` 的伪视图，**不能用于取 NEXTVAL**（源码中无调用方，实际无影响）；
  - 视图创建不校验基表（PG 特性），所以即使基表安装晚于它也能成功；
  - DML 仅对"单表简单列映射"视图有效，当前产物均满足。
- **判定：可直接使用，语义降级属已知合理转换。**

### 12. 00_TEST_DATA.sql — ✅ 可直接使用

- 49/49 全部插入成功（科目 8、教师、学生 9、成绩 1728 行的来源等）。数据完整进入基表，触发器联动无冲突。
- 结尾 `COMMIT;` 在 autocommit/psql 下无副作用。
- ⚠️ 唯一注意：数据依赖触发器 TRG_STUDENT_BI 补列值，行为与"6 触发器齐全"的 Oracle 源相比缺少 TRG_SCORE_BIU 的等级自动计算——测试数据里的 GRADE_LEVEL/IS_PASS 是 INSERT 自带值，不受影响。
- **判定：可直接使用。**

### 13. CREATE_ALL.sql — ❌ 不可直接使用

- 内容仍是**未转换的 SQL*Plus 驱动脚本**：`@@TABLE/001_STUDENT_MST.sql`、`PROMPT '...'` 均为 Oracle SQL*Plus 元命令，psql 无法执行（`@@`/`PROMPT` 语法错，L1/L2 已识别为驱动脚本跳过、L3 亦跳过）。
- 目录引用（`@@TABLE/...`）与实际产物目录结构（`output/` 平铺文件名）也不匹配，即使换解释器也找不到文件。
- 实际部署需**按依赖顺序手动执行**：
  001_STUDENT_MST → 002_SUBJECT_MST → 00_SEQUENCE → 001_TYPES → 00_SYNONYM → 00_VIEW → 00_TRIGGER → 00_PACKAGE → 00_PACKAGE_BODY → 00_FUNCTION → 00_PROCEDURE → 00_TEST_DATA
  （该顺序即验证框架 KIND_RANK 采用的依赖排序）
- **判定：不可直接使用；需人工替换为 PG 一键安装脚本（如 `\i` 或 psql -f 串联）。**

---

## 横向问题汇总（跨文件）

### A. 最危险：静默返回错误数据（L3 安装无法发现）

plpgsql 创建函数时不校验体内 SQL/列引用，导致"安装成功"掩盖了大量运行时缺陷。已实测确认的静默错误：

| 对象 | 现象 | 正确值应为 |
|---|---|---|
| F_GET_TOTAL_SCORE | 恒返 -1（异常回退） | 实际总分 |
| F_GET_GRADE_LEVEL | 95 分返 4 | 1（优秀） |
| PKG FUNC_FORMAT_DATE | 返 `' '`（空串） | '2026-10-09' |
| PKG FUNC_GET_GRADE_LEVEL | 95 分返 4（set_config 失败回退） | 1 |
| PKG FUNC_CALC_TOTAL | 恒返 0（回退） | 实际总分 |
| F_GET_JOBNUM | 恒返 1（DUAL/.NEXTVAL 未转换） | 递增批号 |
| CALC_AVG / CALC_RANK | 恒返 0 | 平均值/名次 |
| GET_*_of_*（TYPE 方法 6 个） | 调用即报列不存在 | 方法返回值 |

**含义：当前"87.0 分 / 141 对象安装成功"不能作为"可用"依据；L4 行为等价层缺失（无金标准用例）时，静默错误完全不可见。**

### B. Oracle 专有能力无 PG 8.4 等价物（需人工/架构方案）

| 能力 | 涉及对象 | 说明 |
|---|---|---|
| 自治事务 PRAGMA AUTONOMOUS_TRANSACTION + 包内 COMMIT/ROLLBACK | TRG_STUDENT_AU、FUNC_SETLOG、SP_*、P_* | PG 8.4 plpgsql 不支持事务语句（11+ 才支持）；需 dblink/pg_background |
| PIPELINED（PIPE ROW / RETURN ... USING BULK COLLECT） | F_GET_STUDENT_LIST_PIPELINE、F_GET_SCORE_LIST_PIPELINE | 需重写为 RETURNS SETOF + RETURN NEXT |
| 显式游标声明于存储过程 | SP_CALC_RANK、SP_GEN_SCORE_REPORT | PG 8.4 仅函数支持；需重写为 FOR ... LOOP 或改函数 |
| BULK COLLECT / FORALL | SP_BATCH_*、PROC_BATCH_* | 需逐条重写 |
| UTL_FILE | PROC_EXPORT_CSV | 无对应；需应用层落盘 |
| 复合触发器 | TRG_SCORE_COMPOUND | PG 无对应概念 |
| INSTEAD OF 于物化更新 | TRG_V_STUDENT_IO | PG 视图不支持，需触发器替代或换建模 |

### C. 转换完整性缺口（静默丢失）

- **5/6 触发器丢失**（00_TRIGGER.sql，见文件 6）——最严重的完整性缺口；
- 包级 PIPELINE / 嵌套子程序"complex body"被丢弃为 `/* TODO: complex body */` 注释（00_FUNCTION/00_PACKAGE_BODY 中大量）；
- PIPELINE 相关嵌套函数被提升为裸全局函数，形成双套命名空间。

### D. 部署注意事项

1. **search_path 必须包含 `pkg_score_manage`**，否则包内裸名跨函数调用运行时失败（见文件 10-4）。
2. 临时表 TEMP_SCORE_BATCH 是会话级的，多会话批量共享场景需改为普通表或按会话使用。
3. 触发器依赖安装顺序：先表 → 触发器 → 测试数据，顺序见文件 13。
4. 序列访问必须用 `nextval('...')`，不能沿用 `.NEXTVAL` 写法。

---

## 验收建议

1. **可以交付/验收的部分**（结构层）：表、序列、类型（复合类型部分）、视图、测试数据、同义词 + TRG_STUDENT_BI。
2. **不可验收、需返工的部分**（行为层）：00_FUNCTION.sql 全量、00_PROCEDURE.sql 全量、00_PACKAGE_BODY.sql 的 12 个失败对象 + 已装对象的静默错误、001_TYPES.sql 的 6 个方法函数、CREATE_ALL.sql。
3. **必须在交付前人工决策**：5 个丢失触发器的行为是否需要（建议：TRG_SCORE_BIU、TRG_SYSTEM_CONFIG_BD 属业务正确性，必须补）。
4. **建议补充 L4 金标准用例**（从 00_TEST_DATA 现有数据构造输入/输出快照），否则"静默返回错误数据"类缺陷永远无法被安装级验证发现。
