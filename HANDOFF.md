# HANDOFF.md — oracle_poc Oracle→PG 转换

> **当前最新工作状态**，不是历史日志。接手按顺序读：本文件 → `docs/最终实施路径_生产级_2026-10-09.md`（实施唯一权威）→ 实际代码与测试。

---

## 0. DoD（对齐实施路径 §5.2）

| DoD | 验收 |
|---|---|
| DoD-1 | psql 一键安装 0 错误 |
| DoD-2 | 残留扫描器 major=0 |
| DoD-3 | L3.5 冒烟 0 fail（业务决策项除外）|
| DoD-4 | L4 业务等价 100% |
| DoD-5 | Gate ≥ 90（A）|
| DoD-6 | 规则修复后必须重转产物并复验，禁止 commit 已修但产物未重跑 |
| DoD-7 | 对象数缺失即判 fail |

关键原则：
- "能安装 ≠ 可用"——所有 PL/SQL 对象必须经 L3.5 冒烟
- "能转换 ≠ 没丢东西"——DoD-2 查残留，DoD-7 查缺失
- **铁律：所有 PG 语义结论必须 live PG 15.19 实测，不能凭记忆或文档转述**

---

## 1. 当前基线（2026-10-13，PG 15.19 真连实测）

| 指标 | 当前 | 目标 |
|---|---|---|
| 总分 | **83.0 / C** | ≥90 / A |
| L3 安装 | **225/243**（18 fail）| 243/243 |
| L3.5 冒烟 | **33/38 + 5 fail** | 0 fail |
| L4 业务等价 | **7/65 实际执行**（11%）| 100% |
| 残留扫描 | **major 30**（critical 0）| 0 |
| 完整性 | dropped_objects = **0** | 0 ✅ |
| 单测 | **386/386** | — |
| Gate | **未通过** | 通过 |

### 残留 30 处归因

| 归属 | 数 | 明细 |
|---|---|---|
| P4-3 复杂体 | 19 | UTL_FILE 9 + BULK COLLECT 4 + FORALL 4 + PIPE ROW 2 |
| P4-2 续 | 12 | `-- TODO:` ×12（`00_PACKAGE.sql`）|
| P2-6 | 11 | EXECUTE IMMEDIATE 7 + FROM DUAL 4 |
| P2-2/P2-3 残留 | 2 | `(+)` 1 + `%TYPE` 1 |
| SQLCODE/SQLERRM/ROWCOUNT | 5 | P2-5 漏网 |

### 剩余 L3 18 失败（按可动手顺序）

| 类 | 数 | 归属 |
|---|---|---|
| UTL_FILE 模式不存在 | 3 | P4-3 |
| BULK COLLECT 语法残留 | 2 | P4-3 |
| 返回值类型不可变 | 2 | P4-4c |
| 输出参数（多 OUT 只补 1 个） | 3 | P4-4c |
| 语法残留 | 8 | 逐个修 |

### L3.5 5 fail

`calc_weighted_sum` / `calc_total_weight`（游标变量为空）/ `sp_process_score`（NOT NULL）/
`sp_gen_score_report` / `sp_calc_rank`（函数内 COMMIT → P4-3 自治事务专项）

### 装上即坏对象（L3 通过但首调返回错误值）

| 对象 | 现象 | 归属 |
|---|---|---|
| `F_GET_TOTAL_SCORE(10001)` | 恒返 -1 | P4-2/P4-4 |
| `F_GET_GRADE_LEVEL(95)` | 恒返 4（应为 1）| P4-2/P4-4 |
| `PKG.FUNC_FORMAT_DATE(20261009)` | 返 `' '` | P4-2/P4-4 |
| `F_GET_JOBNUM()` | 恒返 1 | P4-3 |
| `CALC_AVG` / `CALC_RANK` | 恒返 0 | P4-2/P4-4 |
| `GET_STATUS_OF_SCORE_RECORD` 等 5 TYPE 方法 | SELF_xxx 伪标识符 | P4-4 |

---

## 2. 任务状态

> 权威定义在 `docs/最终实施路径_生产级_2026-10-09.md`，本节为状态视图。
> **实际执行序**：P4-4c 剩余 L3 → P4-3 UTL_FILE/BULK COLLECT → P4-2 续 PACKAGE batch → P2-6 → P5-1 → P5-2 → P5-3/P5-4

