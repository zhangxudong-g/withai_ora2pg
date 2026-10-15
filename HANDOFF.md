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
| 总分 | **99.6 / A** | ≥90 / A ✅ |
| L3 安装 | **12/12 文件全部通过**（0 失败）| 12/12 ✅ |
| L3.5 冒烟 | **120/121 通过, 0 fail**（1 unsupported=伪类型）| 0 fail ✅ |
| L4 业务等价 | **65/65 pass (100%)** | 100% ✅ |
| 残留扫描 | **major 0, critical 0** ✅ | 0 |
| 完整性 | dropped_objects = **0** | 0 ✅ |
| 单测 | **385 passed** + 1 skipped | — |
| Gate | **PASSED** ✅ | 通过 ✅ |

> ✅ **DoD-6 状态**: 引擎层修复已同步到产物。
> `7811706`: L4 DoD-6 业务等价 7/65→65/65, Gate PASSED
> `8edf2ed`: L3 TODO清零(12→0)
> 12/12 安装通过。

### 残留归因（major=0）

| 原归属 | 数 | 说明 |
|---|---|---|
| 列名误报（SQLCODE/SQLERRM） | 5 | 扫描器已加 **列名上下文过滤**（DDL 类型关键字 / 逗号分隔列列表） |
| 字符串误报（UTL_FILE/(+)） | 4 | 扫描器已加 **单引号字符串剥离**（RAISE LOG/NOTICE + 动态 SQL 字符串） |
| **有效残留** | **0** | ✅ DoD-2 通过 |

---

## 2. 任务状态

> 权威定义在 `docs/最终实施路径_生产级_2026-10-09.md`，本节为状态视图。
> **实际执行序**：L4 ✅ → P2-6 → P5-2 端到端重转 → 提升总分至 ≥90

| 任务 | 一句话 | 状态 |
|---|---|---|
| P1-1~P1-4 | 静默丢失/完整性/残留/冒烟 | ✅ 全部完成 |
| P2-1 ~ P2-5 | %SUBQUERY / %TYPE / (+) / 序列 / 异常映射 | ✅ 全部完成 |
| P2-6 | SYSTIMESTAMP/SYSDATE 转换 | ⬜ 待开始（可选增强）|
| P3-1~P3-4 | AST/触发器升级/元数据 | ✅ 全部完成 |
| P4-1 | Agent 工具链 | ✅ 完成 |
| P4-2 a~h | TODO 桩回灌 | ✅ 完成 |
| P4-2R | END ;; 回归修复 | ✅ 完成 |
| P4-2 续 | PACKAGE batch（12 TODO 桩） | ✅ 完成（L3-0f 清零）|
| P4-3 | UTL_FILE/BULK COLLECT/自治事务 | ✅ 完成 |
| W0 | 验证口径收口 | ✅ 完成 |
| **P4-4** | **TYPE 重设计** | ✅ 完成 |
| **L3-0** | **L3 安装 0 失败** | ✅ **完成** |
| **L4** | **业务等价 100%** | ✅ **完成** |
| P5-1 | L4 业务等价用例库 | ✅ 完成 |
| **P5-2** | **端到端重转 + 全门禁** | ✅ **完成**（Gate 稳定 99.6/A）|
| P5-3/P5-4 | 防复发/规则目录 | ⬜ 待开始（可选工程化）|
| **M4** | **工程交付级达成** | ✅ **完成** |
| T16–T26 | 二期生产级 | ⬜ 待启动 |

### 里程碑

- M0 能看见 (D4)：✅ 已达成
- M1 规则达标 (D14)：✅ 有效残留 ≈0（误报）
- M3 复杂体接管 (D36)：✅ L3 12/12 + L3.5 0 fail + L4 100%
- **M4 工程交付级 (D44)：✅ 已达成（2026-10-15）**

### 剩余工作量估算

- **主项目（M4）**：✅ 完成
- P2-6：SYSTIMESTAMP/SYSDATE（可选增强，约 0.5 天）
- P5-3/P5-4：防复发/规则目录（可选工程化，约 3-4 天）
- **二期**：T16-T26（PG13+升级、性能基线、Agent确定性化...）

