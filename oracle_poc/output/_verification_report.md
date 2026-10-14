# 项目验证报告 - oracle_poc

- 生成时间: 2026-10-14T17:05:19
- 制品数: 12
- **总分: 85.3 - B 良**
- Gate: 未通过

## Gate 失败项

- [suite] L4 仅执行 7/65 条用例（覆盖率 11% < 50%），行为验证不充分
- [suite] L4 用例未通过 1 条
- L3.5 首调冒烟 3 个对象失败（总计 42）

## 套件级 L3/L4

| Level | 名称 | 权重 | 得分 | 结果 |
|---|---|---|---|---|
| L3 | 安装验证 | 25% | 100.0 | 通过 |
| L3.5 | 首调冒烟 | 0% | 92.9 | 未通过 |
| L4 | 行为等价 | 40% | 85.7 | 未通过 |

### L3 按类型安装统计

| 类型 | 总数 | 成功 | 失败 |
|---|---|---|---|
| DATA | 49 | 49 | 0 |
| FUNCTION | 113 | 113 | 0 |
| INDEX | 2 | 2 | 0 |
| PROCEDURE | 31 | 31 | 0 |
| SCHEMA | 2 | 2 | 0 |
| SEQUENCE | 6 | 6 | 0 |
| TABLE | 9 | 9 | 0 |
| TRIGGER | 7 | 7 | 0 |
| TYPE | 8 | 8 | 0 |
| VIEW | 18 | 18 | 0 |

### L3 按制品

| 制品 | 对象数 | 成功 | 失败 |
|---|---|---|---|
| DATA/00_TEST_DATA.sql | 49 | 49 | 0 |
| FUNCTION/00_FUNCTION.sql | 52 | 52 | 0 |
| PACKAGE/00_PACKAGE.sql | 13 | 13 | 0 |
| PACKAGE_BODY/00_PACKAGE_BODY.sql | 61 | 61 | 0 |
| PROCEDURE/00_PROCEDURE.sql | 5 | 5 | 0 |
| SEQUENCE/00_SEQUENCE.sql | 6 | 6 | 0 |
| SYNONYM/00_SYNONYM.sql | 11 | 11 | 0 |
| TABLE/001_STUDENT_MST.sql | 3 | 3 | 0 |
| TABLE/002_SUBJECT_MST.sql | 8 | 8 | 0 |
| TRIGGER/00_TRIGGER.sql | 14 | 14 | 0 |
| TYPE/001_TYPES.sql | 16 | 16 | 0 |
| VIEW/00_VIEW.sql | 7 | 7 | 0 |

### L3.5 首调冒烟 (DoD-3: 能安装 ≠ 可用)

对象总数 42，通过 39，失败 3，受阻 0，未等到触发 0，无法构造调用 0

**首调失败明细**（fail，计入 DoD-3）

| 类型 | 对象 | 首调 SQL | 错误 |
|---|---|---|---|
| FUNCTION | oracle_poc_verify.calc_weighted_sum | SELECT * FROM "oracle_poc_verify"."calc_weighted_s | 错误:  ORA: 游标变量"i_cursor"是空的 \| CONTEXT:  在RAISE的第18行的PL/pgSQL函数calc_weighted_sum(refcursor) \| CONTE |
| FUNCTION | oracle_poc_verify.calc_total_weight | SELECT * FROM "oracle_poc_verify"."calc_total_weig | 错误:  ORA: 游标变量"i_cursor"是空的 \| CONTEXT:  在RAISE的第17行的PL/pgSQL函数calc_total_weight(refcursor) \| CONTE |
| PROCEDURE | oracle_poc_verify.sp_process_score | CALL "oracle_poc_verify"."sp_process_score"(NULL:: | 错误:  ORA: null value in column "student_no" of relation "score_rec" violates not-null constraint \|  |

### L4 用例明细

状态分布: fail=1，pass=6，skipped=58

| 用例 | 状态 | 描述 | 期望 | 实际 | 说明 |
|---|---|---|---|---|---|
| VIEW_V_STUDENT_SCORE | FAIL | 视图 V_STUDENT_SCORE 行数 = 8 | 8 | 1536 | 返回值与金标准不一致 |

## 制品明细

| 制品 | L1 | L2 | 总分 | 等级 | Gate |
|---|---|---|---|---|---|
| TABLE/001_STUDENT_MST.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| TABLE/002_SUBJECT_MST.sql | 92.9 | 100.0 | 97.0 | A 优 | 通过 |
| SEQUENCE/00_SEQUENCE.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| TYPE/001_TYPES.sql | 100.0 | 85.0 | 91.4 | B 良 | 通过 |
| VIEW/00_VIEW.sql | 97.6 | 100.0 | 99.0 | A 优 | 通过 |
| TRIGGER/00_TRIGGER.sql | 97.7 | 20.0 | 53.3 | D 差 | 通过 |
| FUNCTION/00_FUNCTION.sql | 99.9 | 0.0 | 42.8 | F 不合格 | 通过 |
| PROCEDURE/00_PROCEDURE.sql | 97.6 | 85.0 | 90.4 | B 良 | 通过 |
| PACKAGE/00_PACKAGE.sql | 100.0 | 95.0 | 97.1 | A 优 | 通过 |
| PACKAGE_BODY/00_PACKAGE_BODY.sql | 97.5 | 0.0 | 41.8 | F 不合格 | 通过 |
| SYNONYM/00_SYNONYM.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| DATA/00_TEST_DATA.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