| 任务 | 一句话 | 状态 |
|---|---|---|
| P1-1~P1-4 | 静默丢失/完整性/残留/冒烟 | ✅ 全部完成 |
| P2-1 | %SUBQUERY 回填 | ✅ 完成 |
| P2-2 | %TYPE 查表展开 | ✅ 完成 |
| P2-3 | (+) 外连接改写 | ✅ 完成 |
| P2-4 | 序列/同义词治理 | ✅ 完成 |
| P2-5 | 异常与运行时属性映射 | ✅ 完成（`584fd06`）|
| P2-6 | SYSTIMESTAMP/SYSDATE 转换 | ⬜ 待开始（0.5–1 天）|
| P3-1~P3-4 | AST/触发器升级/元数据 | ✅ 全部完成 |
| P4-1 | Agent 工具链 | ✅ 完成 |
| P4-2 a~h | TODO 桩回灌 | ✅ 完成（`2810905`）|
| P4-2R | END ;; 回归修复 | ✅ 完成（`541f1dc`）|
| **P4-2 续** | **PACKAGE batch（12 TODO）** | **⬜ 待开始（1–2 天）** |
| P4-3 | UTL_FILE/BULK COLLECT/自治事务 | 🟡 批次1+2 完成，**剩余 UTL_FILE 9 / BULK 2 / 自治事务** |
| W0 | 验证口径收口 | ✅ 完成（`b651980`）|
| **P4-4** | **TYPE 重设计** | 🟡 P4-4a ✅ + P4-4c 阶段1~7 ✅，**剩余 L3 18 失败** |
| P5-1 | L4 业务等价用例库 | ⬜ 待开始 |
| P5-2 | 端到端重转 + 全门禁 | ⬜ 待开始 |
| P5-3/P5-4 | 防复发/规则目录 | ⬜ 待开始 |
| T16–T26 | 二期生产级 | ⬜ 待启动 |

### 里程碑

- M0 能看见 (D4)：✅ 已达成
- M1 规则达标 (D14)：🟡 major 30（目标 0）
- M2 结构可信 (D20)：⚠️ P3-2/P3-3 已建但未接生产调用
- M3 复杂体接管 (D36)：⬜
- M4 工程交付级 (D44)：⬜

### 剩余工作量估算：约 15–23 天（不含二期）

---

## 3. 关键技术决策（固化）

1. **三区路由**：规则 → ANTLR（体检仪，2500× 慢）→ AI Agent（带 validate_pg）
2. **禁用文本内联**：嵌套子程序必须 lift 为独立函数
3. **SPEC 桩禁止静默**：必须 `RAISE EXCEPTION 'not implemented'`
4. **数据丢失级缺陷零容忍**：`%SUBQUERY`/`%TYPE`/静默丢失 优先级最高
5. **阶段5 复核修正**：原 P4-4c 阶段5 的 `RETURN <var>` 写法已被 live PG 证伪，**禁止复用**，正确写法是裸 `RETURN;`

---

## 4. 工作流

1. **读**：本文件 → 实施路径 → 实际代码
2. **改**：按落点纪律（见下表），小步提交
3. **验**：
   - 单测：`pytest workspace/tests/test_<task>.py`
   - 重转：`python scripts/convert_oracle_poc.py --clean`
   - 装 PG：`PGPASSWORD=postgres python scripts/install_oracle_poc.py install [--keep-going]`
   - Gate：`PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py`
   - 残留：`python scripts/scan_oracle_residuals.py oracle_poc/output`
   - **⚠️ verify 脚本会覆盖 `_verification_*` 文件**
4. **提交**：产物与源码同一 commit，message 引用 P* 编号
5. **更新本文件**：§2 状态 + §6 当周追加一行（commit 链接 + 副作用）

### 落点纪律

| 类型 | 落点 |
|---|---|
| 规则改动 | `ora2pg_py_perlstyle/perlstyle_engine.py`（唯一真实引擎）|
| 验证层 | `workspace/verification/`（唯一真源）|
| 新规则/Agent/单测 | `workspace/` |
| AST grammar | `workspace/parsers/grammar/` |
| ⛔ `pipeline/` | **冻结只读，任何情况不得修改** |

### 关键文件

| 路径 | 作用 |
|---|---|
| `ora2pg_py_perlstyle/perlstyle_engine.py` | 唯一真实转换引擎（~6625 行）|
| `scripts/convert_oracle_poc.py` | 转换入口（`--clean`）|
| `scripts/install_oracle_poc.py` | 部署入口（装 PG）|
| `scripts/verify_oracle_poc_output.py` | 验证入口（Gate 打分）|
| `scripts/scan_oracle_residuals.py` | 残留扫描器（DoD-2）|
| `oracle_poc/output/` | 当前 PG 产物（git 跟踪）|
| `oracle_poc/01.SQL/` | Oracle 源 |

---

## 5. 环境

- PG **15.19**（`C:\Program Files\PostgreSQL\15\bin`，**psql 不在 PATH**，需手动加）
- Python 3.12.6，pglast 8.4，psycopg2，antlr4 4.13.2
- 工作目录：`D:/JIP/withai_ora2pg`

---

## 6. 当周记录

> 只放 commit 链接 + 副作用，不堆详细分析（详细在 commit message）。

### 2026-10-13

| 时间 | 内容 | commit |
|---|---|---|
| 16:36 | P4-4c 阶段5 复核推翻+重写 + 阶段6/7（INOUT/CURSOR/SYS_REFCURSOR）；L3 204→225，总分 78→83 | `541f1dc` 后 |
| — | P2-5 异常与运行时属性映射体系；残留 78→45 | `584fd06` |
| — | W0 验证口径收口；weight_covered 100% | `b651980` |
| — | P4-2R END ;; 回归修复；L3 失败 92→8 | `541f1dc` |
| — | P4-2 a~h 全部完成 | `2810905` |
| — | 手册与实现对照报告 | `186be68` |
| — | docs 清理（归档旧文档）+ README 更新 | `c58b63f`~`1550c19` |

📌 **下次开工第一步**：修 §1 列出的 18 个 L3 失败 → P4-3 UTL_FILE → P4-2 续 PACKAGE batch