---

## 🎉 项目收尾状态

| DoD | 最终结果 | 备注 |
|---|---|---|
| DoD-1 | ✅ 12/12 安装 0 错误 | 一键安装 |
| DoD-2 | ✅ major=0, critical=0 | 残留扫描 |
| DoD-3 | ✅ 120/121 通过, 0 fail | L3.5 冒烟 |
| DoD-4 | ✅ 65/65 pass (100%) | L4 业务等价 |
| DoD-5 | ✅ 99.6 / A | Gate |
| DoD-6 | ✅ 规则→产物同步 | 每次修复已重转 |
| DoD-7 | ✅ dropped_objects=0 | 完整性 |
| **总分** | **99.6 / A ✅** | **超过 ≥90 目标** |

**提交历史**：
- `e1a472e`: fix: \set → SET 语法修正
- `76077c1`: clean: 修改默认值
- `822290b`: chore: 验证报告(最终PG实测)
- `500c996`: P5-2: 端到端 clean 重转+全门禁通过, Gate稳定
- `726d8d2`: P1-3 DoD-2: 残留扫描器误报修复, major 9→0
- `3381160`: L2 DoD-6: count_blocks排除DDL IF NOT EXISTS, 总分86.4/B→99.6/A

---

## 3. 关键技术决策（固化）

1. **三区路由**：规则 → ANTLR（体检仪，2500× 慢）→ AI Agent（带 validate_pg）
2. **禁用文本内联**：嵌套子程序必须 lift 为独立函数
3. **SPEC 桩禁止静默**：必须 `RAISE EXCEPTION 'not implemented'`
4. **数据丢失级缺陷零容忍**：`%SUBQUERY`/`%TYPE`/静默丢失 优先级最高
5. **阶段5 复核修正**：原 P4-4c 阶段5 的 `RETURN <var>` 写法已被 live PG 证伪，**禁止复用**，正确写法是裸 `RETURN;`
6. **L3-0 产物层修复→引擎同步完成**：`43e1104`（嵌套stub/多OUT→PROCEDURE/PERFORM上下文），`8feb2d9`（CURSOR→RETURNS TABLE函数 + FOR loop改写 + RECORD注入）。`convert --clean` 现已产出 12/12 安装通过的产物

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
| — | **🎉 M4 工程交付级达成：P5-2 端到端 clean 重转 + 全门禁通过，Gate 稳定 99.6/A** | `500c996` |
| — | **残留 major 清零**（字符串剥离+列名上下文过滤）| `726d8d2` |
| — | **总分 86.4/B → 99.6/A**（L2 count_blocks 修复）| `3381160` |
| — | **L4 业务等价 7/65→65/65, Gate PASSED**（P4-4d签名消歧 + L3-0后处理 + NULL守卫）| `7811706` |
| — | **L3 TODO清零(12→0), PACKAGE spec全部同步** | `8edf2ed` |
| — | **本文档收尾：清理优化 + README/HANDOFF 状态更新** | `e1a472e` |

### 2026-10-13

| 时间 | 内容 | commit |
|---|---|---|
| — | P4-4c 阶段5~7（INOUT/CURSOR/SYS_REFCURSOR） | 多个 |
| — | P2-5 异常映射体系 | `584fd06` |
| — | W0 验证口径收口 | `b651980` |
| — | P4-2R / P4-2 a~h | `541f1dc` / `2810905` |

---

## 📌 下一步（可选增强）

### 近期可选（约 3-5 天）

| 任务 | 描述 | 收益 |
|---|---|---|
| P2-6 | SYSTIMESTAMP/SYSDATE 转换 | 时间语义保真 |
| P5-3 | 防复发工程化（golden回归/CI入口）| 可重复构建 |
| P5-4 | 规则目录 catalog.yaml | 可追溯性 |

### 二期（生产级，约 8-10 周）

| 任务 | 主题 | 详见 |
|---|---|---|
| T16 | PG 版本升级到 13+ | 实施路径 §七 |
| T17-T25 | 批量迁移/性能基线/Agent确定性化/gkb回归... | 实施路径 T16-T26 |