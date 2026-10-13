# HANDOFF.md - Oracle PL/SQL 业务模拟系统

> 最后更新: 2026-10-09

## 任务概述

**目标**: 创建一个完整的 Oracle PL/SQL 业务系统，模拟 GKB 教育系统的复杂度，用于转换为 PostgreSQL。

**状态**: ✅ Oracle 侧已完成;PostgreSQL 转换产物已生成于 `oracle_poc/output/`(详见仓库根 `HANDOFF.md`)

---

## 项目结构

```
oracle_poc/
├── 01.SQL/
│   ├── TABLE/           # 表定义 (9个表)
│   ├── SEQUENCE/        # 序列定义 (6个)
│   ├── SYNONYM/         # 同义词 (13个)
│   ├── TYPE/            # 类型定义 (8个)
│   ├── VIEW/           # 视图定义 (7个)
│   ├── TRIGGER/        # 触发器定义 (6个)
│   ├── FUNCTION/       # 函数定义 (10个)
│   ├── PROCEDURE/      # 存储过程 (5个)
│   ├── PACKAGE/        # 包规格
│   ├── PACKAGE_BODY/   # 包主体
│   ├── DATA/           # 测试数据
│   └── CREATE_ALL.sql  # 一键创建脚本
├── README.md
└── HANDOFF.md
```

---

## 已完成组件

### 1. 表定义 (TABLE) - 9个
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

### 2. 序列 (SEQUENCE) - 6个
- SEQ_STUDENT_NO, SEQ_SUBJECT_CD, SEQ_TEACHER_CD
- SEQ_JOBNUM, SEQ_LOG_ID, SEQ_ATTACHMENT_ROW

### 3. 同义词 (SYNONYM) - 13个
- 公有同义词 + 序列同义词

### 4. 类型 (TYPE) - 8个
| 类型名 | 说明 | 特性 |
|--------|------|------|
| A_CONS_PRM | 通用参数数组 | 嵌套表 |
| SCORE_RECORD | 成绩记录对象 | 带方法 |
| SCORE_LIST | 成绩列表 | 嵌套表 |
| STUDENT_RECORD | 学生记录对象 | 带方法 |
| STUDENT_LIST | 学生列表 | 嵌套表 |
| CONFIG_RECORD | 配置记录对象 | - |
| CONFIG_LIST | 配置列表 | 嵌套表 |
| REF_CURSOR_T | REF CURSOR | 静态函数 |

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
| F_GET_TOTAL_SCORE | 获取总成绩 | 动态SQL, 多层嵌套 |
| F_GET_AVG_SCORE | 获取平均成绩 | 嵌套加权计算 |
| F_GET_GRADE_LEVEL | 计算等级 | **4层嵌套** |
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
**PKG_SCORE_MANAGE** (1508行)

包含：
- 全局变量和常量
- 包内游标定义 (4个)
- 包内类型定义
- 15+个子程序
- 多层嵌套结构
- BULK COLLECT + FORALL
- 自治事务

---

## 复杂度分析

### 代码统计
| 指标 | 数值 |
|------|------|
| SQL文件数 | 14个 |
| 代码行数 | **3855行** |
| 最大文件 | PACKAGE_BODY (1364行) |

### Oracle → PostgreSQL 转换难点

| 难度 | 特性 | 说明 |
|------|------|------|
| 🔴 最高 | 多层嵌套 | 最深4层，PG不支持 |
| 🔴 最高 | PACKAGE | 需要拆分为SCHEMA+函数 |
| 🟠 高 | 自治事务 | 需要重构逻辑 |
| 🟠 高 | BULK COLLECT | 需要改为循环 |
| 🟠 高 | FORALL | 需要改为循环 |
| 🟡 中 | PIPELINED | 需要改用RETURNS TABLE |
| 🟡 中 | REF CURSOR | 需要改用SETOF |
| 🟡 中 | SYNONYM | 需要用SCHEMA替代 |
| 🟢 低 | 触发器 | 语法差异，可转换 |
| 🟢 低 | 动态SQL | 语法略有不同 |

### 嵌套层级示意

```
FUNC_GET_GRADE_LEVEL (层级1)
 └── GET_THRESHOLD (层级2)
      ├── GET_FROM_CACHE (层级3)
      └── GET_FROM_DB (层级3)
           └── DO_QUERY (层级4) ← 最深

PROC_PROCESS_STUDENT (层级1)
 └── CALC_STATS (层级2)
      ├── CALC_TOTAL_SCORE (层级3)
      ├── CALC_AVG_SCORE (层级3)
      └── CALC_PASS_FAIL (层级3)
```

