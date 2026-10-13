# Oracle PL/SQL 业务模拟系统 - 学生成绩管理系统

## 目的
模拟 GKB 教育系统的复杂度，用于转换为 PostgreSQL。
包含所有 Oracle 特有的 PL/SQL 特性。

## 目录结构
```
01.SQL/
├── TABLE/           # 表定义
├── TYPE/            # 类型定义(对象类型、嵌套表)
├── VIEW/            # 视图定义
├── FUNCTION/        # 函数定义(含PIPELINED)
├── PROCEDURE/       # 存储过程定义
├── PACKAGE/         # 包规格
├── PACKAGE_BODY/    # 包主体(多层嵌套)
├── SEQUENCE/        # 序列定义
├── SYNONYM/         # 同义词定义
├── TRIGGER/         # 触发器定义
└── DATA/            # 测试数据
```

## 创建顺序（依赖关系）
1. TABLE (表必须最先创建)
2. SEQUENCE (序列依赖表)
3. TYPE (类型依赖表)
4. VIEW (视图依赖表)
5. TRIGGER (触发器依赖表和视图)
6. FUNCTION (函数依赖表、视图、类型)
7. PROCEDURE (存储过程依赖表、视图、类型)
8. PACKAGE (包依赖以上所有)
9. PACKAGE_BODY (包主体依赖包规格)
10. SYNONYM (同义词依赖所有对象)
11. DATA (测试数据)

## Oracle 特有特性

### 1. 序列 (SEQUENCE) - 6个
- `SEQ_STUDENT_NO` - 学生编号序列
- `SEQ_SUBJECT_CD` - 科目代码序列
- `SEQ_TEACHER_CD` - 教师编号序列
- `SEQ_JOBNUM` - 批次号序列
- `SEQ_LOG_ID` - 日志ID序列
- `SEQ_ATTACHMENT_ROW` - 附件行号序列

### 2. 同义词 (SYNONYM) - 13个
- 公有同义词: `SM_STUDENT_MST`, `SM_SCORE_REC` 等
- 序列同义词: `SM_SEQ_STUDENT`, `SM_SEQ_JOBNUM` 等

### 3. 类型 (TYPE) - 8个
| 类型名 | 说明 |
|--------|------|
| A_CONS_PRM | 通用参数数组(嵌套表) |
| SCORE_RECORD | 成绩记录对象(带方法) |
| SCORE_LIST | 成绩列表(嵌套表) |
| STUDENT_RECORD | 学生记录对象(带方法) |
| STUDENT_LIST | 学生列表(嵌套表) |
| CONFIG_RECORD | 配置记录对象 |
| CONFIG_LIST | 配置列表 |
| REF_CURSOR_T | REF CURSOR类型(带静态函数) |

### 4. 表 (TABLE) - 9个
| 表名 | 说明 |
|------|------|
| STUDENT_MST | 学生信息主表 |
| SUBJECT_MST | 科目信息主表 |
| TEACHER_MST | 教师信息主表 |
| SCORE_REC | 成绩记录表 |
| WK_SCORE_REPORT | 报表工作表 |
| ATTACHMENT_STORAGE | 附件存储表(BLOB) |
| SYSTEM_CONFIG | 系统配置表 |
| PROCESS_LOG | 过程日志表 |
| TEMP_SCORE_BATCH | 临时表(GLOBAL TEMPORARY) |

### 5. 视图 (VIEW) - 7个
| 视图名 | 说明 |
|--------|------|
| V_STUDENT_SCORE | 学生成绩一览 |
| V_STUDENT_STATS | 学生成绩统计 |
| V_SCORE_RANK | 成绩排名(分析函数) |
| V_SUBJECT_STATS | 科目成绩统计 |
| V_TEACHER_STATS | 教师授课统计 |
| V_DEPENDENCY_GRAPH | 表依赖关系 |
| V_STUDENT_WITH_SCORE | 可更新视图 |

