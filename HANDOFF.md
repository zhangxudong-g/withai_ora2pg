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

## 1. 当前基线（2026-10-15，PG 15.19 真连实测）

| 指标 | 当前 | 目标 |
|---|---|---|
| 总分 | **84.4 / C** | ≥90 / A |
| L3 安装 | **12/12 文件全部通过**（0 失败）| 12/12 ✅ |
| L3.5 冒烟 | **33/38 + 5 fail**（待复测）| 0 fail |
| L4 业务等价 | **7/65 实际执行**（11%）| 100% |
| 残留扫描 | **major 19**（critical 0）| 0 |
| 完整性 | dropped_objects = **0** | 0 ✅ |
| 单测 | **385/385 passed** + 1 skipped | — |
| Gate | **通过（84.4/C）** | ≥90 |

> ⚠️ **DoD-6 警告**：本轮 L3 修复是**直接修改产物文件**（非引擎修复）。
> `convert_oracle_poc.py --clean` 重转会丢失所有变更。
> **下一步必须把引擎层也修好，否则重转后 L3 会回退到 18 失败**。

### 残留 19 处归因

| 归属 | 数 | 说明 |
|---|---|---|
| P4-2 续 | 12 | `-- TODO:` ×12（PACKAGE SPEC 桩，已知）|
| 列名误报 | 6 | SQLCODE/SQLERRM 是**真实列名**（非 Oracle 关键字引用）|
| 字符串误报 | 1 | `bulk collect` 在 RAISE EXCEPTION 消息中 |
| 动态 SQL | 1 | `(+)` 在动态字符串中，静态无法改写 |

> **有效残留**（排除列名/字符串误报）= 13 处，全在 P4-2 续（PACKAGE 桩）范围内。

---

## 2. 任务状态

> 权威定义在 `docs/最终实施路径_生产级_2026-10-09.md`，本节为状态视图。
> **实际执行序**：引擎层修复（DoD-6）→ P4-2 续 PACKAGE batch → P2-6 → L3.5 复测 → P5-1

| 任务 | 一句话 | 状态 |
|---|---|---|
| P1-1~P1-4 | 静默丢失/完整性/残留/冒烟 | ✅ 全部完成 |
| P2-1 ~ P2-5 | %SUBQUERY / %TYPE / (+) / 序列 / 异常映射 | ✅ 全部完成 |
| P2-6 | SYSTIMESTAMP/SYSDATE 转换 | ⬜ 待开始（0.5–1 天）|
| P3-1~P3-4 | AST/触发器升级/元数据 | ✅ 全部完成 |
| P4-1 | Agent 工具链 | ✅ 完成 |
| P4-2 a~h | TODO 桩回灌 | ✅ 完成 |
| P4-2R | END ;; 回归修复 | ✅ 完成 |
| **P4-2 续** | **PACKAGE batch（12 TODO 桩）** | **⬜ 待开始（1–2 天）** |
| P4-3 | UTL_FILE/BULK COLLECT/自治事务 | 🟡 产物层修复完成，**引擎层待修** |
| W0 | 验证口径收口 | ✅ 完成 |
| **P4-4** | **TYPE 重设计** | 🟡 P4-4a+c 阶段1~7 ✅，**产物 L3-0 修复完成** |
| **L3-0** | **L3 安装 0 失败（产物直接修复）** | ✅ **完成**（`41c58ca`）|
| **⚠️ 引擎层** | **DoD-6：引擎层同步修复（否则重转回退）** | **⬜ 待开始（最高优先）** |
| P5-1 | L4 业务等价用例库 | ⬜ 待开始 |
| P5-2 | 端到端重转 + 全门禁 | ⬜ 待开始 |
| P5-3/P5-4 | 防复发/规则目录 | ⬜ 待开始 |
| T16–T26 | 二期生产级 | ⬜ 待启动 |

### 里程碑

- M0 能看见 (D4)：✅ 已达成
- M1 规则达标 (D14)：🟡 major 19（含 6 处列名误报 + 1 处字符串误报 + 1 处动态 SQL）
- M2 结构可信 (D20)：⚠️ P3-2/P3-3 已建但未接生产调用
- M3 复杂体接管 (D36)：🟡 L3 安装 0 失败，L3.5 待复测
- M4 工程交付级 (D44)：⬜

### 剩余工作量估算：约 12–20 天（不含二期）

---

## 3. 关键技术决策（固化）