---

## 创建顺序

Oracle 对象创建必须遵循依赖顺序：

```
1. TABLE      (表必须最先创建)
2. SEQUENCE   (序列依赖表)
3. TYPE       (类型依赖表)
4. VIEW       (视图依赖表) ← 必须在TRIGGER之前
5. TRIGGER    (触发器依赖表和视图)
6. FUNCTION   (函数依赖表、视图、类型)
7. PROCEDURE  (存储过程依赖表、视图、类型)
8. PACKAGE    (包依赖以上所有)
9. PACKAGE_BODY (包主体依赖包规格)
10. SYNONYM   (同义词依赖所有对象)
11. DATA      (测试数据)
```

执行命令：
```bash
sqlplus user/pass@db @CREATE_ALL.sql
```

---

## 测试验证

### 函数测试
```sql
SELECT F_GET_TOTAL_SCORE(10001, NULL) FROM DUAL;
SELECT F_GET_AVG_SCORE(10001, NULL) FROM DUAL;
SELECT F_GET_GRADE_LEVEL(95) FROM DUAL;
SELECT F_CALC_RANK(10001, 1, 1, 1) FROM DUAL;
```

### 包测试
```sql
DECLARE
    v_rtn NUMBER;
BEGIN
    PKG_SCORE_MANAGE.P_GEN_REPORT(
        i_nNENDO => 2024,
        i_nGRADE => 1,
        i_nCLASS_NO => 1,
        i_vWSNUM => 'WS001',
        o_nRTN => v_rtn
    );
END;
/
```

### 视图测试
```sql
SELECT * FROM V_STUDENT_SCORE WHERE STUDENT_NO = 10001;
SELECT * FROM V_SCORE_RANK WHERE GRADE = 1 ORDER BY CLASS_RANK;
```

---

## 关键发现

### 1. 完整性检查结果
- ✅ VIEW定义在VIEW文件中，不在TRIGGER中
- ✅ 创建顺序正确
- ✅ 无空目录或孤立文件
- ✅ 依赖关系正确

### 2. 转换策略建议
- 多层嵌套 → 提取为独立函数
- PACKAGE → SCHEMA + 函数集合
- BULK COLLECT/FORALL → FOR循环
- 自治事务 → 应用层处理或dblink

---

## 已知问题与风险

| 风险 | 影响 | 建议 |
|------|------|------|
| 多层嵌套无法直接转换 | 高 | 需要人工重构 |
| 包结构无法保留 | 高 | 需要重新设计 |
| BULK COLLECT性能优势丢失 | 中 | 使用批量API替代 |
| 自治事务无法模拟 | 中 | 使用触发器函数 |

---

## 下一步

> PostgreSQL 侧已于 2026-10-09 完成首轮转换，验证得分 **86.6 (B 良)**，真实 PG 15.19 安装 140/164。
> 完整缺陷清单与优先级见仓库根 `HANDOFF.md`。此处只保留摘要。

1. **PostgreSQL 版本** ✅ 已生成 `oracle_poc/output/`
   - 表/序列/类型/视图/同义词/触发器/测试数据 全部装上
   - 函数、存储过程、包主体仍有 24 个对象安装失败（PIPELINED、REF CURSOR、嵌套子程序、BULK COLLECT/FORALL、DBMS_SESSION.SET_CONFIG、UTL_FILE 等）

2. **功能验证** ⏳ 待办
   - 尚无金标准用例库，L4 行为等价一直跳过（占 40% 权重）
   - 可从本文档「测试验证」章节的用例起步

3. **性能测试** ⏳ 待办

### 重新生成与验证

```bash
cd D:/JIP/poc
python scripts/convert_oracle_poc.py --clean
python scripts/verify_oracle_poc_output.py
```

---

## 修改历史

| 日期 | 修改内容 |
|------|----------|
| 2024-01-30 | 初始创建，完成所有组件 |
| 2024-01-30 | 修复VIEW定义位置，调整创建顺序 |
| 2024-01-30 | 增强多层嵌套复杂度 |
| 2024-01-30 | 文档完善，git提交 |

---

**交接人**: POC Team  
**交接时间**: 2024-01-30