### 6. 触发器 (TRIGGER) - 6个
| 触发器名 | 类型 | 说明 |
|---------|------|------|
| TRG_STUDENT_BI | BEFORE INSERT | 插入前触发器 |
| TRG_STUDENT_AU | AFTER UPDATE | 更新后触发器(自治事务) |
| TRG_SCORE_BIU | BEFORE INSERT/UPDATE | 成绩计算触发器 |
| TRG_SYSTEM_CONFIG_BD | BEFORE DELETE | 行级安全触发器 |
| TRG_SCORE_COMPOUND | COMPOUND | 复合触发器 |
| TRG_V_STUDENT_IO | INSTEAD OF | 视图更新触发器 |

### 7. 函数 (FUNCTION) - 10个
| 函数名 | 说明 | 特殊特性 |
|--------|------|----------|
| F_GET_TOTAL_SCORE | 获取总成绩 | 动态SQL、多层嵌套 |
| F_GET_AVG_SCORE | 获取平均成绩 | 嵌套加权计算 |
| F_GET_GRADE_LEVEL | 计算等级 | 4层嵌套阈值获取 |
| F_CALC_RANK | 计算排名 | 动态SQL构建 |
| F_GET_STUDENT_LIST_PIPELINE | 学生列表 | PIPELINED |
| F_GET_SCORE_LIST_PIPELINE | 成绩列表 | PIPELINED |
| F_GET_STUDENT_CURSOR | 学生游标 | REF CURSOR |
| F_GET_WEIGHTED_AVG | 加权平均 | 嵌套游标处理 |
| F_GET_CONFIG_VALUE | 配置获取 | 嵌套验证 |
| F_GET_JOBNUM | 批次号生成 | - |

### 8. 存储过程 (PROCEDURE) - 5个
| 过程名 | 说明 | 特殊特性 |
|--------|------|----------|
| SP_PROCESS_SCORE | 成绩处理 | - |
| SP_GEN_SCORE_REPORT | 报表生成 | 游标处理 |
| SP_BATCH_IMPORT_SCORE | 批量导入 | BULK COLLECT + FORALL |
| SP_CALC_RANK | 排名计算 | - |
| SP_BATCH_DELETE_SCORE | 批量删除 | BULK COLLECT + FORALL |

### 9. 包 (PACKAGE/PACKAGE_BODY) - 1个
**PKG_SCORE_MANAGE**

包含：
- 15+个子程序
- 多层嵌套(最深4层)
- 全局变量和常量
- 包内游标定义
- 包内类型定义
- BULK COLLECT + FORALL
- 自治事务

## 代码统计
| 类型 | 文件数 | 行数 |
|------|--------|------|
| TABLE | 2 | 178 |
| SEQUENCE | 1 | 63 |
| SYNONYM | 1 | 18 |
| TYPE | 1 | 166 |
| VIEW | 1 | 215 |
| TRIGGER | 1 | 171 |
| FUNCTION | 1 | 940 |
| PROCEDURE | 1 | 371 |
| PACKAGE | 1 | 144 |
| PACKAGE_BODY | 1 | 1364 |
| DATA | 1 | 162 |
| CREATE_ALL | 1 | 63 |
| **总计** | **14** | **3855** |

## PostgreSQL 转换要点

### 不支持特性
| Oracle特性 | 转换方式 |
|-----------|----------|
| SEQUENCE | GENERATED ALWAYS AS IDENTITY 或独立序列 |
| SYNONYM | 使用 SCHEMA 路径或视图替代 |
| OBJECT TYPE | CREATE TYPE (有限支持) |
| NESTED TABLE | 数组类型或临时表 |
| REF CURSOR | SETOF 函数或事务临时表 |
| PACKAGE | 使用 SCHEMA + 函数替代 |
| PIPELINED | RETURNS TABLE 或 SETOF |
| BULK COLLECT | 使用数组 + UNNEST 或循环 |
| FORALL | 使用 FOR 循环 |
| 自治事务 | dblink 或应用层处理 |
| 多层嵌套子程序 | 需重构为独立函数 |

### 支持特性
- 分析函数 (RANK, LAG, LEAD, NTILE 等)
- 递归 WITH
- 触发器
- 动态 SQL (语法略有不同)