1. **三区路由**：规则 → ANTLR（体检仪，2500× 慢）→ AI Agent（带 validate_pg）
2. **禁用文本内联**：嵌套子程序必须 lift 为独立函数
3. **SPEC 桩禁止静默**：必须 `RAISE EXCEPTION 'not implemented'`
4. **数据丢失级缺陷零容忍**：`%SUBQUERY`/`%TYPE`/静默丢失 优先级最高
5. **阶段5 复核修正**：原 P4-4c 阶段5 的 `RETURN <var>` 写法已被 live PG 证伪，**禁止复用**，正确写法是裸 `RETURN;`
6. **L3-0 产物层修复**：本轮直接修改产物文件使 12/12 安装通过，但 **引擎未同步修改**。重转前必须先修引擎（DoD-6）

---

## 4. 工作流

1. **读**：本文件 → 实施路径 → 实际代码
2. **改**：按落点纪律（见下表），小步提交
3. **验**：
   - 单测：`python -m pytest workspace/tests/ -q`
   - 装 PG（清 schema）：
     ```bash
     export PGPASSWORD=postgres
     PSQL="/c/Program Files/PostgreSQL/15/bin/psql"
     $PSQL -h 127.0.0.1 -p 5432 -U postgres -d postgres -w \
       -c "DROP SCHEMA IF EXISTS oracle_poc_verify CASCADE; DROP SCHEMA IF EXISTS pkg_score_manage CASCADE; CREATE SCHEMA oracle_poc_verify;"
     # 然后逐文件或 install_oracle_poc.py install
     ```
   - **psql 连接方式**：必须用 `127.0.0.1` 不能用 `localhost`（IPv6 ::1 会超时）
   - Gate：`PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py`
   - 残留：`python scripts/scan_oracle_residuals.py oracle_poc/output`
   - **⚠️ verify 脚本会覆盖 `_verification_*` 文件**
4. **提交**：产物与源码同一 commit，message 引用任务编号
5. **更新本文件**：§2 状态 + §6 当周追加一行

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
| `scripts/install_oracle_poc.py` | 部署入口（装 PG，含 search_path 处理）|
| `scripts/verify_oracle_poc_output.py` | 验证入口（Gate 打分）|
| `scripts/scan_oracle_residuals.py` | 残留扫描器（DoD-2）|
| `oracle_poc/output/` | 当前 PG 产物（git 跟踪）|
| `oracle_poc/01.SQL/` | Oracle 源 |

---

## 5. 环境

- PG **15.19**（p15.19**
- Python 3.12.6，pglast 8.4，psycopg2，antlr4 4.13.2
- 工作目录：`D:/JIP/withai_ora2pg`

**psql 连接方式**（已验证）：
```bash
export PGPASSWORD=postgres
"/c/Program Files/PostgreSQL/15/bin/psql" -h 127.0.0.1 -p 5432 -U postgres -d postgres -w -c "SELECT 1"
```
- 必须用 `127.0.0.1`（`localhost` 解析到 IPv6 ::1 会超时）
- `-w` 配合 `PGPASSWORD` 环境变量
- 装 PACKAGE_BODY 前必须 DROP `pkg_score_manage` schema（否则返回值类型冲突）

---

## 6. 当周记录

### 2026-10-15

| 时间 | 内容 | commit |
|---|---|---|
| — | **L3-0: 18 个 L3 失败 → 0**（产物直接修复，12/12 全通过） | `41c58ca` |
| — | install 脚本 search_path 加入 pkg_score_manage | `41c58ca` |
| — | test 修复 (MagicMock 补 schema) | `e1e5669` |
| — | HANDOFF/AGENTS 精简 (1554→160行 / 190→80行) | `3465253` |

### 2026-10-13

| 时间 | 内容 | commit |
|---|---|---|
| — | P4-4c 阶段5~7（INOUT/CURSOR/SYS_REFCURSOR） | 多个 |
| — | P2-5 异常映射体系 | `584fd06` |
| — | W0 验证口径收口 | `b651980` |
| — | P4-2R / P4-2 a~h | `541f1dc` / `2810905` |

📌 **下次开工第一步**：
1. **引擎层 DoD-6 同步**（把 L3-0 的产物修复同步到 perlstyle_engine.py，否则重转回退）
2. P4-2 续：PACKAGE batch 12 个 TODO 桩
3. L3.5 复测确认冒烟状态
4. P2-6：SYSTIMESTAMP/SYSDATE 转换