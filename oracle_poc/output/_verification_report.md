# 项目验证报告 - oracle_poc

- 生成时间: 2026-10-14T22:45:32
- 制品数: 12
- **总分: 99.6 - A 优**
- Gate: 通过

## 套件级 L3/L4

| Level | 名称 | 权重 | 得分 | 结果 |
|---|---|---|---|---|
| L3 | 安装验证 | 25% | 100.0 | 通过 |
| L3.5 | 首调冒烟 | 0% | 100.0 | 通过 |
| L4 | 行为等价 | 40% | 100.0 | 通过 |

### L3 按类型安装统计

| 类型 | 总数 | 成功 | 失败 |
|---|---|---|---|
| DATA | 49 | 49 | 0 |
| FUNCTION | 189 | 189 | 0 |
| INDEX | 2 | 2 | 0 |
| PROCEDURE | 37 | 37 | 0 |
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
| FUNCTION/00_FUNCTION.sql | 101 | 101 | 0 |
| PACKAGE/00_PACKAGE.sql | 13 | 13 | 0 |
| PACKAGE_BODY/00_PACKAGE_BODY.sql | 94 | 94 | 0 |
| PROCEDURE/00_PROCEDURE.sql | 5 | 5 | 0 |
| SEQUENCE/00_SEQUENCE.sql | 6 | 6 | 0 |
| SYNONYM/00_SYNONYM.sql | 11 | 11 | 0 |
| TABLE/001_STUDENT_MST.sql | 3 | 3 | 0 |
| TABLE/002_SUBJECT_MST.sql | 8 | 8 | 0 |
| TRIGGER/00_TRIGGER.sql | 14 | 14 | 0 |
| TYPE/001_TYPES.sql | 16 | 16 | 0 |
| VIEW/00_VIEW.sql | 7 | 7 | 0 |

### L3.5 首调冒烟 (DoD-3: 能安装 ≠ 可用)

对象总数 121，通过 120，失败 0，受阻 0，未等到触发 0，无法构造调用 1

### L4 用例明细

状态分布: pass=65

## 制品明细

| 制品 | L1 | L2 | 总分 | 等级 | Gate |
|---|---|---|---|---|---|
| TABLE/001_STUDENT_MST.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| TABLE/002_SUBJECT_MST.sql | 92.9 | 100.0 | 97.0 | A 优 | 通过 |
| SEQUENCE/00_SEQUENCE.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| TYPE/001_TYPES.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| VIEW/00_VIEW.sql | 97.6 | 100.0 | 99.0 | A 优 | 通过 |
| TRIGGER/00_TRIGGER.sql | 97.7 | 100.0 | 99.0 | A 优 | 通过 |
| FUNCTION/00_FUNCTION.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| PROCEDURE/00_PROCEDURE.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| PACKAGE/00_PACKAGE.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| PACKAGE_BODY/00_PACKAGE_BODY.sql | 97.6 | 100.0 | 99.0 | A 优 | 通过 |
| SYNONYM/00_SYNONYM.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
| DATA/00_TEST_DATA.sql | 100.0 | 100.0 | 100.0 | A 优 | 通过 |
