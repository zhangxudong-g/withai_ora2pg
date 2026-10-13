# HANDOFF.md — oracle_poc Oracle→PG 转换（达到可用状态）

> **当前最新工作状态**。本文件是**当前目标**的视图，不是历史日志。
> 接手任务时按此顺序读：**本文件** → **`docs/最终实施路径_生产级_2026-10-09.md`（实施唯一权威）** → 再以实际代码与测试为准。
> ~~`docs/改进任务书_达到可用状态_2026-10-09.md`~~ **已归档**，内容并入实施路径，勿据其实施。
> 详细工作流规范见 `AGENTS.md`；本目录为 `D:/JIP/poc/`。
>
> **接任务先看这三条**：
> 1. **落点**：规则改动落 `ora2pg_py_perlstyle/perlstyle_engine.py`（唯一真实引擎，实测 **6625 行 / 95 个顶层 def / 13 个 `convert_*` 入口**），
>    **新能力一律建在 `workspace/`**。**`pipeline/` 是前期代码，0 处生产引用，已冻结只读** —— 改它产物不会变，DoD-6 落空。
> 2. **完成**：P1-1 ~ P3-4，D0，P4-1，P4-2a~h，**P4-2R**（`541f1dc`），**W0**（`b651980`），
>    **P2-5**（`584fd06`），**P4-3 批次1+2**，**P4-4a**，**P4-4c 阶段 1~7**（阶段5/6/7 见下方「阶段5 复核修正」）。单测 **386/386**。
>    **下一步：P4-4c 剩余 L3 失败 18 个 → P4-3 UTL_FILE/BULK COLLECT 残留 → P4-2 续 PACKAGE batch**。
> 3. **基线**：**83.0 / C**（PG 15.19 真连实测，2026-10-13 16:36）、Gate 未通过、
>    L3 **225/243**（18 失败）、L3.5 **33/38 + 5 fail**、L4 **7/65 实际执行 + 1 fail**（覆盖率 11% 不足）、
>    残留 **critical 0 / major 30 / minor 0**、完整性 `dropped_objects=0`。
>    ✅ **本轮相对 `c219876`（78.0/C）**：总分 +5.0，L3 **204 → 225**（+21），L3.5 75 → 33/38，
>    残留 **35 → 30**，单测 310 → 386。
>    🔴 **仍阻塞**：L3 18 失败（UTL_FILE 3 / BULK COLLECT 2 / 返回值类型变更 2 / 输出参数 3 / 语法残留 8）。

>
> **⚠️ 阶段5 复核修正（2026-10-13，本轮）**：原 P4-4c 阶段5「跑了 4 处 + GET_CREDIT_INFO 改 RETURNS NUMERIC」
> **计数属实但实现是错的**，live PG 实测证伪 3 点，详见 §6。**任何人不得据旧 commit 复用阶段5 的 `RETURN <var>` 写法。**

>
> **📌 下次开工第一步**：读 §6「P4-4c 阶段5 复核修正 + 阶段6/7」，然后按 §6 列的 18 个 L3 失败清单继续修。
> **铁律：本项目所有 PG 语义结论必须 live PG 15.19 实测确认，不能凭记忆或文档转述**（本轮已 4 次被实测推翻）。

---

> ## ⚡ 人工测试入口（2026-10-12 新增）
>
> 在不修改代码/产物前提下，**人工**逐条核对 DoD-1 ~ DoD-7：
>
> 📋 **[`docs/人工测试方案_2026-10-12.md`](docs/人工测试方案_2026-10-12.md)**（人工测试权威手册）
> - §1 DoD-1 一键安装 psql
> - §2 DoD-2 残留人工抽检（95 处逐条对应 P* 任务）
> - §3 DoD-3 冒烟调用（按对象，含 5 SELF_xxx fail 重测）
> - §4 DoD-4 业务等价抽样（5 个核心函数）
> - §5 DoD-5 复跑 Gate（先备份 _verification_*）
> - §6 DoD-6 流程纪律检查（commit ↔ 产物 mtime）
> - §7 DoD-7 对象数比对（基线 ↔ 产物）
>
> **当前实测基线（2026-10-12，PG 15 真连）**：
> - 总分 **84.1 / C**，Gate 未通过
> - 残留扫描 **95 处 major**（TODO 28 + SQLCODE/SQLERRM 21 + INLINED 8 + …）
> - L3 安装 **152/176**（PACKAGE_BODY 12 + FUNCTION 7 + PROCEDURE 4 + PACKAGE 1 fail）
> - L3.5 冒烟 **37 pass / 5 fail**（全部 SELF_xxx 伪标识符，P4-4 范畴）
> - 单测 **165/165 通过**（P1~P3 全部基础设施可信）
> - 完整性门禁 `dropped_objects=0`（DoD-7 达成）
> - 工作树干净，HEAD = `d4119bd`（D0 Defect-1+2 修复）

---

## 0. 目标（DoD，对齐实施路径 §5.2）

**最终目标**：让 `oracle_poc/output/` 产物达到"一键安装 + 首调可用 + 业务等价"的可交付状态。

| DoD | 验收标准 | 度量 | 目标值 |
|---|---|---|---|
| **DoD-1** | `scripts/install_oracle_poc.py install` 在干净 PG 沙箱一键全量安装成功 | 手工跑命令 | 0 错误 |
| **DoD-2** | 产物中禁止残留：`%TYPE` / `%NOTFOUND` / `%ISOPEN` / `SQL%ROWCOUNT` / `SQLCODE` / `BULK COLLECT` / `FORALL` / `PIPE ROW` / `UTL_FILE` / `EXECUTE IMMEDIATE` / `(+)` / `CREATE SYNONYM` / `x : IS NULL; /* INLINED */` / `%SUBQUERY\d+%` / `/* TODO` / `FROM DUAL` | T1 扫描器 | major=0 |
| **DoD-3** | 冒烟执行：每个 FUNCTION/PROCEDURE/包成员/TRIGGER 至少一次真实调用不报错；视图可 SELECT；序列可 nextval | T2 冒烟层 | fail=0（业务决策项除外）|
| **DoD-4** | 业务等价：输入/输出快照对比 Oracle 语义基线与 PG 实际 | P5-1 L4 行为测试 | 100% |
| **DoD-5** | Gate 分数 ≥ A（≥90） | verification framework | ≥90 |
| **DoD-6** | **流程纪律**：任何引擎规则修复（P1/P2 阶段任务）后，**必须重新生成产物并全量复验**，禁止 commit 已修但产物未重跑 | git log 与 `_verification_report.md` mtime 对照 | 强制 |
| **DoD-7** | **产出完整性**：源中每个可识别对象必须在产物中有对应物，**对象数缺失即判 fail** | P1-1 断言 + `scripts/convert_oracle_poc.py` 转换报告 | 0 缺失 |

> **关键原则**："能安装 ≠ 可用"（plpgsql 延迟解析）。所有"安装成功"的对象必须再经 L3.5 首次调用冒烟才算通过。
> **【Rev.4 第二条】"能转换 ≠ 没丢东西"**：DoD-2 只查**残留**，查不出**缺失**。丢失的对象在产物里不存在，**四层验证全都发现不了**。

---

## 1. 当前基线 vs 目标

> **基线更新于 2026-10-12 真实 PG 15 实测（commit `d4119bd` / D0 后）**。
> P1~P3 阶段进展见 §6 当周记录；2026-10-09 旧基线仍保留在
> `docs/转换产物分析整合报告_2026-10-09.md` / `git log` 中可追溯。

| 维度 | 当前（2026-10-12 实测）| DoD 目标 | 差距来源 |
|---|---|---|---|
| 制品可使用率 | 8/13（🟢8 / 🟡3 / 🔴2）| 13/13 | PACKAGE_BODY / PACKAGE 装上但首调错；TYPE 完全错 |
| L3 安装 | **152/176**（24 失败）| 176/176 | PACKAGE_BODY 12 + FUNCTION 7 + PROCEDURE 4 + PACKAGE 1 |
| L3.5 冒烟 | **37 pass / 5 fail**（42 总）| 0 fail（业务决策项除外）| 5 fail 全部 SELF_xxx 伪标识符，同一根因 |
| Gate 分数 | **84.1 / C** | ≥90 / A | L3 失败 24 + L3.5 fail 5 + 残留扫描 95 major + L4 缺用例 |
| 残留扫描 | **95 处 major**（critical=0, minor=0）| major=0 | TODO 28 + SQLCODE/SQLERRM 21 + INLINED 8 + 复杂体 38 |
| 完整性缺口 | **0**（dropped_objects 清零）| 0 | ✅ DoD-7 达成；P1-1 + P3-4 修复后 6/6 触发器全在产物 |
| 单测 | **247/247 通过**（34.8s，2026-10-13 实测）| — | P1~P4-2 全部基础设施可信 |
| HEAD | `74f6363`（P4-2h ✅ `2810905`，2026-10-13 10:07）| — | 见下方「基线演进」 |

### 1.0 基线演进（数字口径必须对齐，勿混用）

| 时点 | 总分 | 残留 major | 单测 | 说明 |
|---|---|---|---|---|
| 2026-10-09 | 86.6 / B | 73 | — | 实施路径 §1.1 原始基线（L3 有真实数据）|
| 2026-10-12 | 84.1 / C | 95 | 165 | 人工测试口径 |
| 2026-10-13 09:40 | 79.6 / C | 74 | 247 | `--no-pg` 降级数据（L3/L3.5/L4 全跳过）|
| **2026-10-13 10:07（当前）** | **81.6 / C** | **78** | **247** | P4-2h 后；**L3/L3.5/L4 仍全部跳过** |

> ⚠️ **三处告警**：
> ① **79.6 / 84.1 / 86.6 三个数字口径不同**（PG 连通与否决定 L3 是否计入），**不可直接比较**。
> ② 残留 74→78 是 **P4-2h 的 lift 输出带进新残留**，符合"暴露真缺口不回退"，但需在 P4-2 收尾时归因。
> ③ 早期文档中的「228/228」「165/165」均为历史值，**当前实测为 247**。

### 1.0.1 残留 78 处归因（决定任务优先级，2026-10-13 实测）

| 归属任务 | 残留数 | 占比 | 明细 |
|---|---|---|---|
| **P2-5 异常体系（新增）** | **34** | **44%** | SQLCODE 10 + SQLERRM 11 + `SQL%ROWCOUNT` 3 + `%NOTFOUND` 6 + `%ISOPEN` 4 |
| P4-3 复杂体专项 | 19 | 24% | UTL_FILE 9 + BULK COLLECT 4 + FORALL 4 + PIPE ROW 2 |
| P4-2 续（PACKAGE batch）| 12 | 15% | `-- TODO:` ×12（全在 `PACKAGE/00_PACKAGE.sql`）|
| P2-6（新增）| 11 | 14% | EXECUTE IMMEDIATE 7 + FROM DUAL 4 |
| P2-3 / P2-2 残留 | 2 | 3% | `(+)` 1 + `%TYPE` 1（SYSTEM_CONFIG 无表定义）|

> **P2-5 一项即覆盖 44% 的残留**，比 P4-3 的 24% 多近一倍，且为纯规则转换（确定性、可单测、不耗 Agent 额度）。

### 1.1 已确认的"装上即坏"对象（L3 通过但 L3.5 必败，**2026-10-12 复测标记**）

| 对象 | 现象 | 对应任务 | 2026-10-12 实测验证 |
|---|---|---|---|
| `F_GET_TOTAL_SCORE(10001)` | 恒返 -1（异常回退） | P4-2 / P4-4 | ✅ 已装上、✅ 调用实际恒返 -1 |
| `F_GET_GRADE_LEVEL(95)` | 恒返 4（应为 1） | P4-2 / P4-4 | ✅ 已装上、✅ 调用实际恒返 4 |
| `PKG.FUNC_FORMAT_DATE(20261009)` | 返 `' '`（应为 '2026-10-09'）| P4-2 / P4-4 | ✅ 已装上、✅ 调用实际返 ' ' |
| `PKG.FUNC_GET_GRADE_LEVEL(95)` | 恒返 4 | P4-3 | ✅ 已装上、✅ 调用实际恒返 4 |
| `PKG.FUNC_CALC_TOTAL(10001, NULL)` | 恒返 0 | P4-2 / P4-4 | ✅ 已装上、✅ 调用实际恒返 0 |
| `F_GET_JOBNUM()` | 恒返 1（DUAL/.NEXTVAL 未转换）| P4-3 | ✅ 已装上、✅ 调用实际恒返 1 |
| `CALC_AVG(10001)` / `CALC_RANK(10001,3,1)` | 恒返 0 | P4-2 / P4-4 | ✅ 已装上、✅ 调用实际恒返 0 |
| `GET_STATUS_of_SCORE_RECORD()` | `column "self_score_record_is_pass" does not exist` | P4-4 | ⚠️ L3.5 **fail**（SELF_xxx 伪标识符）|
| `GET_LETTER_GRADE_OF_SCORE_RECORD()` | `column "self_score_record_score" does not exist` | P4-4 | ⚠️ L3.5 **fail**（SELF_xxx 伪标识符）|
| `GET_AGE_OF_STUDENT_RECORD()` | `column "self_student_record_birth_date" does not exist` | P4-4 | ⚠️ L3.5 **fail**（SELF_xxx 伪标识符）|
| `GET_GENDER_NAME_OF_STUDENT_RECORD()` | `column "self_student_record_gender" does not exist` | P4-4 | ⚠️ L3.5 **fail**（SELF_xxx 伪标识符）|
| `GET_RANK_STR_OF_STUDENT_RECORD()` | `column "self_student_record_rank_no" does not exist` | P4-4 | ⚠️ L3.5 **fail**（SELF_xxx 伪标识符）|
| `PKG.F_GET_TOTAL(10001)` | `function func_calc_total(numeric, unknown) does not exist` | P4-2 + P4-4 | ⚠️ L3 装不上（关联函数缺失）|
| `SP_PROCESS_SCORE(...)` | `invalid transaction termination` | P4-3 | ⚠️ L3 装不上（BULK COLLECT） |
| `INSERT INTO student_mst ...` | 触发器首调报 `%TYPE` 解析失败 | P2-2 / P3-4 | ✅ **已修复**（P2-2 后 `%TYPE`=0，P3-4 后 TRIGGER 14/14 装上）|
| `F_GET_WEIGHTED_AVG(...)` | 恒返 0（L3 装不上，pre-BEGIN 嵌套未 lift）| P4-2f / P4-2g | 🟡 **装不上**（函数在产物，首调 fail）|
| `F_GET_CONFIG_VALUE(...)` | 恒返 0（L3 装不上，pre-BEGIN 嵌套未 lift）| P4-2f / P4-2g | 🟡 **装不上**（函数在产物，首调 fail）|
| `F_GET_JOBNUM()` | 恒返 1（DUAL/.NEXTVAL 未转换；函数在产物）| P4-3 | ✅ **已装上**，首调返回错误值 |

> **L3.5 5 fail 全部同一根因**：`SELF_xxx` 伪标识符 → P4-4 TYPE 重设计范畴。
> 表中部分装上即坏对象（如 `CALC_AVG`、`CALC_RANK`）仅看 PASS 但调不出期望值，
> 属 L4 业务等价范畴（P5-1）。

### 1.2 可行性评估（2026-10-09 补充）

**重要**：原任务书 T1–T15 在 20 天 W1–W3 排期**不现实**，真实工时 **30–50 天**（实施路径 Final v2 已调整为 **40–60 天**）（联动 + 决策 + 隐藏债务）。完成后**仅达"工程交付级"**——生产级需追加 T16–T25 二期（PG 升级、批量迁移、性能、可观测、Agent 确定性化、真实项目回归）。详见 `docs/任务书实施难度与生产可达性评估_2026-10-09.md`。

### 1.3 任务书 Rev.2（2026-10-09 补充）

依据可行性评估，任务书已修订为 Rev.2。**主要变化**：
- **Pre-7**：Agent 额度 30–80 → **60–200** 次
- **新增 Pre-8 目标定级**：本次 = 工程交付级
- **新增 Pre-9 回归范围**：仅 oracle_poc + gkb
- **§四 4.2**：W1–W3 (20 天) → **W1–W5 (30–50 天)**，含排障预留
- **§六 风险表**：新增 3 条（T4/T9/T11 复杂度低估）
- **§七 外部资源**：新增 2 条（环境决策、回归人天）
- **T4/T9/T11 工时上调**：M–L→L、L→XL、L→XL
- **T8/T9/T12 决策项默认**：避免决策阻塞
- **新增 §十 T16–T25 生产化二期路线图**（8–10 周）

详见任务书 §九 修订记录 Rev.2。

### 1.3 任务书 Rev.3（2026-10-09 T1 开工前实测校准）

依据开工前对前置条件与 T1 全部证据行号的**逐条实测**（非文档转述），修正 3 处与实物不符 + 1 处 T1 真阻塞：

- **T1 唯一真源 = `workspace/verification/`**（**不是** `pipeline/verification/`）。
  `scripts/verify_oracle_poc_output.py:26` 注入 `REPO_ROOT/workspace`、`:29` 从 `verification` 导入。
  仓库存在**两份真实目录副本**（非符号链接）；`patterns.py`/`static_check.py` 两边哈希一致，但
  **`framework.py`/`__init__.py` 已分叉**。只改 `pipeline/` 那份 → Gate 读不到，T1 空转。
  两份副本的合并**不在 T1 范围**，留待 T15 防复发工程化。
- **Pre-3 已失效**：`antlr4-python3-runtime` **实测已装**（Python312 site-packages），
  T7 从"装依赖"降级为"验证运行时版本与生成版 `PlSqlParser.py` 匹配"，工作量下调。
- **TODO 口径**：实测 `TODO`=28 但 `/* TODO`=16 —— 另 12 处是 `-- TODO: implement` 行注释
  （全在 `00_PACKAGE.sql`）。T1 规则须同时覆盖两种形态，否则单测必失败。
- **T1 验收基线**：实测现有 `patterns.py` 得 **critical 0 / major 73 / minor 12**，
  任务书原写 "major≥8 / minor≥30" 不成立，已改为 major≥70 / minor≥10。
- **`FROM DUAL` 行号**：`00_FUNCTION.sql` 实测在 **522**（原记 307）。**单测一律按内容断言，禁止写死行号**。
- **环境**：`psql` 不在 PATH，实际在 `C:\Program Files\PostgreSQL\15\bin`（15.19）；
  PG 15 服务 Running、5432 可连；Python 3.12.6；pytest 9.0.2。
- **基线复现**：`verify_oracle_poc_output.py --no-pg` → **86.7 / B**，Gate 通过（原记 86.6，差 0.1 可忽略）。

详见任务书 §九 Rev.3。

### 1.6 Rev.4（2026-10-09 按「生产可用」目标重新校准）

对《转换产物分析整合报告》做**独立复核**（全部重测，不采信转述），得两个核心发现：

**发现一：报告与任务书都漏掉了"静默丢失"——本次最严重缺陷**

| 项 | 实测值 |
|---|---|
| 源 `01.SQL/TRIGGER/00_TRIGGER.sql` | **6 个**触发器 |
| 产物 `output/TRIGGER/00_TRIGGER.sql` | **仅 1 个**（TRG_STUDENT_BI）|
| `_conversion_report.json` 记录 | `success:true, error:"", warnings:[]` ← **报成功、零警告** |
| 字节数 | 5238 → **883**（**丢 83%**）|

- **根因**：`ora2pg_py_perlstyle/perlstyle_engine.py:1978-1981` 代码自注
  *"Convert only the first trigger block"*。`_split_triggers()`（同文件 1942 行）**已实现但从未被调用**。
- **引入**：commit `1bbc132`（"fix: TRG_STUDENT_BI 触发器 BEGIN/END 深度感知提取"）—— 只修好第 1 个，其余 5 个不处理却整体报成功。
- **为何四层验证都发现不了**：丢失的对象在产物里**根本不存在**，没有东西可报错。
- **全引擎扫描**：`only the first` 类缺陷**仅此一处**，无同类扩散。
- **新增静默丢失治理任务**（P0·最高优先·排 D1，现编号 **P1-1**）+ **DoD-7**。

**发现二：改动落点大面积指向无生产引用的 `pipeline/`**

- `scripts/` 全量扫描：**7 处 import 指向 `workspace/`，0 处指向 `pipeline/`**
- 真实引擎：`ora2pg_py_perlstyle/perlstyle_engine.py`（251KB），`scripts/convert_oracle_poc.py:33` 硬编码引入
- `workspace/converter/perlstyle_converter.py` 仅 290 行**薄包装**，转调同一函数
- `workspace/` 下**不存在** `rules/plsql_converter/parsers/stages/tests` 五目录
- **负责人决定**：`pipeline/` 冻结为只读参考，一律实现在 `workspace/`。任务书旧版 11 处 + 8 处单测路径已批量修正。

**对依据报告的复核结论**：报告**事实层面可靠**（产物 13 文件、基线 86.6/B/24 失败、L3 分项、
`%SUBQUERY`×3、`%TYPE`、`INLINED`×8、TODO×28、5/6 触发器丢失 —— 逐条实测全部吻合），
但**未识别**「静默丢失 + 假成功」这一层，且架构图隐含 `pipeline/` 为生产链路。Rev.4 已补正。

### 1.7 任务书落点对照（Rev.4 唯一真源，接手人必读）

| 任务 | **真实落点** | 旧版（已作废）|
|---|---|---|
| P1-1 静默丢失 | `ora2pg_py_perlstyle/perlstyle_engine.py:1942-1981` | — |
| T1/T2/T13 | `workspace/verification/` | ~~`pipeline/verification/`~~ |
| T3/T4/T5/T6/T8/T9 | `ora2pg_py_perlstyle/` | ~~`pipeline/rules/`、`pipeline/plsql_converter/`~~ |
| P4-1~P4-4 | 新实现落 `workspace/` | ~~`pipeline/stages/stage3_ai/`~~ |
| 单测 | `tests/` | ~~`pipeline/tests/`~~ |
| 配置 | `workspace/config/settings.py` | — |

**T8 已拆为 T8a/T8b**：T8a 修多对象转换**不依赖 T7**，可立即开工；T8b 才依赖 T7。
**T7 降级**："装 ANTLR" → "核版本 + 打通解析"，工期 1–3 天 → **0.5 天**。

详见任务书 §九 Rev.4（12 条）、§二 2.0–2.2。

### 1.8 ANTLR 实测结论（2026-10-09，决定 AST 层定位）

**实测过程与结果**：

| 检验项 | 结果 |
|---|---|
| 运行时版本 | **4.13.2 已装** |
| 与 parser 生成版本 | **精确匹配**（`PlSqlParser.py` 头部：`Generated by ANTLR 4.13.2`）|
| import | ✅ `PlSqlLexer` + `PlSqlParser` 均可加载 |
| TRIGGER 解析 | 6 语句，**0 错误**，**6/6 对象名提取正确** |
| FUNCTION 解析 | 31 节点，**0 错误** |
| PROCEDURE 解析 | 16 节点，**0 错误** |
| PACKAGE_BODY 解析 | 4389 节点，**8 错误** |

**8 个错误的根因**：`PACKAGE_BODY:161` 嵌套函数**缺 `DECLARE` 关键字**（Oracle 允许嵌套函数直接跟在 `BEGIN` 后，
ANTLR 社区 grammar 要求 `declareBlock`）—— **同一根因连锁 8 条**，非分散缺陷。

**决定性代价**：

| 场景 | 耗时 |
|---|---|
| 规则引擎转一个触发器文件 | **4.7 ms** |
| ANTLR 解析同一文件 | **11,700 ms** |
| 倍率 | **约 2500×** |

**架构决策**：**ANTLR 做"看医生"，不做"动手术"** ——
不进转换主路径（否则 2500 倍减速不可接受），只做**独立结构体检器**。
它能看见正则看不见的东西：对象数（数 `Unit_statementContext` 节点）、嵌套深度、
`%TYPE` 引用哪张表哪列、函数是否被替换成空壳桩。

**grammar 副本已就位**：`pipeline/parsers/grammar/` 的 6 个生成文件（共 **8,754,421 B**）
已于 2026-10-09 **字节级复制**到 `workspace/parsers/grammar/`，SHA-256 全部一致，
独立解析验证通过（TRIGGER 6 unit / 0 err）。**允许冗余** —— 使 workspace 自包含、
不依赖已冻结目录。两份并存时**以 workspace 那份为准**。
**约束与已知缺口见 `workspace/parsers/grammar/README.md`**：
① 禁止手工编辑生成物 ② runtime 必须严格 4.13.2 ③ **`.g4` 源不在仓库，无法重新生成**（T15 需处理）
④ PACKAGE_BODY 嵌套函数缺 `DECLARE` → 8 处连锁错误（同一根因）。

详见 `docs/最终实施路径_生产级_2026-10-09.md` §二、§3.3。

### 1.9 另一项实测发现：T10 的 lift 机制已存在

`_lift_nested_subprograms`（`perlstyle_engine.py:3466`，70+ 行）**已实现且已在主路径调用**
（`L3448`）。但产物中仍有 INLINED 残骸，说明**覆盖不全**而非"待实现"。
T10 范围需从"实现 lift"重估为"补齐 lift 覆盖 + 修残留"。

### 1.4 前置条件实测状态（2026-10-09）

| Pre | 状态 | 说明 |
|---|---|---|
| Pre-1 Python/PG | ⚠️ 半通过 | Python 3.12.6 ✅、PG 15 Running ✅；**psql 不在 PATH**，需手动加 bin 路径 |
| Pre-2 依赖 | ✅ | pglast 8.4 / psycopg2 / pyyaml 全部可用 |
| Pre-3 ANTLR | ✅ 已装（失效项）| 见 1.3，利好 T7 |
| Pre-4 可写 | ✅ | — |
| Pre-5 基线 | ✅ | 86.7/B 可复现 |
| Pre-6/7/8/9 | ⏳ 非 T1 阻塞 | 分别阻塞 T6/T8/T12、T11、全部、T15 |

### 1.5 关键缺陷证据（grep 实证）

| 缺陷类 | 位置 |
|---|---|
| `%TYPE` 残留 | TRIGGER:2 / FUNCTION:474 / PACKAGE_BODY:100 (共 5) |
| `%SUBQUERY%d%` 未回填（数据丢失） | PROCEDURE:203/284/287 (共 3) |
| `INLINED` 残骸 `x : IS NULL; /* INLINED */` | PACKAGE_BODY:119/308/515; FUNCTION:94/170 (共 8) |
| 孤儿 `END` | PACKAGE_BODY:128/139; FUNCTION:201 |
| TODO 桩 | 28 = PACKAGE 12 + PACKAGE_BODY 8 + FUNCTION 8（其中 `/* */` 16 + `--` 12）|
| BULK COLLECT/PIPE ROW/UTL_FILE/SQLCODE | 多个文件，详见分析报告 |
| `BEGIN set_config(...)` 拼接 | PACKAGE_BODY:79/568/666 |
| `INOUT o_nVALUE + RETURNS NUMERIC` | PACKAGE_BODY:89/129 |
| `FROM DUAL` | FUNCTION:**522**（Rev.3 修正，原 307）|
| `(+)` 改写错（V_STUDENT_SCORE） | VIEW:40-41 `LEFT JOIN TEACHER_MST T / ON TRUE` 笛卡尔积 |
| SELF_xxx 伪标识符 | TYPE:39/53/55-59 等多处 |
| 5/6 触发器静默丢失 | TRG_STUDENT_AU、TRG_SCORE_BIU、TRG_SYSTEM_CONFIG_BD、TRG_SCORE_COMPOUND、TRG_V_STUDENT_IO |

---

## 2. 任务状态（权威在 `docs/最终实施路径_生产级_2026-10-09.md`，本节为其视图）

> ⚠️ 实施路径已于 **2026-10-13 追加 Final v3 修订记录**：新增 **W0 / P2-5 / P2-6 / P5-4 / T26**，
> **P4-3 扩范围**，**执行顺序调整**（见本文 §2.2.1），并勘误 3 处事实错误。
> **本节与实施路径 Final v3 保持同步**；冲突时以实施路径为准。

**文档分工**（冲突时优先级：**实施路径 > 分析报告**）：

| 文档 | 定位 |
|---|---|
| **`docs/最终实施路径_生产级_2026-10-09.md`（Final v2）** | **实施唯一权威**：路径 + 架构 + 落点 + 任务 P1-1~P5-3 + 二期 T16–T25 + DoD |
| `docs/转换产物分析整合报告_2026-10-09.md` | 缺陷分析基底（已独立复核；**未识别静默丢失层**）|
| `docs/改进任务书_达到可用状态_2026-10-09.md` | ⛔ **已归档**（内容并入实施路径，保留 Rev.1–5 演进供追溯）|
| `docs/archive/` | 历史归档 | 仅追溯，勿据其实施 |
| `workspace/CONVERSION_OUTPUT_ANALYSIS.md` | 逐文件实测（仅分析未改代码）|

> **任务编号体系**：实施路径统一为 **P1-1 ~ P5-3**（阶段一~五）+ **T16–T25**（二期）。
> **Final v3 新增**：**W0**（前置）、**P2-5**、**P2-6**、**P5-4**、**T26**。
> 旧任务书的 T0–T15 编号已作废，勿混用。

### 2.1 任务清单（P1-1 ~ P5-3 + W0 + T26，Final v3 同步）

| 任务 | 优先级 | 主题 | 状态 |
|---|---|---|---|
| **P1-1** | **P0·最高** | **静默丢失根治 + 对象计数断言** | ✅ **已完成（commit `b441bb6`，2026-10-09）** |
| P1-2 | P0 | 完整性门禁（`dropped_objects`）| ✅ **已完成（commit `9db1881`，2026-10-09）** |
| P1-3 | P0 | 残留扫描器（覆盖 `/* TODO` 16 + `-- TODO` 12）| ✅ **已完成（commit `83607a2`，2026-10-09）** |
| P1-4 | P0 | 首调冒烟层 | ✅ **已完成（commit `dda2435`，2026-10-09）** |
| P2-1 | P0 | `%SUBQUERY` 回填兜底 | ✅ **已完成（commit `41ca8da`，2026-10-09）** |
| P2-2 | P0 | `%TYPE` 查表展开 | ✅ **已完成（commit `64031ab`，2026-10-09）** |
| P2-3 | P0·重 | `(+)` 外连接结构化改写（3–5 天）| ✅ **已完成（commit `e8465d8`，2026-10-09）** |
| P2-4 | P1 | 序列/同义话语义治理 | ✅ **已完成（commit `9fa0fef`，2026-10-09）** |
| P3-1 | P1 | AST 适配器（包装 workspace 副本）| ✅ **已完成（commit `a73f2b4`，2026-10-09）** |
| P3-2 | P1 | 结构体检器 | ✅ **已完成（commit `1dcfb01`，2026-10-09）** |
| P3-3 | P1 | 元数据提取 | ✅ **已完成（2026-10-09，24 单测全绿）** |
| P3-4 | P1 | 触发器转换升级（结构化生成）| ✅ **已完成（2026-10-09，复合触发器拆分 + PG 实装验证通过）** |
| P4-1 | P1 | Agent 工具链 | ✅ **已完成（commit `f0f42af` + `9df25b5`，2026-10-12）** |
| **D0** | **P0** | **Defect-1 + Defect-2 修复（产物安装层）** | ✅ **已完成（commit 待提交）** |
| **P4-2** | P1·关键路径 | TODO 桩回灌 + Bug 根治 | ✅ **完成**（`2810905`，2026-10-13）：P4-2a~h 全部完成。FUNCTION L1 critical 16→0；PACKAGE_BODY L1 critical 26→2；总分 79.6→81.6。**剩余：PACKAGE batch 未做**（`00_PACKAGE.sql` 残留 `-- TODO:` 12 处），归入"P4-2 续" |
| **P4-2R** | **P0·紧急** | **修 lift 路径 `END ;;` 回归**（多输出一个分号 → PG 语法错误）| ✅ **已完成（commit `541f1dc`，2026-10-13）—— L3 失败 92 → 8** |
| **W0** | **P0·最高** | **验证口径收口**（单一验收入口 + Gate 补结构判定 + `weight_covered` + 完整性 diff 移到 post-pass 后 + 接 golden cases）| ✅ **已完成（commit `b651980`，2026-10-13）—— 5 子项一次实施；`weight_covered` 现 100%** |
| **P2-5** | **P0·最高** | **异常与运行时属性映射体系**（`WHEN OTHERS`×112 / SQLCODE×12 / SQLERRM×13 / `SQL%ROWCOUNT`×3 / `%NOTFOUND`×6 / `%ISOPEN`×4）| ✅ **已完成（commit `584fd06`，2026-10-13）—— 残留 78→45，消 33（42%）；单测 294** |
| **P4-3** | P1 | 复杂体专项 —— **已扩范围**：原 BULK COLLECT/PIPE ROW/UTL_FILE/自治事务 **+ `INDEX BY` / `EXECUTE IMMEDIATE` / `FROM DUAL` / `FORALL` 集合化** | 🟡 **进行中**：批次1 + 批次2（BULK COLLECT 多变量桩 + FORALL → FOR）✅。**剩余：UTL_FILE 9 / BULK COLLECT 2 / 自治事务（函数内 COMMIT）** |
| **P4-2 续** | P1 | PACKAGE batch（残留 TODO 12 处）| ⬜ 待开始（1–2 天）|
| **P4-4** | **P1·硬阻塞** | TYPE 重设计（5–8 天）| 🟡 **进行中**：P4-4a ✅（TYPE SELF 引用改写）+ **P4-4c 阶段1~7** ✅（L3 204→225，总分 78.0→83.0）。**剩余 L3 18 失败**（UTL_FILE 3 / BULK COLLECT 2 / 返回值类型 2 / 输出参数 4 / 语法残留 7），详见 §6 ⑦ |
| **P2-6** | P1 | 时间语义保真（`SYSTIMESTAMP`→`clock_timestamp()`，源中 17 处）| ⬜ 待开始（0.5–1 天）|
| **P5-1** | P2 | L4 业务等价用例库 | ⬜ 待开始 —— **缩减为「接线 + 定级」**（65 条用例已存在，资产在 `pipeline/verification/golden/`，需迁到 `workspace/`）。**期望值可信度待发起人决策（见 D4）** |
| **P5-2** | P2 | 端到端重转 + 全门禁 | ⬜ 待开始（W0 已铺大部分）|
| **P5-3** | P3 | 防复发工程化 + grammar 可维护性 | ⬜ 待开始 —— ⚠️ grammar 有 **3 份活动副本**，守卫建议提前 |
| **P5-4** | P3 | **规则目录 `catalog.yaml` + `feature_detector`**（手册 §19.4/§25.1/§25.4）| ⬜ 待开始（3–4 天，新增）|
| T16–T25 | 二期 | **生产级路线图**（8–10 周）| ⬜ 待启动（M4 达成后）|
| **T26** | 二期 | **DBMS_SQL 专项**（手册 §11.2；GKB 基线已识别该特征）| ⬜ **已立项，建议排在 T21 之前独立排期，不与 P1–P5 混排**；oracle_poc 源命中 0，不阻塞当前交付 |

### 2.1.1 ✅ W0 已闭环（原「为什么是强制前置」，2026-10-13 更新）

> **状态：W0 已于 `b651980` 完成，下列三条盲区全部修复。** 本节保留作为决策溯源，**不再是当前事实**。
> 修复后实测：Gate 读 `structure_issues`（`framework.py:567`）、单入口 `scripts/verify_oracle_poc.py` 已建、
> `weight_covered` 已上报且当前 100%、L4 加 `execution_ratio` 门槛（`framework.py:484`）。
> **仍存软肋**：跳过层在 `_gate_failures` 中仍 `continue`（`framework.py:551-552`），
> `--no-pg` 仍能产出「Gate 通过」报告。修复方案见 `docs/迁移能力边界评估与全量迁移任务清单_2026-10-13.md` N6。

以下三条是 W0 立项时的实测证据（历史，**已修复**）：

1. ~~**Gate 存在已证实的盲区**~~ ✅ 已修：`_gate_failures` 现读 `structure_issues`
2. **DoD-2 / DoD-7 两个硬门禁不在 Gate 路径内**——`residuals.py` 与 `integrity.py` 都是独立 CLI，Gate 完全不感知。
3. **分数口径不完整**：当前 81.6 是在 L3/L3.5/L4 **全部跳过**（65% 权重缺席）时按比例归一化算出的；
   `_gate_failures` 对 skipped 层直接 `continue`，导致 **`gate_require_cases` / `min_case_coverage` 永不触发**。

> 对应实施路径 §〇 第一原则：**"先建立能看见问题的能力，再修问题本身。"**
> 没有 W0，后续每批任务完成都无法证明"真的修好了"。

### 2.2 实施顺序（实施路径 §四，40–60 天）

- **D1–D4（阶段一）**：P1-1 静默丢失根治 → P1-2 完整性门禁 → P1-3 残留扫描器 → P1-4 首调冒烟
- **D5–D14（阶段二）**：P2-1 `%SUBQUERY` / P2-2 `%TYPE` / P2-3 `(+)` JOIN / P2-4 序列同义词
- **D15–D20（阶段三）**：P3-1 适配器 → P3-2 体检器 → P3-3 元数据 → P3-4 触发器升级
- **D21–D36（阶段四）**：P4-1 Agent 工具链 → P4-2 TODO 回灌 → P4-3 专项件 → P4-4 TYPE 重设计
- **D0（产物安装层前置）**：D0 Defect-1+2（已完成）→ P4-1 阶段四起点
- **D37–D44（阶段五）**：P5-1 L4 用例 → P5-2 端到端 → P5-3 防复发

### 2.2.1 ⚠️ 实际执行序（**Final v3 调整，已取代上面的阶段顺序**）

> 原阶段顺序是"按阶段推进"，但实测发现**能力建设跑在验证接线前面**，且 P2-5 的残留杠杆远高于 P4-4。
> 以下为经实测重排后的实际执行序，**下次开工按此走**：

```
W0 (1.5d) ──→ P2-5 (2-3d) ──→ P4-3 (5-7d) ──→ P4-2续 PACKAGE batch (1-2d)
   ↑              ↑                                          │
   │              └── 消 34/78 残留（44%）                     ↓
   └── 强制前置（Gate 有盲区，见 §2.1.1）          P4-4 (5-8d) 【硬阻塞】
                                                              ↓
                                    P5-1 ──→ P5-2 ──→ P5-3 / P5-4

并行（不阻塞 oracle_poc）：T26 DBMS_SQL（二期，排在 T21 之前）
可与 P2-5 合并做：P2-6 时间语义（0.5-1d）
```

**剩余工作量估算：约 19–27 天**（不含 T26 二期）。

**三条排序理由**：

1. **W0 强制前置**——Gate 现在会放过结构崩掉的文件，且 65% 权重缺席。没有 W0，后面每批做完都无法验收。
2. **P2-5 插到 P4-4 之前**——2–3 天消 44% 残留（纯规则、可单测、不耗 Agent 额度），
   投入产出比高于 P4-4 的 5–8 天语义设计。
3. **P5-1 必须排在 P4-4 之后**——当前 `F_GET_TOTAL_SCORE`→-1、`F_GET_GRADE_LEVEL(95)`→4（应为 1）、
   `FUNC_FORMAT_DATE`→`' '`，用例建在恒返错值的函数上会返工。

### 2.3 里程碑（实施路径 §5.1）

- **M0 能看见 (D4)**：转换不再"假成功"；触发器 6/6；完整性门禁生效 —— ✅ **已达成**
- **M1 规则达标 (D14)**：残留 major 归零；VIEW 7/7；Gate ≥ 80 —— 🟡 当前 major 78 / 81.6
- **M2 结构可信 (D20)**：AST 体检全通过 —— ⚠️ **P3-2/P3-3 已建但无生产调用方，AST 体检层实际从不运行**
- **M3 复杂体接管 (D36)**：TODO ≤3；L3 失败 0；Gate ≥ 90 —— ⬜
- **M4 工程交付级 (D44)**：L4 100%；五道门禁全绿；CI 可回归 —— ⬜
- **M5（二期）**：T16–T25 + T26 启动后重新定里程碑（实施路径 §七）

---

## 3. 关键技术决策（已固化，不允许擅自改动）

来自 `docs/转换产物分析整合报告_2026-10-09.md` §五 根因诊断：

1. **三区路由**：复杂度低走规则（快、稳）；含 AST 可解析的语义结构走 ANTLR；规则/解析搞不定的复杂体走 AI Agent（带 `validate_pg` 硬校验 + 重试回灌）。
2. **禁用文本内联**：嵌套子程序必须走 `package_body_converter.lift_nested_subprograms` 提升为 schema 级独立函数，不再用"小函数体内联 + 大函数体丢 TODO"的旧策略（T10）。
3. **plpgsql 延迟解析**导致 L3 不可信——所有 PL/SQL 对象必须经 L3.5 冒烟才算通过（DoD-3）。
4. **SPEC 桩遮蔽 BODY 失败的防护**：当 BODY 未全覆盖 SPEC 的成员时，把桩改为 `RAISE EXCEPTION 'not implemented'`，避免"静默返回 NULL"（任务书 §六风险 3）。
5. **流程纪律（DoD-6）**：任何引擎规则修复后**必须重转产物**——commit 49085b5 声称修 V_STUDENT_SCORE 但产物未重跑是反例。
6. **【Rev.4】产出完整性优先于一切（DoD-7）**：能查残留、查不出缺失。当前"源 6 触发器 → 产物 1 个、
   转换报告记 success"是实证反例。**任何"修好了"的结论，必须先确认没有被静默丢弃的东西**（P1-1）。
7. **【Rev.4】落点纪律**：改动必须落在 `ora2pg_py_perlstyle/` 或 `workspace/`；`pipeline/` 是前期代码、
   **无任何生产代码引用**，改它产物不会变 —— 这是 DoD-6 落空的直接原因。

---

## 4. 工作流（接任务时执行）

1. **先读**（**顺序不可颠倒**）：
   - 本文件 `HANDOFF.md`（当前状态/目标/执行序）
   - **`docs/最终实施路径_生产级_2026-10-09.md`**（实施唯一权威，**Final v3 修订记录含最新任务编号**）
   - `docs/整合任务计划_2026-10-13.md`（实际执行序 + 批次 + 决策点）
   - `AGENTS.md`（工作规范）
   - ~~`docs/改进任务书_达到可用状态_2026-10-09.md`~~ ⛔ **已归档，不要据此实施**
2. **确认前置**（实施路径 §1.5 Pre-1 到 Pre-9）：PG 实例、依赖、ANTLR 状态、当前产物基线可复现。
   **当前基线见 §1.0（2026-10-13）**。注意 `psql` 不在 PATH，执行 DoD-1 命令前需先加
   `C:\Program Files\PostgreSQL\15\bin`；`antlr4` 已装（Pre-3 失效）；**目标即 PG 15.19**（实施路径 §六"PG 8.4"为勘误，见 Final v3 v3.4）。
   **提交前先 `git status`**——本仓库存在并行作业（2026-10-13 曾发生误 sweep）。
3. **认领任务**：在 `docs/最终实施路径_生产级_2026-10-09.md` 与本文件 §2.1 状态栏把 ⬜ 改为 🟡 进行中；完成时改为 ✅ 并写 commit。
4. **实施**：按实施路径 P* 的"落点 / 实施步骤 / 验收"执行；**实际顺序以本文件 §2.2.1 为准**（已偏离阶段顺序，见 Final v3 v3.3）。
   **落点纪律（Rev.4，接手人务必遵守）**：
   - 规则改动 → `ora2pg_py_perlstyle/perlstyle_engine.py`（**唯一真实引擎**）
   - 验证层 → `workspace/verification/`（**唯一真源**）
   - 新规则 → **`workspace/rules/`**（注意：不是实施路径 §3.1 写的 `workspace/converter/rules/`，见 Final v3 v3.4-E2）
   - 新代码 → `workspace/`
   - **`pipeline/` 冻结，不改、不迁移、不删**
5. **验证**：
   - 单测：`pytest workspace/tests/test_<task>.py`（**注意：不在 `pipeline/tests/`**）
   - 三个脚本职责明确、不重叠——按需选用：
     - 改了转换规则 → `python scripts/convert_oracle_poc.py --clean`（重转产物）
     - 装到真 PG 验收 → `PGPASSWORD=postgres python scripts/install_oracle_poc.py install [--keep-going]`
     - 跑 Gate 报告 → `PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py`
   - 残留扫描（DoD-2）：`python scripts/scan_oracle_residuals.py oracle_poc/output`
   - **⚠️ 副作用警告**：`verify_oracle_poc_output.py` 会**覆盖** `oracle_poc/output/_verification_report.md`
     与 `_verification_results.json`。跑 `--no-pg` 更会用降级数据覆盖含 L3 真实数据的基线报告，
     **且在 Windows 中文环境下报告生成器会写出 GBK 乱码**。跑之前先备份或事后 `git checkout` 还原。
   - DoD-6：commit 后必须重转产物（`scripts/convert_oracle_poc.py --clean`）+ 装 PG（`install_oracle_poc.py install`）验证
   - DoD-7：P1-1 完成后还要断言**产物对象数 ≥ 源对象数**（`scripts/convert_oracle_poc.py` 自动用 `dropped_objects` 门禁）
6. **更新 HANDOFF**：把任务状态、commit 链接、发现的副作用写到本文件 §6 当周记录。

---

## 5. 关键文件速查

### 5.1 产物与基线

| 路径 | 作用 |
|---|---|
| `oracle_poc/01.SQL/` | Oracle 源 |
| `oracle_poc/output/` | 当前 PG 转换产物（基线 141/164） |
| `oracle_poc/output/_verification_report.md` | L1/L2/L3 验证报告 |
| `oracle_poc/output/00_CREATE_ALL_PG.sql` | psql 一键安装脚本（DoD-1 验证对象）|

### 5.2 转换/验证/入口

| 路径 | 作用 |
|---|---|
| `ora2pg_py_perlstyle/perlstyle_engine.py` | **唯一真实转换引擎**（实测 251KB）。**P1-1/P2-1~P2-4/P3-4 的改动落点**；`:1942-1981` 是 T0 的根因现场 |
| `ora2pg_py_perlstyle/plsql_subs.py` | 占位符替换子句（T3 的 `SUBQUERY_RESTORE` 落点）|
| `scripts/convert_oracle_poc.py` | 转换入口（`--clean`；Oracle 源 → PG 产物；`:33` 硬编码引入引擎） |
| `scripts/install_oracle_poc.py` | 部署入口（per-file 装 PG，默认错停；`--keep-going` 收集全错；`drop` 重装） |
| `scripts/scan_oracle_residuals.py` | 残留扫描器（DoD-2 门禁，只读；`--json` 报告） |
| `scripts/verify_oracle_poc_output.py` | 验证入口（复用 `workspace/verification/`，跑 L1/L2/L3/L3.5 + 打 Gate 分） |

### 5.3 任务书将要用到的资产

| 路径 | 任务书用途 |
|---|---|
| `pipeline/` | Stage 0–5 + R1–R5 完整流水线；T7/T8/T9 用 ANTLR，T10/T11/T12 用 Agent |
| `pipeline/parsers/grammar/PlSqlParser.py` | ANTLR 生成的 Python 解析器（T7 依赖） |
| `pipeline/rules/outer_join.py` | T4 起点（当前仅标记规则，需扩展为转换） |
| `pipeline/rules/rowtype.py` | T5 起点（已有 schema_resolver 钩子） |
| `pipeline/plsql_converter/package_body_converter.py` | T10 提升嵌套子程序 |
| `pipeline/stages/stage3_ai/` | T11 Agent 编排（agent/chunk_planner/tools/convert_chunk/validate_pg/fallback） |
| `workspace/verification/static_check.py` | **T1 唯一真源**（`verify_oracle_poc_output.py:29` 实际导入的就是它）|
| `workspace/verification/patterns.py` | T1 复用 `RULE_GROUPS`；与 `pipeline/` 那份哈希一致 |
| `pipeline/verification/` | ⚠️ **同名副本，Gate 不读它**。改前先确认改的是哪份（Rev.3）|
| `workspace/verification/pg_sandbox.py` | T2 冒烟层扩展点（同属真源副本）|
| `workspace/verification/behavior_check.py` | L4 业务等价扩展点（P5-1）|

### 5.4 历史归档（不再维护，仅追溯）

- `docs/archive/2026-10-09_reports/` — v1/v2/v3/v4/v5 五份历史分析报告
- `docs/CONVERSION_ISSUES_ANALYSIS.md` — 旧一轮快照（57 失败/88.6，已过期）
- 各 session 的零散 commit log 见 `git log`

---

## 6. 当周记录（实施阶段在此追加）

> 任务书任务开始实施后，把每个 T* 的 commit、副作用、发现写到此处，**保持精简**。
> 详细分析在 commit message 与代码注释里，**不堆在本文件**。

### 2026-10-13 16:36（P4-4c 阶段5 **复核推翻并重写** + 阶段6/7 新增 + PG 端到端验证）

**性质**：起手是「核验一段状态声明是否准确 + 继续 PG 端到端验证」。**核验结果：计数属实，实现是错的**。

#### ① 核验结论：阶段5 原实现的 3 处硬错误（全部 live PG 15.19 实测证伪）

原声明「阶段5 跑了 4 处 + GET_CREDIT_INFO 改 RETURNS NUMERIC。P4-4 推进完成 5 个阶段」——
**计数属实**（4 处 RETURNS 改型 + `GET_CREDIT_INFO` 确实改成了 `RETURNS NUMERIC`；
提交史也确实是 P4-4a + 阶段1~4 共 5 个 commit）。**但阶段5 的实现是错的，且引入 6 处新缺陷**：

| # | 原实现 | live PG 15.19 实测 | 判定 |
|---|---|---|---|
| 1 | 前提「`INOUT` + `RETURNS BOOLEAN` 非法」 | ✅ 属实：`错误: 因为OUT参数，函数的结果类型必须是numeric` | 前提对 |
| 2 | 把 `RETURN TRUE` → `RETURN o_nCREDIT` | ❌ **方向反了**：`错误: 在带有输出参数的函数中RETURN不能有参数`。正确是**裸 `RETURN;`**。**改完仍装不上** | 实现错 |
| 3 | 用 `re.sub` 对**整份文件**全局替换 `RETURN TRUE/FALSE` | ❌ 把「最后一个被修复函数的 INOUT 变量名」灌注到同文件**其他**函数 | **新造 6 处作用域外引用** |

**第 3 项造出的 6 处作用域外引用**（首调必炸，`column "o_npass" does not exist`）：
`INIT_WSINFO` / `VALIDATE_SCORE_VALUE` / `PARSE_YMD` / `VALIDATE_YMD` / `GET_SUBJECT_INFO` / `WRITE_HEADER`，
外加 FUNCTION 文件的 `VALIDATE_STUDENT_NO`。**这 7 处全部是阶段5 v1 引入的，不是历史遗留。**

另：原 INOUT 正则 `INOUT\s+(\w+)\s+(\w+)` **漏掉「名在前、INOUT 在后」写法**，
导致 `VALIDATE_SCORE_VALUE(i_nVALUE INOUT NUMERIC, …)` 和 3 个 `SYS_REFCURSOR` 参数没被识别 —— 只修了 8 个中的 4 个。

#### ② 阶段5 重写（`workspace/rules/p44c_inout.py`）

- 改为**逐函数、按函数体区间**处理，修复范围不越界（治第 3 项）
- 兼容 `INOUT x T` 与 `x INOUT T` 两种参数写法（治漏检）
- **改 `RETURNS` 类型 + 把函数体内 `RETURN <表达式>` 改成裸 `RETURN;`**（治第 2 项）
- 产出 `scan_inout_fixes()` 供转换脚本打印真实计数（替换原先那个**恒返回 0 的死正则**计数器）
- 实测：**RETURNS 改型 4 → 8 处**，裸 RETURN 8 处

#### ③ 阶段6 新增：动态游标 `OPEN c FOR` 补 `EXECUTE`

live 实测 3 种形态全部 CREATE 失败（`语法错误`），加 `EXECUTE` 后 CREATE 成功**且首调返回 3 行**：

| 形态 | 原写法 | 修法 |
|---|---|---|
| 函数调用 | ❌ `语法错误 在 "build_query" 或附近的` | `FOR EXECUTE build_query(...)` |
| 变量 | ❌ `语法错误 在 "v_sql" 或附近的` | `FOR EXECUTE v_sql` |
| 字面量 | ❌ `语法错误 在 "'SELECT...'" 或附近的` | `FOR EXECUTE 'SELECT ...'` |
| 真查询 | ✅ 正常 | 不动 |

共修 **5 处**。

#### ④ 阶段7 新增：PL/pgSQL 游标声明两形态

live 实测：
- **词序反了** —— Oracle/产物 `CURSOR c1 FOR SELECT…` → `无效的类型名字 "c1 FOR SELECT …"`（PG 把它当类型名+变量名）。
  PG 正确语法是 **`c1 CURSOR FOR query`（名字在前）**。共修 **3 处**。
- **`R_STUDENT CUR_STUDENT;`** → `类型 "CUR_STUDENT" 不存在`。PL/pgSQL **没有"游标行类型"**，
  `FETCH c INTO R` 要的是 **record**。改为 `R_STUDENT RECORD;`（首调正确取到 3 行）。共修 **1 处**。

#### ⑤ 引擎修复（`ora2pg_py_perlstyle/perlstyle_engine.py`，`_convert_args`）

`_map_return_type` 早有 `SYS_REFCURSOR → REFCURSOR`，但**参数侧漏了** → lifted 函数参数保留
`INOUT i_cursor SYS_REFCURSOR` → PG `错误: 类型 sys_refcursor 不存在`（L3 阻塞，历史遗留）。
补 2 行正则（`SYS_REFCURSOR` / `REF CURSOR`）→ 产物残留 **SYS_REFCURSOR 2 → 0**。

#### ⑥ 实测基线（PG 15.19 真连，DoD-6）

| 指标 | `c219876` | 本轮 | 变化 |
|---|---|---|---|
| 总分 | 78.0 / C | **83.0 / C** | **+5.0** |
| L3 安装 | 204/243 | **225/243** | **+21** |
| L3.5 首调 | 75/91 + 16 fail | **33/38 + 5 fail** | 总量降、总失败 5（目标 0）|
| 残留 major | 35 | **30** | −5 |
| 单测 | 310 | **386** | +76 |
| Gate | 未通过 | 未通过 | — |

**新增单测 3 文件 / 76 用例**，含 v1 跨函数污染回归守门测试 `test_multi_function_no_cross_contamination`。

#### ⑦ 剩余 L3 18 失败（下一步清单，已按可动手顺序归类）

| 类 | 数 | 明细 | 归属 |
|---|---|---|---|
| **UTL_FILE 模式不存在** | 3 | `PROC_EXPORT_CSV` / `WRITE_HEADER` / `WRITELN` | P4-3 UTL_FILE 专项 |
| **BULK COLLECT 语法残留** | 2 | `PROC_BATCH_RANK_UPDATE` / `SP_BATCH_DELETE_SCORE` | P4-3 批次 2 续 |
| **返回值类型不可变** | 2 | `F_GET_SCORE_LIST` ×2 | 阶段2 `RETURNS TABLE` 改型后与旧对象冲突 |
| **输出参数相关** | 3 | `FUNC_GET_CONFIG`(`RETURN不能有参数`) / `PARSE_YMD`(`o_nm`未知) / `GET_SUBJECT_INFO`(`o_ncredit`未知) / `CALC_PASS_FAIL`(`o_nfail`未知) | **Oracle 原函数有 3~4 个 OUT，阶段2 只补了 1 个** |
| **语法残留** | 8 | `INIT_JOBNUM` / `PROC_LOAD_CONFIG`(`set_config`) / `GENERATE_ROWNO`(`%`) / `CALC_STATS` / `PROC_BATCH_SCORE_PROCESS` / `P_UPDATE_RANK` / `SP_BATCH_IMPORT_SCORE`(`EXCEPTION` 嵌在 LOOP 内) | 逐个修 |

> ⚠️ **`SP_BATCH_IMPORT_SCORE` 的 `EXCEPTION` 嵌在 `LOOP` 内**是结构问题（plpgsql 的 EXCEPTION 只能挂在块级，
> 不能挂在 LOOP 里），**不是正则能修的**，需按 Oracle 源码结构重排。

#### L3.5 剩余 5 fail

`calc_weighted_sum` / `calc_total_weight`（游标变量为空）/ `sp_process_score`（NOT NULL 业务数据）
/ `sp_gen_score_report` / `sp_calc_rank`（`子事务处于活动状态时无法提交` —— Oracle 自治事务 + `COMMIT`，
PG **函数内不允许 COMMIT**，属 P4-3 自治事务专项）。

#### 副作用

- `_verification_report.md` / `_verification_results.json` 已由本轮 **PG 真连**验证生成并纳入版本管理
  （取代此前被 `convert --clean` 删除的版本；**是真实基线，不是 `--no-pg` 降级数据**）
- PG 临时 schema `oracle_poc_verify` / `_t_probe` / `_dod1` 已 DROP；`_t_probe`、`_dod1` 为本轮实测临时对象
- 13 个 `_tmp_*` 实测探针脚本已删除（可回收删除，未进版本库）
- `pipeline/` **0 改动**
- ⚠️ 遗留 **`_tmp_check.py`** 是历史文件（commit `5a0b170`），**非本轮产物，勿删**

### 2026-10-13（迁移能力边界评估 + 全量迁移任务清单 · 只读分析）

**性质**：只读分析。**未修改任何代码或产物**，未执行 `convert_oracle_poc.py` / `verify_oracle_poc_output.py`（有副作用）。
唯一改动：新建本文档 + 修正本文件 §2.1 三处过时状态。

**产出**：`docs/迁移能力边界评估与全量迁移任务清单_2026-10-13.md`

**核心结论**：现有 5 阶段流程可作为「代码转换流水线」跑通，但**不能承担「完整迁移」定义**——
① 手册 §1.1 四层面中，**历史数据迁移**与**应用与运维**两层**一行代码都没有**（实测 grep 0 命中）；
② 9 类「静默语义缺陷」**全绿也抓不到**；③ 65 条 L4 用例 note 写明「非 Oracle 实例实测」，**DoD-4 无法判定**。

**重要勘误**：本文校准了前两份评审（基准 `563e642`）的过时结论——**W0 已修好 A1/A2/A3/A4 四项门禁盲区**，
「Gate 放过结构崩掉文件」「DoD-2/7 不在门禁路径」「跳过即免责」**均已不成立**，勿再引用旧表述。

**新发现（不在任何既有文档中）**：
1. **L4 用例仍在冻结目录**——`scripts/verify_oracle_poc_output.py:110` 默认读 `pipeline/verification/golden/oracle_poc_cases.json`（27,988 B），
   与 AGENTS.md「`pipeline/` 冻结只读」冲突（N5）。
2. **残留扫描器 11 个高风险项全缺**——实测 `CONNECT BY`/`START WITH`/`NO_DATA_FOUND`/`TOO_MANY_ROWS`/`DUP_VAL_ON_INDEX`/
   `RAISE_APPLICATION_ERROR`/`SAVE EXCEPTIONS`/`SYSTIMESTAMP`/`DECODE`/`AUTHID`/`DBMS_SQL` **全部 MISSING**（N1）。
3. **跳过层仍免责**——`framework.py:551-552` 对 skipped 仍 `continue`，`--no-pg` 仍能报「Gate 通过」（N6）。

**任务清单**：A 类代码任务 9 项（6–9 天，N1~N9）／ B 类需决策 5 项 ／ **C 类缺失阶段 4 层**（数据迁移 3–5d、
应用适配 5–8d、切换回退 3–5d、性能验证 2–3d）。
> ⚠️ **C 类四层不在 HANDOFF 顶部「剩余 19–27 天」估算内**——A 类 + 现有 P4-3/P4-4/P2-6 全做完仍只到**工程交付级**。

**待决策（Q1–Q4，见文档 §六）**：Q1 C 类四层是否纳入本项目范围（范围决策，非技术决策）；
Q2 Oracle 实例从哪来（无实例则 DoD-4 只能标 `MANUAL_REVIEW`）；Q3 A 类新编号是否采纳；Q4 应用适配是否在本仓库可行。

**实测基线**：残留 **major 45**（本文实跑扫描器）／ HEAD `28dcd27`（工作 HEAD `584fd06`）／ 产物 `search_path` **0 处** ／
grammar **3 份副本**（各 6,900,343 B）／ 引擎仍反向依赖 AST（`perlstyle_engine.py:3641-3644`）。

**副作用**：无代码/产物改动。⚠️ **工作树有 2 个未跟踪文件**（`workspace/rules/dynamic_sql.py` + 对应测试），
属并行会话的 `EXECUTE IMMEDIATE` 工作（P2-6/P4-3 范围），提交时勿误 sweep。

### 2026-10-13（P2-5 完成 · commit `584fd06`，异常与运行时属性映射体系）

**性质**：纯规则映射（无 Agent 额度消耗），实施路径 v3.1 P2-5 6 子项一次实施。

**新建** `workspace/rules/exception_map.py` + `workspace/tests/test_exception_map.py`（21 用例），接入 `scripts/convert_oracle_poc.py` post-pass（P2-4 之后）。

**6 子项**：
- P2-5a SQLCODE → `_p25_sqlstate`（GET STACKED DIAGNOSTICS 注入第一个 WHEN THEN 后）
- P2-5b SQLERRM → `_p25_msg`
- P2-5c WHEN OTHERS 静默 `RETURN X` → 强制 RAISE（手册 §13.1 加粗禁止静默吞错）
- P2-5d `v := SQL%ROWCOUNT` → `GET DIAGNOSTICS v := ROW_COUNT`
- P2-5e `cursor%NOTFOUND/%ISOPEN/%FOUND` → `NOT FOUND/IS NOT FOUND/FOUND`
- P2-5f Oracle 异常名映射表（13 个：DUP_VAL_ON_INDEX→unique_violation 等）

**实测**：残留 **78 → 45**（消 33，与计划 34 一致）；总分 **77.3 → 80.5 / C**（+3.2）；单测 273 → **294**。

**剩余 45 残留归因**：12 TODO（P4-2 续）+ 24 P4-3（BULK COLLECT 5/FORALL 3/PIPE ROW 2/UTL_FILE 7/EXECUTE IMMEDIATE 7）+ 2 FROM DUAL（P2-6）+ 3 SQLCODE/SQLERRM 列名（非 P2-5）+ 2 P2-2/P2-3 漏网 + 2 其他。

**P2-5c 语义权衡**：把静默 `WHEN OTHERS THEN RETURN X` 改为 RAISE 后，L4 用例 47 fail → 1 fail + 58 skipped（函数现在抛错，用例 expected 是数值）。**这是手册要求的正确行为**，用例期望值"非 Oracle 实测"问题（v3.6 D4）留给 P5-1 / 发起人决策。

**修复过程 2 个 bug**：
1. `_PLACEHOLDER_FMT = "%%P25_STRIP_%d%%"` 经 `% 52` 格式化后是 `%P25_STRIP_52%`（单 %），但还原 regex 用了 `%%`（双 %），导致占位符泄露到产物
2. `_strip_strings_and_comments` 把整个 `$$ ... $$` 函数体当字符串吃掉（`v_cursor%NOTFOUND` 在函数体内不被替换）→ 改为递归处理内部，保留 `$$` 边界

**副作用**：PG 测试 schema 已 DROP；`pipeline/` 0 改动；工作树干净。

**下一步**：P4-3（复杂体专项，5-7 天，已扩范围）→ P4-2 续 PACKAGE batch → P4-4。

### 2026-10-13（W0 完成 · commit `b651980`，验证口径收口，5 子项一次实施）

**性质**：验证口径收口，**不新增转换规则，只接线**。5 个子项按 `docs/整合任务计划_2026-10-13.md` §三 W0 表一次实施。

**5 个子项**：
- **W0-1** 单一验收入口 `scripts/verify_oracle_poc.py`（串联 DoD-1~DoD-7）
- **W0-2** Gate 补结构判定（`framework.py:530` 检查 `structure_issues` critical）
- **W0-3** 报告 `weight_covered`（按 5 个固定 Level 判断覆盖率）
- **W0-4** 完整性 diff 移到 post-pass 之后（`convert_one` 不再算，`_postpass_diff` 补上）
- **W0-5** 接 `cases_file`（默认指向 `pipeline/verification/golden/oracle_poc_cases.json`） + `write_json default=str` 修 `Decimal` 序列化

**实测 oracle_poc 端到端**（PG 15，DoD-6）：

| 指标 | W0 前（--no-pg 假数据）| W0 后（PG 连通真实） | 变化 |
|---|---|---|---|
| 总分 | 81.6 / C | **77.3 / C** | 真实化（-4.3） |
| weight_covered | 35% | **100%** | 表明口径完整 |
| L3 安装 | 不可见 | **205/243** | ✅ 首次可见 |
| L3.5 首调 | 不可见 | **86/91 pass + 5 fail** | ✅ 首次可见 |
| L4 业务等价 | 不可见 | **65/65 跑通 + 47 fail** | ✅ 首次真实接线 |
| Gate 判定 | 漏判（structure_issues 不查）| **正确** | W0-2 起效 |
| 单测 | 257 | **273** | +16 新增 |

**关键突破**：L4 业务等价层**首次真跑** 65 条 golden cases（之前 `cases=[]` 写死，10+ 年 L4 虚位）。 47 fail 集中在 SELF_xxx 伪标识符 + 恒返错值函数，全部属 **P4-4 范畴**。

**单测**：`workspace/tests/test_w0_verification_calibration.py`（16 个用例：W0-2 4 + W0-3 3 + W0-4 1 + W0-5 2 + W0-1 3 + 范围自检 3）。

**W0-1 入口输出示范**（PG 连通，2026-10-13 11:45）：

```
[OK] 完整性 OK: 0 静默丢失
[FAIL] 残留扫描 FAIL: 78 major（与基线一致）
[FAIL] 总分 77.3 / C; weight_covered=100%; gate=未通过
[FAIL] L3.5 FAIL: 5/91 首调失败
[FAIL] L4 FAIL: 47 失败, execution_ratio=100%
[FAIL] 4 项 DoD 未通过
exit 1
```

**副作用**：
- `_verification_*` 被覆盖（**反映真实基线 77.3/C，取代 --no-pg 假 81.6**）
- PG 测试 schema `oracle_poc_verify` 已 DROP
- 入口脚本 emoji 改 ASCII（Windows GBK 兼容）
- `pipeline/` 0 改动

**下一步**：P2-5（异常与运行时属性映射体系，消 34/78 残留 = 44% 杠杆，2-3 天）。

### 2026-10-13（P4-2R 完成 · commit `541f1dc`，紧急回归修复）

**性质**：紧急回归修复，**非新功能**。起手于 `docs/整合任务计划_2026-10-13.md` 紧急插队建议。

**根因**：`perlstyle_engine.py:3916` `_lift_nested_subprograms` 把 Oracle 嵌套函数 `END <name>;` 替换为 `r"\1;\2"`，在 END 后强制加分号 + 保留原 `\2` 中的分号 → 产生 **`END ;;`**。PL/pgSQL 块结束符 `END;` 后遇游离 `;` → 「语法错误 在 ";" 或附近的」。

**修复**：regex 改为 `r"^(\s*)END\s+NAME\s*;?\s*$"` + 替换为 `r"\1END;"`，保留缩进、剥离函数名与原分号。代码变更 +4/-2 行。

**实测 oracle_poc 端到端**（PG 15，DoD-6）：

| 指标 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| `END ;;` 残留 | **73** | **0** | ✅ 全部消除 |
| L3 安装失败 | 92 | **8**（独立 + 7 依赖连锁）| ✅ -84 |
| 残留扫描 major | 78 | 78 | ✅ 无新残留 |
| 单测 | 247 | **257** | ✅ +10 新增 |
| HEAD | `e1fe521` | `541f1dc` | — |

**新暴露的真实问题**（P4-3 范畴，非 P4-2R 责任）：
- `RETURNS TABLE(IN SYS_REFCURSOR)` —— IN 在 TABLE 列名里非法
- `FOR rec IN (SELECT...)` 游标循环 —— PL/pgSQL 不支持隐式 RECORD
- TRIGGER/PACKAGE/SYNONYM/DATA 失败源于前序装不上（依赖连锁）
- PACKAGE_BODY 错误：类型 `score_list` 不存在（P4-4 范畴）

**单测**：`workspace/tests/test_lift_end_label.py`（10 个用例覆盖：基础 regex / 集成 lift / convert_function 端到端 / 产物扫描 / 真实 lift 输出无 labeled END）。

**副作用**：
- `_verification_*` 被 `convert_oracle_poc.py --clean` 删 → `git checkout HEAD` 还原（基线报告未重跑，verify 分数待 W0 时一并刷新）
- PG 测试 schema `oracle_poc_verify` 已 DROP
- `pipeline/` 0 改动
- 工作树干净

**下一步**：W0（验证口径收口，1.5 天，强制前置）→ P2-5 → P4-3 → P4-2 续 → P4-4。详见 §2.2.1。

### 2026-10-13 10:33（🔴 手动全链路实测 → 发现 P4-2 回归，**L3 失败 79% 是单点缺陷**）

**⏭ 下次开工第一步：修 `END ;;`（建议编号 P4-2R，< 0.5 天）**

**现象**：`install_oracle_poc.py install` 在 `FUNCTION/00_FUNCTION.sql:78` FAIL
（`语法错误 在 ";" 或附近的`），后续 5 个文件未跑 → **DoD-1 FAIL**。
L3 安装 **成功 151 / 243，失败 92**。

**根因**（产物 `FUNCTION/00_FUNCTION.sql:77`）：

```sql
EXCEPTION
    WHEN OTHERS THEN RETURN NULL;
    END ;;          ← lift 路径多输出一个分号
$$;
```

**归因**：92 个失败中 **73 个（79.3%）** 的对象含 `END ;;`。明细分布：
FUNCTION 42 + PACKAGE_BODY 31。

**这是 P4-2 引入的回归**：

| 版本 | FUNCTION `END ;;` | 对象数 |
|---|---|---|
| `f52a1ba`（P4-2c）| **0** | 16 |
| `ce4f630`（P4-2e 启用 lift）| **8** | 28 |
| `2810905`（P4-2h 当前）| **42** | 52 |

**交集验证**：38 个坏对象中 **21 个在转换报告的 `Lifted nested` 集合内** → 确认是 lift 拼装路径的缺陷，
ANTLR 检测与正则检测两条子路径都中招。

**定位提示**：`END <name>;`→`END;` 的剥离逻辑在 `perlstyle_engine.py:4154-4160`，
但 **lift 走的是另一条拼接路径**，需顺着 `_lift_nested_subprograms` 的产物拼装处找。
**复现**：`convert --clean` 后 `grep -n 'END\s*;;' oracle_poc/output/*/*.sql`。

**预期收益**：L3 失败 92 → 约 19；DoD-1 可跑通。

**✅ 顺带确认的一个正面结论（确定性）**：

> 本次 `convert --clean` 重转后，**12 个对象产物 `.sql` 全部与已提交版本字节一致**，
> `git diff` 只有 `00_CREATE_ALL_PG.sql` 的一行生成时间戳变化。
> **规则层转换是确定性的、可重复的**——这是手册 §19.5 与二期 T20「Agent 确定性化」想要的基础能力，
> 已具备。**P4-2R 修复后应再跑一次确认这条性质不被破坏。**

---

**本次实测数据（PG 连通，第一次拿到真实 L3 数据）**：

| 步骤 | 结果 |
|---|---|
| `convert_oracle_poc.py --clean` | 12/12 成功，警告 89；P2-2 代 6 处 `%TYPE`、P2-3 改 2 处 `(+)`、P2-4 改 3 处序列同义词 |
| `install_oracle_poc.py install` | ❌ FAIL（FUNCTION:78）|
| `verify_oracle_poc_output.py` | 总分 **80.5 / C**；**L3 失败 92**；L3.5 总 40 / 35 pass / **5 fail** |
| `scan_oracle_residuals.py` | critical 0 / **major 78** / minor 0 |
| `pytest workspace/tests/` | **247 passed** |

**与历史基线的差异（重要）**：

| | 历史记录 | **本次实测** |
|---|---|---|
| L3 | 152/176（24 fail）| **151/243（92 fail）** |
| 总分 | 84.1 / C（`--no-pg`）| 80.5 / C（**PG 连通**）|
| L3.5 | 37 pass / 5 fail | **35 pass / 5 fail**（总 40）|

> L3 失败绝对数 **24 → 92**，对象总数 176 → **243**。P4-2 的 lift 产出 43 个新函数对象，
> 其中 73 个因 `END ;;` 装不上。**符合「暴露真缺口不回退」，但确认引入了回归。**
> ⚠️ **此前所有基线都是 `--no-pg` 降级数据（65% 权重缺席），这次的 80.5 才是真实分数。**

**L3.5 的 5 个 fail —— 全部 `SELF_xxx`（P4-4 范畴，与 §1.1 一致）**：
`get_status_of_score_record` / `get_letter_grade_of_score_record` / `get_age_of_student_record` /
`get_gender_name_of_student_record` / `get_rank_str_of_student_record` —— 均报「字段 `self_xxx` 不存在」。

**本次额外发现的两个手册违规**：

| # | 发现 | 数量 | 手册依据 |
|---|---|---|---|
| **N1** | **`WHEN OTHERS THEN RETURN NULL` 静默吞错** | **13**（FUNCTION 10 + PACKAGE_BODY 3）；`WHEN OTHERS` 总计 **104** | §13.1 加粗"不得静默吞掉"；§21.2 禁止 → **归入 P2-5c** |
| **N2** | **嵌套函数声明原样留在 `BEGIN` 块内未 lift**，但注释已标「提升为独立函数」 | 3（PACKAGE_BODY）| §9.2 关键原则 → **归入 P4-2R** |

> **N2 是"假成功"的又一实例**：注释写「-- 嵌套函数: 提升为独立函数」，紧接着却是未转换的 Oracle 语法。
> **注释不能作为"已处理"的证据**——与实施路径 §1.3 同源。

---

### 2026-10-13（代码级对照评审 → 实施路径 Final v3 + 计划重排）

**性质**：本 session **未实施任何 P\* 任务**，只做评审与文档同步。

**产出文档**（均为新增）：

| 文档 | 内容 |
|---|---|
| `docs/工作区实现评审与优化建议_2026-10-13.md` | 验证/门禁层视角：Gate 盲区、已建能力未接入生产路径、分层与依赖方向 |
| `docs/迁移手册与workspace代码级对照_2026-10-13.md` | 转换规则层视角：手册 29 章逐条 × 引擎源码，含 live PG 语义实测 |
| `docs/整合任务计划_2026-10-13.md` | 执行序 + 批次 + 残留归因 + 6 个决策点 |

**已同步的既有文档**：

- `docs/最终实施路径_生产级_2026-10-09.md` —— 追加 **Final v3 修订记录**（新增 W0/P2-5/P2-6/P5-4/T26、P4-3 扩范围、执行序调整、**事实勘误 3 处**）
- `HANDOFF.md` —— §顶部状态块 / §1 基线演进与残留归因 / §2.1 任务清单 / §2.2.1 实际执行序 / §2.3 里程碑状态

**关键实测数据**（HEAD `74f6363`）：

```
单测 247 passed (34.77s)   总分 81.6/C   残留 critical 0 / major 78
引擎零命中复核：WHEN OTHERS / SQLCODE / SQLERRM / INTO STRICT /
              GET DIAGNOSTICS / EXECUTE IMMEDIATE 均为 0
live PG 15.19 实测：
  CASE WHEN NULL = NULL -> nomatch        （证实 DECODE-NULL 转换会静默出错）
  txn CURRENT_TIMESTAMP frozen: True       （证实 SYSTIMESTAMP→CURRENT_TIMESTAMP 改语义）
  EXECUTE PROCEDURE : ACCEPTED by PG15     （实测接受，**非阻塞项**，仅弃用写法）
```

**新发现的最高优先级缺口**（详见两份评审文档）：

- `WHEN OTHERS` 源中 **112** 处、引擎处理 **0**；`SQLCODE`×12 / `SQLERRM`×13 原样穿透
- `search_path` 产物中 **0** 处（手册 §16 加粗要求必须显式限定）
- Gate 会放过 `TRIGGER/00_TRIGGER.sql`（L2=20 / 12 结构问题 / 判通过）

**⚠️ 副作用与异常**：

1. **并行作业**：09:40→10:07 期间另一会话提交 `2810905`（P4-2h），并把本 session 的第一份评审文档一并 sweep 进该 commit。
   **下次提交前务必 `git status`**。
2. **残留上升**：74 → 78（P4-2h 的 lift 输出带进 4 处新残留）。符合"暴露真缺口不回退"，
   但**需在 P4-2 收尾时归因**。
3. **HANDOFF 原内部矛盾已修**：§1 写"P4-2h ✅ / 228 测试"、顶部写"下一步 P4-2h"，现统一为实测值。
4. **未执行**：`convert_oracle_poc.py`（会改写产物）、`verify_oracle_poc_output.py`（会覆盖 `_verification_*`）。
   当前 `_verification_report.md` 仍是仓库已提交版本，未被污染。

**⏭ 下次开工第一步**：

```
读 HANDOFF.md §2.1.1（W0 为什么强制前置）
  → 读 docs/整合任务计划_2026-10-13.md
  → 起手 W0（1.5 天，5 个子项，不改任何转换规则）
  → W0 完成后跑一次 scripts/verify_oracle_poc.py，确认 weight_covered=100%
  → 再进 P2-5
```

**⏸ 待发起人决策（阻塞 DoD-4，非代码问题）**：

> **D4**：65 条 golden 用例的 `note` 明确写「期望值由源码语义推导，**非 Oracle 实例实测**」。
> 手册 §22.4 要求语义不确定须标 `MANUAL_REVIEW`。
> 需二选一：① 安排一次 Oracle 实例回归；② 标 `MANUAL_REVIEW` 且 DoD-4 暂不声称达成。

### 2026-10-12（P4-2 阶段：Agent 重写 + 引擎 Bug 根治）

**P4-2a**（`ba50389`）：新增 `schema_dict.py`，扫描产物 63 个可用函数注入 prompt，解决 Agent 盲引用未 lift 的嵌套函数（BUILD_QUERY 等）。

**P4-2b**（`409b5ac`）：SYSTEM_PROMPT 加 cursor/loop 转换规则；`--verify-file-install` 整文件试装；`apply_results_to_text` 修复行号偏移；PGCLIENTENCODING=UTF8 修 Windows psql 编码。

**P4-2c**（`f52a1ba`）：修复 `extract_code` 清理 markdown ``` 围栏（首尾都清）；`apply_results_to_text` 回归字符串替换。**FUNCTION 桩 13→0**（里程碑）。

**P4-2d 根因**（`ec946b9`）：
- **Bug**：`perlstyle_engine._lift_nested_subprograms` depth 计数把 `END IF/LOOP/CASE` 误当块结束 → 7 个嵌套函数全部 skipping lift → INLINED 残骸 → PG 装不上。
- **修复**：END 后跟 IF/LOOP/CASE 时跳过 depth 计数。实测 skipping lift **7→0**，Lifted nested **0→18**。
- **剩余**：inline 路径与 lift 打架 → **P4-2e 根治**。INLINED 6→0，`_nested_result` 5→0。

**P4-2f 验证**（ANTLR 切分）：ANTLR 实测 oracle_poc 源文件 15.2s / 0 错误 / 识别 24 个函数体节点（含嵌套层级）。确认可用于 chunker 替代正则（待实施）。

**快照 reset**：`59867bc` / `9087f5c` / `a9a89b5` / `ee52ef7` / `f2fdda0` 为自动 snapshot commit，已软 reset 合并为 P4-2c。

**副作用记录**：
- `oracle_poc/output/FUNCTION/00_FUNCTION.sql` 被多次 write-back + 回滚（产物变更未入正式 commit 前的 snapshot 已还原）
- `oracle_poc/output/_verification_*.md/.json` 被 `verify_oracle_poc_output.py` 覆盖过，git checkout 已还原

**下一步**：P4-2f（ANTLR chunker：新建 `workspace/parsers/chunker_ast.py`，接口 `chunk_oracle_by_antlr`，SHA256 缓存，失败降级正则；目标是丢 3 个顶层函数归零）→ P4-2g（validate_pg 格式校验：`$$` 配对 / `RETURNS void` / `$$;plpgsql` 分隔）→ PACKAGE_BODY batch → PACKAGE batch。

**P4-2e**（`ce4f630`）：禁用 `_inline_nested_functions` 旧内联路径（三层 bug 根治）：
- 主路径冲突：lift 成功后 inline 仍跑 → `_nested_result` 死代码。修复：加 `nested_lifted` 参数，lift 成功后 skip inline。
- lift 失败漏网：plsql_subs import 失败返回 `[]` → `bool([])=False` → inline 照跑。修复：`found_any_header=True` → 返回 `[True]` 哨兵。
- lift 内部也有 inline：`nested_lifted=False` 默认值。修复：lift 内部调用 `_convert_single_function` 时传 `nested_lifted=True`。
- 实测：INLINED 6→0，`_nested_result` 5→0。
- **剩余已知问题**：
  ① `SYSTEM_CONFIG.CONFIG_VALUE%TYPE` → P2-2 无表定义，待 P2-2 补充
  ② **勘误**（2026-10-12 实测）：F_GET_WEIGHTED_AVG / F_GET_CONFIG_VALUE / F_GET_JOBNUM **均在产物中**（grep 确认源 10 个顶层函数全部存在）。"丢函数"是误判——真正问题是 pre-BEGIN 嵌套函数未 lift，导致这些函数体内残留 Oracle 语法 → L3 FUNCTION 7 项失败（待 P4-2f）
  ③ `$$;plpgsql` 挤一行 / `RETURNS void` 幻觉 / `$$` 失衡 → 待 P4-2g
  ④ ANTLR `$$;` 解析错 → grammar 不支持 PG `$$` 定界符（pre-existing）

**P4-2f**（🟡 进行中，未 commit）：
- **勘误**："丢 3 个顶层函数"是误判。grep 实测：源 10 个顶层函数全部在产物（产物 26 个 CREATE FUNCTION = 10 源 + 16 lift）。
- **已完成**：
  1. `workspace/parsers/chunker_ast.py`（433 行）：ANTLR 切分器，接口 `chunk_oracle_by_antlr(sql_src) → list[dict]`，SHA256 缓存，失败降级正则，`allow_errors=True` 支持部分解析
  2. `workspace/tests/test_chunker_ast.py`（240 行）：20 单测全部通过
  3. `perlstyle_engine.py` `_lift_nested_subprograms`（+214 行）：新增 ANTLR 策略——IS...BEGIN depth-aware 提取声明段 + CREATE OR REPLACE 前缀 + DECLARE 包装，行号映射回 original block
- **当前障碍**：ANTLR 路径在 `except Exception: pass` 块内静默失败，产物未变（仍 25621B / 18 lift）。**下一步**：删除 `pass` 打印异常，定位真实错误（最可能：ANTLR 行号映射偏移 → `_hdr_line` 全程为 None → 无 chunk 进入 nested_blocks）
- **真正未完成**：pre-BEGIN 嵌套函数未被 lift（如 F_GET_AVG_SCORE 的 VALIDATE_STUDENT_NO / CHECK_FORMAT），函数体残留 Oracle 语法 → L3 FUNCTION 7 项失败

**下一步**：修 ANTLR 行号映射 → 确认 pre-BEGIN 嵌套 lift 生效 → 产物变化验收 → **P4-2g**（validate_pg 格式校验：$$ 配对 / RETURNS void / $$;plpgsql 分隔）→ PACKAGE_BODY batch → PACKAGE batch。


### 2026-10-09（Final v2：实施路径成为唯一权威，任务书归档）
- **Review 结论**：《最终实施路径》v1 有 5 个问题 —— ① 测试文件列表误用已作废的 T0/T17 编号
  ② 与任务书并行存在导致落点冲突 ③ **缺前置条件** ④ **缺二期路线图（生产级目标必需）**
  ⑤ LFS 表述错误（原 grammar 已在 git 跟踪内，无需 LFS）
- **已修正并补全为 Final v2**（10 章）：补 Pre-1~Pre-9 实测状态、默认决策表、外部资源、预期收益；
  编号统一为 **P1-1~P5-3**（阶段一~五）+ **T16–T25**（二期）；
  澄清 **P1-1 与 P3-4 的边界**（前者让丢失对象出现在报告里，后者做结构化生成升级）；
  排期由 35–55 天调整为 **40–60 天**
- **任务书已归档**：`docs/改进任务书_达到可用状态_2026-10-09.md` 顶部加 ⛔ 归档声明，
  说明取代原因与内容去向；保留 Rev.1–5 演进供追溯
- **HANDOFF 同步**：任务清单/排期/里程碑换成 P1-1~P5-3 体系；
  DoD 表、装上即坏对象表、关键文件速查、工作流中的旧编号引用一并修正
- **未完成**：P1-1~P5-3 全部未实施（本轮纯文档 + grammar 复制，未动转换代码与 SQL 产物）

### 2026-10-09（Final v1：产出《最终实施路径_生产级》文档）
- **动作**：整合《分析整合报告》+《改进任务书 Rev.5》+ 本轮全部实测，产出
  `docs/最终实施路径_生产级_2026-10-09.md`（**实施路径 + 三层架构的唯一真源**）。
- **技术栈定案**（用户指定：规则引擎 + ANTLR + AI Agent）：
  - 规则引擎 = `ora2pg_py_perlstyle`（实测 **6195 行 / 94 顶层函数 / 11 个 convert_* 入口**），**保持主力不搬迁**
  - ANTLR = **体检仪，不做翻译**（2500 倍减速，只在转换后跑一次，约 48s）
  - AI Agent = 复杂体最后手段（`validate_pg` 硬校验 + 每批 git 快照 + 200 次配额上限）
- **实现目录**：`workspace/` —— 新建 `rules/`（规则）、`parsers/`（AST）、`agent/`（Agent）、`tests/`（单测）
- **路径规划**：5 阶段 35–55 天，M0"能看见"(D4) → M1"规则达标"(D13) → M2"结构可信"(D18) → M3"复杂体接管"(D33) → M4"生产就绪"(D40)
- **新实测发现**：`_lift_nested_subprograms`（`perlstyle_engine.py:3466`）**已实现且已在主路径调用**，
  但产物仍有 INLINED 残骸 → **T10 范围需重估**（从"实现 lift"改为"补齐覆盖"）
- **文档分工**：冲突时优先级 **实施路径 > 任务书 > 分析报告**；HANDOFF 顶部已更新阅读顺序
- **未完成**：全部实施任务未开工（本轮纯文档 + 分析 + grammar 复制，未动转换代码与 SQL 产物）
- **grammar 副本落地**：`pipeline/parsers/grammar/` 6 文件 → `workspace/parsers/grammar/`
  （**8,754,421 B**，SHA-256 全一致，独立解析通过）；写入 `README.md`（provenance + 5 条约束）；
  任务书 T7 新增 §7.1 记录该决策；T15 新增 2 步（处理 `.g4` 不可再生缺口 + 版本守卫）
- **冗余决策理由**：workspace 自包含 > 避免依赖冻结目录；代价 8.75MB 可接受；
  原始副本已在 git 跟踪内，故副本沿用同一策略，无需 LFS

### 2026-10-09（Rev.5：任务书整合重写为干净版）
- **动作**：把经 Rev.1–4 四轮补丁的任务书**整合重写**为 Rev.5 干净可实施版。
- **清理内容**：剔除全部 `【Rev.x】` 行内批注（约 40 处）与 `~~删除线~~`（11 处），
  四次修订结论**直接融入正文**；篇幅从 1225 行降至 1150 行但信息量不减。
- **保留**：全部实测证据（行号、字节数、计数、行数订正）、落点纪律表、两条方法论教训、
  DoD-7、T0、依赖图/排期/里程碑、修订记录索引、新增「关键事实复核记录」表。
- **新增可读性改造**：
  - 文档头「接手人必读 · 两条最高优先级约束」（落点 + 先做 T0）
  - Pre 表加**实测状态列**
  - 任务书各 T\* 统一体例：现状证据（实测）/ 目标 / 涉及文件（标注唯一真源）/ 实施步骤 / 单测 / DoD 验收 / 风险 / 规模
  - 风险表新增「改动落点误判」「静默丢失复发」两条
- **归档**：`docs/archive/2026-10-09_reports/改进任务书_达到可用状态_Rev4_2026-10-09.md`
  （实际为 git HEAD 的 v1 快照，含 Rev.1–2；已在文件头标注版本说明，**勿据此实施**）
- **未完成**：T0–T15 全部未实施（本轮纯文档工作，未动代码与 SQL 产物）。

### 2026-10-09（Rev.4：按「生产可用」目标重新校准）
- **依据**：对《转换产物分析整合报告》做**独立复核**（逐条重测，不采信报告转述）。
- **发现一 · 最严重**：**静默丢失 + 假成功**。源 6 个触发器 → 产物仅 1 个，**5238→883 字节（丢 83%）**，
  而 `_conversion_report.json` 记 `success:true, warnings:[]`。**L1/L2/L3/L3.5 四层全无法发现**
  —— 丢失对象在产物里不存在，无物可报错。
  根因 `perlstyle_engine.py:1978` 代码自注 *"Convert only the first trigger block"*；
  `_split_triggers()` 已实现但**从未被调用**；commit `1bbc132` 引入。全引擎扫描确认仅此一处。
  → **新增 DoD-7 + 新任务 T0（P0·最高优先·排 D1）**，配套 M0 里程碑。
- **发现二 · 落点错误**：`scripts/` 全量扫描 **7 处 import → `workspace/`，0 处 → `pipeline/`**。
  真实引擎是 `ora2pg_py_perlstyle/perlstyle_engine.py`；`workspace/converter/perlstyle_converter.py`
  仅 290 行薄包装。任务书旧版 11 处 + 8 处单测路径指向 `pipeline/` → **照做产物不变、DoD-6 落空**。
  → **负责人决定：`pipeline/` 冻结为只读参考，一律实现在 `workspace/`。**
- **对报告的复核结论**：报告**事实层面可靠**（13 文件、86.6/B/24 失败、L3 分项、`%SUBQUERY`×3、
  `%TYPE`、`INLINED`×8、TODO×28、5/6 触发器丢失 —— 逐条实测全吻合），
  但**未识别**「静默丢失」层，且架构图隐含 `pipeline/` 为生产链路。Rev.4 已补正。
- **连带修订**：T8 拆 T8a/T8b（T8a 不依赖 T7，可立即开工）；T7 降级为版本核对（1–3 天 → **0.5 天**）；
  §二 架构章整体重写 + 落点对照表；行数估值订正 9 处；T15 新增 pipeline 冻结副本处置。
- **编号消解**：新任务定为 **T0**（§十 二期已占用 T16–T25）。
- **落盘**：任务书 §九 Rev.4（12 条）+ 文档头两条最高优先约束 + DoD-7 + §二 2.0–2.2 + T0/T7/T8 + §四/§五；
  HANDOFF 新增 §1.6/§1.7 + DoD-7 + 落点对照 + 工作流落点纪律 + 验证副作用警告。
- **副作用登记**：`verify_oracle_poc_output.py` 会覆盖 `oracle_poc/output/_verification_*`；
  `--no-pg` 更会用降级数据覆盖基线，且 Windows 中文环境写出 GBK 乱码。**跑前备份 / 跑后 `git checkout` 还原**。
- **未完成**：**T0–T15 全部未实施**（本轮纯文档修订，未动任何代码与 SQL 产物）。
- 详见 `docs/改进任务书_达到可用状态_2026-10-09.md` §九 Rev.4。

### 2026-10-09（Rev.3：T1 开工前实测校准）
- **T1 状态：🟡 进行中**。开工前逐条实测 §〇 前置条件与 T1 全部证据行号（**不采信文档转述**）。
- 关键发现：仓库存在**两份 `verification/` 真实副本**，Gate 只读 `workspace/verification/`
  （`verify_oracle_poc_output.py:26,29`）。任务书原指 `pipeline/verification/` → 照做会空转。
  决策：**T1 唯一真源 = `workspace/verification/`**。
- 修正 3 处事实错误：TODO 口径（28 = 块注释 16 + 行注释 12）、T1 验收基线
  （实测 critical 0/major 73/minor 12，原写 ≥8/≥30 不成立）、`FROM DUAL` 行号（522，原 307）。
- `antlr4` **实测已装** → Pre-3 失效，**利好 T7**（降级为版本匹配验证）。
- 基线复现：`--no-pg` → 86.7/B，Gate 通过（原记 86.6）。
- 修订已落盘：任务书 §九 Rev.3（6 条）+ §〇 Pre-1a/Pre-3 + T1 正文；HANDOFF §1.3/§1.4/§5.3/§4。
- **副作用**：`framework.py`/`__init__.py` 两份副本**已分叉**，合并留待 T15，本次不动。
- **未完成**：T1 实施本身（`scripts/scan_oracle_residuals.py` 尚未创建，Gate 未接入）。
- 详见 `docs/改进任务书_达到可用状态_2026-10-09.md` §九 Rev.3。

### 2026-10-09（P1-1 完成 · commit `b441bb6`）
- **动作**：修复 `perlstyle_engine.py:1955 convert_trigger`——遍历 `_split_triggers` 全部分块，
  不再只转第一个；COMPOUND TRIGGER 显式登记到 warnings，不再静默丢弃。
- **新建** `workspace/converter/integrity.py`：`count_objects` / `diff_objects` / `IntegrityReport`，
  供 P1-2 门禁消费（DoD-7 基础）。
- **新建** `workspace/tests/test_multi_object_conversion.py`：9/9 通过（按内容断言，无行号）。
- **实测结果**（oracle_poc/TRIGGER）：
  - 源 6 个触发器 → 产物 **5 个 TRIGGER 函数** + **1 个 COMPOUND 显式登记**（TRG_SCORE_COMPOUND）
  - 字节 883B → **4172B**（源 5238B）；转换报告不再 "假成功"
  - `integrity.diff_objects` 输出：missing=['TRG_SCORE_COMPOUND']，ok=False（**符合预期**——
    这正是 P1-2 门禁要硬 fail 的对象；它从"看不见"变成"显式可见"）
- **预期副作用**（实施路径 §四已预警）：L3 安装失败数预计会从 24 **上升**——
  现在 5 个触发器真的尝试安装了，暴露真实缺口。严禁为数字好看回退。
- **下一步**：P1-2 完整性门禁（`dropped_objects` 非空即 exit 1；建 13 文件对象数基线）。

### 2026-10-09（P1-2 完成 · commit `9db1881`）
- **动作**：`scripts/convert_oracle_poc.py` 接入 `converter.integrity.diff_objects`；
  record 新增 `dropped_objects` 字段；main() 非空即 exit 1（硬门禁）。
- **产物侧名提取模式**（`workspace/converter/integrity.py`）：
  SYNONYM→VIEW / PACKAGE→SCHEMA / TYPE→CREATE TYPE / TRIGGER→FUNCTION...RETURNS TRIGGER；
  源模式排除 TYPE BODY / PACKAGE BODY 误匹配。
- **13 文件对象数基线**：`workspace/converter/integrity_baseline.json`
  （12 文件 / 63 源对象；生成器 `scripts/build_integrity_baseline.py`）。
- **实测门禁效果**：
  - 接入前 14 个"丢失"（含 SYNONYM/PACKAGE/TYPE 误报）→ 接入后 **1 个真实丢失**
  - 唯一丢失 = `TRG_SCORE_COMPOUND`（COMPOUND TRIGGER 无 PG 原生等价物，
    P1-1 已显式登记进人工清单，等 P3-4 结构化实现）
  - exit code = **1**（硬门禁生效）
- **单测**：`workspace/tests/test_integrity_gate.py` 4/4 通过；全量 13/13 通过。
- **下一步**：P1-3 残留扫描器（覆盖 `/* TODO` 16 + `-- TODO` 12）。

### 2026-10-09（P1-3 完成 · commit `83607a2`）
- **新建** `workspace/verification/residuals.py`：DoD-2 完整清单 **22 条规则**。
  * TODO 块注释 `/* TODO:` 与行注释 `-- TODO:` **两种形态都覆盖**（实测 16+12=28）
  * 剥离注释防 Oracle 原注释误报；TODO/INLINED 用 `raw=True` 在原始代码上匹配
  * 输出 `ResidualMatch` + `ScanReport`，`ok = (critical=0 and major=0)`
- **新建** `scripts/scan_oracle_residuals.py`：CLI 入口，exit 0=PASS / 1=FAIL / 2=参数错，
  支持 `--json` 输出。
- **新建** `workspace/tests/test_residual_scanner.py`：7/7 通过。
- **实测基线**（oracle_poc/output，12 文件）：
  * `total_hits=103, critical=3, major=100, minor=0`
  * 与 HANDOFF §1.5 实测证据**完全吻合**：`/* TODO`=16、`-- TODO`=12、INLINED=8、
    `%SUBQUERY`=3、`%TYPE`=6、SQLCODE=10、SQLERRM=11、EXECUTE IMMEDIATE=6、
    UTL_FILE=6、BULK COLLECT=4、FORALL=4、`%NOTFOUND`=6、`%ISOPEN`=4、
    SQL%ROWCOUNT=3、PIPE ROW=2、FROM DUAL=2
  * 门禁 `ok=False`（DoD-2 要求 major=0），exit=1
- **全量单测**：20/20 通过（P1-1: 9 + P1-2: 4 + P1-3: 7）。
- **下一步**：P1-4 首调冒烟层（L3.5，每个对象建完立即最小调用）。

### 2026-10-09（P1-4 完成 · commit `dda2435`）
- **新建** `workspace/verification/smoke_check.py`（L3.5 首调冒烟层）：
  * 函数/过程真实调用、视图 SELECT、序列 nextval、触发器经宿主表 DML（事务内+ROLLBACK）触发
  * STRICT 函数自动用类型哑值（numeric→1、text→''、date→'1970-01-01'）
  * 伪类型参数 → unsupported（不计 fail）
  * 触发器错误归因：plpgsql CONTEXT 区分函数报错（fail）vs 纯约束（BEFORE→pass/AFTER→not_fired）
  * 中文 locale 兼容（PL/pgSQL函数trg_x() 无空格形态）
  * 状态分级：pass / fail / blocked / not_fired / unsupported（DoD-3 只计 fail）
- **新建** `workspace/tests/test_smoke_layer.py`：22 个测试（11 纯函数 + 11 集成）
  * 关键：BEFORE 触发器被 NOT NULL 约束拒收时仍判 pass（函数体已成功执行）
- **改动**：
  * `pg_sandbox.py`：新增 `execute_detailed()`（含 CONTEXT）和 `try_dml_rollback()`（事务+ROLLBACK）
  * `framework.py`：新增 L3.5 层级（weight=0，只进 Gate 不动基线分数）
  * 冒烟范围 = 目标 schema + 本次安装新建的 schema（**避免 gkb* 公共表/旧 rt2 副本污染**）
  * `report.py`：渲染 L3.5 明细 + failures 表
  * `verify_oracle_poc_output.py`：打印 L3.5 摘要（ASCII 标，避免 GBK 乱码）
- **实测 oracle_poc 端到端（PG 15）**：
  * 冒烟对象 **42 个**（FUNCTION 17 / PROCEDURE 1 / VIEW 18 / SEQUENCE 6）
  * pass 37 / fail 5 / blocked 0 / not_fired 0
  * 5 fail 全部在 oracle_poc_verify 内（**rt2/rt_test 副本污染已剔除**）：
    SELF_xxx 伪标识符 → `字段 self_score_record_is_pass 不存在`
  * 与 HANDOFF §1.1 第 8 项精确对应（Type member 语义错）
  * L3 装入 **148/172**（从 141 上升，符合 P1-1 暴露真实缺口预期）
  * 总分 84.3 / C（**基线重估**——L3 失败数增加、Gate 反映更多真相）
- **M0 里程碑（D4）达成**：转换不再假成功、触发器 6→5 显式登记、完整性门禁生效、
  残留扫描器 0 major + L3.5 5 fail（已知问题全可见）
- **P1-4 实施发现·非本次引入问题**：
  * `verify_suite` install 排序 bug：DROP TRIGGER 被分类为 OTHER，排在 CREATE TRIGGER (TRIGGER)
    之后执行 → 本批安装的 5 个触发器在后续被同文件的 DROP 句清除，**冒烟时已不存在**
    → 当前 trigger 冒烟覆盖为 0。修复落点：framework install 排序（按 source seq 不跨表重排）
    → **留待 P3-4 触发器升级时一并处理**。
  * 7 个"恒返 X"类语义错（F_GET_TOTAL_SCORE/-1 等）需值断言，属 L4/P5-1 而非 L3.5
- **单测**：42/42 通过（P1-1: 9 + P1-2: 4 + P1-3: 7 + P1-4: 22）
- **下一步**：阶段二 P2-2（`%TYPE` 查表展开）。

### 2026-10-09（P2-1 完成 · commit `41ca8da`）
- **新建** `workspace/rules/subquery_restore.py`（P2-1 唯一逻辑落点）:
  * `restore_subqueries(sql, state)` 纯函数：还原 state.subquery_map 中缓存的
    (SELECT|WITH ...) 原文
  * 严格策略：任一 idx 缺失 → `SubqueryRestoreError`（数据丢失零容忍）
  * 错误消息列出所有缺失 idx + 上下文（每处 40 字符前后）
- **新建** `workspace/tests/test_subquery_restore.py`：12 个测试
  * 9 纯函数单元（FROM/USING/多 idx/边界/verbatim/缺 idx 抛错/部分缺失抛错/错误消息全索引）
  * 1 往返（`extract_subqueries` → `restore_subqueries` 可逆）
  * 1 引擎集成（`convert_function` 后产物无 `%SUBQUERY`）
  * 1 oracle_poc 端到端（复刻 HANDOFF §1.1 第 1 类数据丢失场景）
- **改动**：
  * `perlstyle_engine.py` `_convert_plsql_code` 流水线末尾新增 **step 16: 还原 %SUBQUERY%d%**
  * 落点：`extract_subqueries`（step 1c）缓存 → 所有转换步骤操作占位符 → 本步骤还原
- **实施过程修复**：
  * 初始引入 `from workspace.rules...` 误路径 → `convert_oracle_poc.py` 的 sys.path 布局
    把 `workspace/` 作为一项，等同 P1-2 的 `from converter.integrity import`
  * 改为 `from rules.subquery_restore import`（与现有约定一致）
  * 副作用：PACKAGE_BODY 转换从 success=False 恢复为 success=True
- **实测 oracle_poc 端到端（PG 15，DoD-6）**：
  * convert: 12/12 成功, 1 dropped (TRG_SCORE_COMPOUND 显式登记)
  * **`%SUBQUERY` 计数: 3 → 0** ✓ 验收达成
  * 还原样例：
    `USING(%SUBQUERY0%) D` → `USING (SELECT 1 AS DUMMY FROM DUAL) D`
    `FROM (%SUBQUERY0%) alias0` → `FROM (SELECT ... GROUP BY ...) alias0`
    `FROM (%SUBQUERY1%) alias1` → `FROM (SELECT ... GROUP BY ...) alias1`
  * L3: 172/148/24（与 P1-4 基线一致，**无回归**）
  * L3.5: 37 pass / 5 fail（与 P1-4 一致）
  * 总体分 84.1（P1-4 84.3，噪声范围）
- **单测**：54/54 通过（P1-1: 9 + P1-2: 4 + P1-3: 7 + P1-4: 22 + P2-1: 12）
- **下一步**：P2-2 `%TYPE` 查表展开（实施路径 §四 阶段二 P0·重）。

### 2026-10-09（P2-2 完成 · commit `64031ab`）
- **新建** `workspace/rules/percent_type.py`:
  * `build_type_map_from_table_sql(sql)`: 解析 CREATE TABLE 建 (TABLE, COL) → PG_TYPE 字典
    （区分列定义与 CONSTRAINT，常见 PG 类型关键字白名单过滤）
  * `resolve_percent_types(sql, type_map)`: 仅在 DECLARE 块内替换
    `table.col%TYPE` → PG 类型；找不到保留原文 + warning（**P2-2 宽松策略**）
  * 严格处理：字符串/注释/大小写归一/DECLARE 块定位（DECLARE→匹配 BEGIN）
  * 范围限制：仅 `table.col%TYPE` 形式（`var%TYPE` 属另一任务）
- **新建** `workspace/tests/test_percent_type.py`：19 个测试
  * 7 纯函数（build）：单/多表、大小写、VARCHAR(100)、约束、注释豁免
  * 11 纯函数（resolve）：DECLARE 块外不替换、多块、默认值、字符串/注释豁免、
    大小写、缺失保留+warning、`var%TYPE` 不动
  * 1 oracle_poc 端到端：调用后 6 处 %TYPE 全部展开
- **改动**：
  * `scripts/convert_oracle_poc.py`：新增 `post_pass_percent_type()` 后处理
    从已转换 TABLE 文件建 type_map，对 FUNCTION/PROCEDURE/PACKAGE/PACKAGE_BODY/TRIGGER
    逐个跑 resolve，写回文件 + 收集 warnings（驱动生成后运行，驱动 `\i` 读新内容）
- **实施过程修复**：
  * 首次 `_strip_strings_and_comments` 把 `$$ ... $$` 当作 dollar-quote 整体替换
    → DECLARE/BEGIN 在函数体内的位置全找不到（透传修正）
  * `type_map={}` 时误用 `if not type_map` 早返回 → 无替换且无 warning（移除该早返回）
  * 字符串/注释豁免：首次仅在 DECLARE 定位时剥离，%TYPE 匹配时不剥离
    → 字符串字面量 `'T.X%TYPE not allowed'` 被误匹配（掩码检查修正）
- **实测 oracle_poc 端到端（PG 15，DoD-6）**：
  * convert: 12/12 成功, 1 dropped（TRG_SCORE_COMPOUND 显式登记）
  * **`%TYPE` 计数: 6 → 0** ✓ 验收达成（验收标准写 5→0，实际为 6 处，**超额达成**）
  * 还原样例：
    `v_X STUDENT_MST.STUDENT_NO%TYPE;` → `v_X BIGINT;`
    `vPASS_SCORE SUBJECT_MST.PASS_SCORE%TYPE := 60;` → `vPASS_SCORE BIGINT := 60;`
    `v_VALUE SYSTEM_CONFIG.CONFIG_VALUE%TYPE;` → `v_VALUE VARCHAR(1000);`
    `vGRADE STUDENT_MST.GRADE%TYPE;` → `vGRADE BIGINT;`
    `vCLASS STUDENT_MST.CLASS_NO%TYPE;` → `vCLASS BIGINT;`
  * L3: 172/148/24（与 P2-1 一致，**无回归**）
  * L3.5: 37 pass / 5 fail（与 P2-1 一致）
  * 总体分 84.4（P2-1 84.1，+0.3）
  * 残留扫描器: 103 → 97（6 处 %TYPE 命中规则被清除）
- **P2-2 未涉及（后续任务）**：
  * `var%TYPE` 形式（无表名前缀）需语义级追踪：变量声明 → 后续 `%TYPE` 引用 → 查该变量类型
    留 P3-2 结构体检器 / 后续任务
- **单测**：73/73 通过（P1-1: 9 + P1-2: 4 + P1-3: 7 + P1-4: 22 + P2-1: 12 + P2-2: 19）
- **下一步**：P2-3 `(+)` 外连接结构化改写（实施路径 §四 阶段二 P0·重，**3–5 天**——小型编译器子任务）。

### 2026-10-09（P2-3 完成 · commit `e8465d8`）
- **新建** `workspace/rules/outer_join_ast.py`:
  * `rewrite_outer_joins(sql)` 主入口
  * 解析 FROM（逗号分隔多表）+ WHERE 中 (+) 标记
  * 2 表 + 单 (+) 完整：`A.x = B.x(+)` → `A LEFT JOIN B ON ...`
  * 3 表简化：涉及 2 表的 (+) 做 LEFT JOIN，第 3 表 CROSS JOIN
  * 字符串/注释豁免（与 P2-2 一致）
  * 支持任意深度嵌套括号（NVL(SC.SCORE)）
  * GROUP BY/HAVING/ORDER BY 保留（_find_where_end 切分 WHERE 子句）
- **新建** `workspace/tests/test_outer_join_conversion.py`：14 个测试
  * 7 黄金样例：基础 LEFT JOIN / 子查询 / 嵌套子查询 / 混合 LEFT+INNER /
    LEFT JOIN + 额外过滤 / RIGHT JOIN 语义 / 多 SELECT
  * 6 单元：无 FROM / 无 (+) 不动 / 字符串/注释豁免 / 空 SQL / 多语句
  * 1 oracle_poc 端到端
- **改动**：
  * `scripts/convert_oracle_poc.py`：新增 `post_pass_outer_join()` 后处理
    对 FUNCTION/PROCEDURE/PACKAGE/PACKAGE_BODY/TRIGGER/VIEW/TYPE 跑改写
- **实施过程修复**：
  * FromTable dataclass 未 hashable → `eq=False` + 改用 `is` 比较
  * `_FROM_WHERE_RE` 贪婪跨多条 SELECT → 用 `[^();]` 排除顶层 `;`
  * 嵌套括号 `(NVL(SC.SCORE, 0))` 被正则截断 → 用 `(?:[^()]|\([^()]*\))*` 允许一层嵌套
  * GROUP BY/HAVING 被丢失（原 after_where 未追加到 new_block）
    → 修复 new_block 拼接 after_where，并加空格分隔（`= 0 GROUP BY ...`）
- **实测 oracle_poc 端到端（PG 15，DoD-6）**：
  * convert: 12/12 成功, 1 dropped（TRG_SCORE_COMPOUND 显式登记）
  * **`(+)` 计数: 2 → 0** ✓ 验收达成
  * 改写样例：
    `FROM STUDENT_MST S, SCORE_REC SC WHERE S.STUDENT_NO = SC.STUDENT_NO(+)
     AND S.GRADE = i_nGRADE ... GROUP BY S.STUDENT_NO HAVING ...`
    →
    `FROM STUDENT_MST S LEFT JOIN SCORE_REC SC ON S.STUDENT_NO = SC.STUDENT_NO
     WHERE S.GRADE = i_nGRADE ... GROUP BY S.STUDENT_NO HAVING ...`
  * L3: 172/148/24（与 P2-2 一致，**无回归**）
  * L3.5: 37 pass / 5 fail（与 P2-2 一致）
  * 总体分 84.5（P2-2 84.4，+0.1）
  * 残留扫描器: 97 → 95（2 处 (+) 命中规则被清除）
- **P2-3 未涉及（后续任务）**：
  * 跨多组 (+)、OR 条件混合、复杂 N 表拓扑排序留待后续
  * 实施路径 §P2-3 提到 "Kahn 拓扑"——本期 oracle_poc 实际只 2 表场景,
    未触发完整拓扑排序；3 表用 CROSS JOIN 兜底（简化）
- **单测**：87/87 通过（P1-1: 9 + P1-2: 4 + P1-3: 7 + P1-4: 22 + P2-1: 12 + P2-2: 19 + P2-3: 14）
- **下一步**：P2-4 序列/同义话语义治理（实施路径 §四 阶段二 P1）。

### 2026-10-09（P2-4 完成 · commit `9fa0fef`）
- **新建** `workspace/rules/sequence_synonym.py`:
  * `identify_sequences(sql)`: 从 SEQUENCE 文件提取序列名（大写归一）
    忽略 `--` / `/* */` 注释中的假匹配
  * `rewrite_sequence_synonym_views(sql, sequences)`: 改写 SYNONYM 文件
    * 表同义词（target 不是序列）：保持 VIEW 不变
    * 序列同义词（target 是序列）：改写为 `SELECT nextval('y') AS next_value`
      （PG 序列不是表，`SELECT * FROM seq_x` 语法错误）
- **新建** `workspace/tests/test_sequence_synonym.py`：12 个测试
  * 5 单元（identify）：单/多/大小写/忽略注释/空
  * 6 单元（rewrite）：表不动 / 序列改写 / 混合 / 大小写 / 空 / 保留周围
  * 1 oracle_poc 端到端
- **改动**：
  * `scripts/convert_oracle_poc.py`：新增 `post_pass_sequence_synonym()` 后处理
    从 SEQUENCE 文件建序列集合，对 SYNONYM 文件跑改写
- **实测 oracle_poc 端到端（PG 15，DoD-6）**：
  * convert: 12/12 成功, 1 dropped（TRG_SCORE_COMPOUND 显式登记）
  * **序列同义词: 3 处改写为 nextval()** ✓
    - `sm_seq_student`: `SELECT * FROM seq_student_no` → `SELECT nextval('seq_student_no') AS next_value`
    - `sm_seq_jobnum`: `SELECT * FROM seq_jobnum` → `SELECT nextval('seq_jobnum') AS next_value`
    - `sm_seq_log`: `SELECT * FROM seq_log_id` → `SELECT nextval('seq_log_id') AS next_value`
  * 表同义词保持不变（8 个）：`SELECT * FROM student_mst / score_rec / ...` 仍可用
  * L3: 172/148/24（与 P2-3 一致，**无回归**）
  * L3.5: 37 pass / 5 fail（与 P2-3 一致）
  * 总体分 84.5（P2-3 84.5，持平）
- **未涉及（后续任务）**：
  * MAXVALUE 策略开关：已有 'MAXVALUE capped' 警告在引擎里，本期不改行为
    （属业务决策项，留 P2-4 二期 / 业务方确认）
- **阶段二收官**：P2-1 ~ P2-4 全部完成（4/4）
- **单测**：99/99 通过（P1-1: 9 + P1-2: 4 + P1-3: 7 + P1-4: 22 + P2-1: 12 + P2-2: 19 + P2-3: 14 + P2-4: 12）
- **下一步**：阶段三 P3-1 AST 适配器（包装 workspace grammar 副本，0.5 天）。

### 2026-10-09（P3-1 完成 · commit `a73f2b4`）
- **新建** `workspace/parsers/grammar_adapter.py`:
  * `ParseResult` dataclass: ok / tree / parser / errors
  * `parse_plsql(sql)`: 输入 PL/SQL 源码，返回 `ParseResult`
  * **永不抛错**: 所有 ANTLR 异常 wrap 成 errors 字段（降级策略）
  * 环境检测: antlr4 runtime 不可用时返回 degraded
  * 输入校验: None / 非 str / 空输入都返回 degraded
  * 用 `ErrorListener` 收集语法错误（避免 stdout 污染）
  * 根 rule: `Sql_script`（社区 grammar 4.13.2 实际根）
- **新建** `workspace/tests/test_grammar_adapter.py`：8 个测试
  * 3 正常解析: PROCEDURE / FUNCTION / PACKAGE SPEC
  * 3 异常处理: 垃圾 SQL / 空输入 / None 输入（不抛错）
  * 1 ParseResult 字段检查
  * 1 oracle_poc 端到端（实际文件能跑通或 degraded）
- **落点（按 AGENTS.md §2.1）**：
  * 新能力: `workspace/parsers/grammar_adapter.py`
  * 不修改转换引擎（P3-1 只建基础设施，不接进主路径）
- **实测**：
  * oracle_poc 端到端: 实际 PROCEDURE 文件能被解析或 degraded（不抛错）
  * 单测: 107/107 通过
  * 无需重转产物（P3-1 是基础设施，不改输出）
- **下一步**：P3-2 结构体检器（对象数 / 嵌套深度 / 空壳检测）。

### 2026-10-09（P3-2 完成 · commit `1dcfb01`）
- **新建** `workspace/parsers/structure_check.py`:
  * `StructureReport` dataclass: object_counts / max_nesting_depth / stubs / percent_type_count / ast_available
  * `check_structure(sql)`: 主入口，返回 `StructureReport`
  * **4 项能力**：
    1. 对象数比对: 数 CREATE FUNCTION/PROCEDURE/TRIGGER/VIEW/TABLE/SEQUENCE/TYPE/INDEX/SYNONYM
    2. 嵌套深度: 数 BEGIN...END 块的最大深度（粗略）
    3. 空壳/桩检测: body 仅含 RAISE EXCEPTION 'not implemented' 或空 body
    4. %TYPE 引用溯源: 残留 %TYPE 数（P2-2 后应为 0）
  * AST 尝试（P3-1 适配器），失败时降级为 regex 路径
  * 不抛错——所有内部异常 wrap 成 StructureReport 字段
- **新建** `workspace/tests/test_structure_check.py`：10 个测试
  * 3 对象数：单函数 / 多类型 / PROCEDURE
  * 1 嵌套深度
  * 3 空壳检测：RAISE EXCEPTION / 空 body / 非桩函数不被误判
  * 2 %TYPE：残留 / 健康文件应为 0
  * 1 oracle_poc 端到端：13 个产物文件全部通过（%TYPE 残留=0）
- **落点（按 AGENTS.md §2.1）**：
  * 新能力：`workspace/parsers/structure_check.py`
  * 不修改转换引擎（P3-2 是基础设施）
- **实测**：
  * oracle_poc 端到端：13 个产物文件全部通过（%TYPE 残留=0）
  * 单测：117/117 通过
  * 无需重转产物（P3-2 是基础设施）
- **下一步**：P3-3 元数据提取（触发器 / 类型 / 函数结构元数据）。

---
### 2026-10-09（P3-4 完成：复合触发器结构化拆分）
- **背景**：P1-1 已让 6 个触发器全部出现在产物中，但复合触发器 `TRG_SCORE_COMPOUND`
  仍被 `continue` 掉（PG 无原生等价物），只在 warnings 里登记。P3-4 实现拆分。
- **改动**：
  - 新建 `workspace/converter/compound_trigger.py`：解析声明节 + 各时机点，
    按实施路径 §六默认决策拆为多个普通触发器（`<名>_BR` / `<名>_AS`），
    跨时机点状态用 session GUC 承载
  - `workspace/converter/__init__.py` 补建（此前缺失，会导致引擎导入失败）
  - `ora2pg_py_perlstyle/perlstyle_engine.py`：`convert_trigger` 接入拆分器
    （**延迟导入 + try/except**，引擎不硬依赖 workspace，拆分失败降级为显式 warning）
  - `workspace/converter/integrity.py`：完整性比对**剥掉拆分后缀**还原原名
  - 新增 `workspace/tests/test_compound_trigger.py`（22 用例）
  - `workspace/tests/test_integrity_gate.py`：P1-1 时期"复合触发器应被报为丢失"的
    断言已过时（P3-4 修好后不该再报），改为断言"已拆分不报丢失"+"门禁仍能抓真丢失"
- **实测**：
  - 转换门禁 `dropped_objects` **清零，exit=0**（DoD-7 达成）
  - 产物 `TRIGGER/00_TRIGGER.sql`：6/6 触发器，复合触发器拆出 2 个 PG 触发器
  - **L3 安装：TRIGGER 10 → 14/14 全装上**（+4），其余制品**零回归**
  - **PG 实装验证通过**：拆分产物在真实 PG 15 上 CREATE 成功且 INSERT 真触发
  - 全量单测 165 passed
- **踩到并修掉的 4 个 bug**（均有回归用例）：
  1. 头部正则只认 `CREATE ... COMPOUND TRIGGER`，但源是 `COMPOUND` 在**中间**的写法
  2. `PERFORM set_config(...)` 被渲染进 DECLARE 段 → PG 报"语法错误 在 set_config"
  3. `RAISE NOTICE` 改写时 `DBMS_OUTPUT.PUT_LINE('x' || v || 'y')` 多留一个右括号
     → 改用**配对括号扫描**而非正则（正则非贪婪遇嵌套括号不可靠）
  4. 声明节变量未搬进 DECLARE → `v_count` 首调报"column does not exist"（装上即坏）
- **分数说明**：总分 84.5 → 84.1（-0.4）。
  **不是回归** —— 基线在 P1~P3 各项修复后已从 86.6 降到 84.5（HEAD 实测）。
  L3 安装数全面持平/提升，pglast 解析 OK；L2 分数变化源于文件变长后的归一化效应。
- **已知缺口（如实登记）**：集合下标赋值 `v_scores(i).FIELD := ...` 在 PG 无直接等价物，
  已转成**显式人工复核标记**（不静默、不假装转换成功），后续需人工确认业务语义。
- **副作用**：探针文件已用可恢复删除清理；`pipeline/` 0 改动。

### 2026-10-09（P3-3 完成 · commit `2d59507`）
- **背景**：P3-1/P3-2 已接 AST，但 `workspace/parsers/metadata.py` **仍是纯正则**——
  这正是 P3-3 该用 AST 的核心价值所在。实测旧版触发器提取 **5/6，漏 `TRG_SCORE_COMPOUND`**（复合触发器）。
- **改动**：`metadata.py` 重写为 AST 优先 + 正则降级；补 `workspace/parsers/__init__.py`；
  新增 `workspace/tests/test_metadata.py`（**24 用例全绿**）。
- **产出（真实语料实测）**：
  | 语料 | 结果 |
  |---|---|
  | TRIGGER | **6/6 全字段正确，全部走 AST**（含 COMPOUND 与 INSTEAD OF）|
  | TYPE | 8 个类型 / 列与 MEMBER 方法正确（AST 在 L31 连锁失败 → 如实标记 degraded 并降级正则）|
  | FUNCTION | 9 个函数，参数方向词与 DEFAULT 均已剥离 |
  | PROCEDURE | 5 个 |
- **修掉的 5 个真实 bug**（均有回归用例锁定）：
  1. `INSTEAD` 与 `OF` 被 grammar 拆成两个 terminal → 单 token 判断取不到时机
  2. `_TYPE_RE` 写死 `AS\s*\(`，但源是 `AS OBJECT (` → **8 个对象类型只认出 4 个**
  3. `_balanced_body` 深度从 0 起算（开括号已被正则消费）→ `VARCHAR2(1000)` 的右括号被误判闭合，
     **7 列的类型只认出 1 列**
  4. 行内注释 `-- 计数` 吞掉类型 → 列解析失真
  5. 参数解析假设"类型在前"，但源是 `i_nSTUDENT_NO IN NUMBER`（名字在前）→ 名字被当类型
- **ANTLR 陷阱（已写入代码注释）**：`ctx.getText()` **会去掉所有空白**，
  `MEMBER FUNCTION GET_ID` → `MEMBERFUNCTIONGET_ID`，正则**不能用 `\s+`**。
- **降级纪律**：AST 解析失败时回退正则并在 `MetadataResult.degraded=True` 如实标记，
  **不静默返回空**（避免重演"静默丢失"老问题）。
- **P3-2 回归正常**：TRIGGER 6 / FUNCTION 10 / TYPE 11 计数正确。
- **副作用**：`oracle_poc/` 产物、`ora2pg_py_perlstyle/` 引擎、`pipeline/` 均 **0 改动**。
- **无需重转产物**（P3-3 是 AST 层基础设施，不改转换行为）。
- **下一步**：P3-4 触发器转换结构化升级——用 P3-3 提供的准确元数据
  （6/6 触发器的 timing/events/table/compound）驱动转换；改动落
  `ora2pg_py_perlstyle/perlstyle_engine.py`（`convert_trigger` 目前只转第一个）。

### 2026-10-12（人工测试手册 · 文档同步）
- **动作**：未修改任何代码或产物；用真实数据完成一轮手工功能测试，
  并以该轮结果为依据补充人工测试文档。
- **真实数据实测（PG 15）**：
  - 全量单测：165/165 通过（32.6s）
  - 残留扫描：critical=0 / major=95 / minor=0；exit=1
  - L3 安装：152/176（24 fail：PACKAGE_BODY 12 + FUNCTION 7 + PROCEDURE 4 + PACKAGE 1）
  - L3.5 首调烟测：37 pass / 5 fail（**5 fail 全部 SELF_xxx 伪标识符**、同一根因、
    对应 P4-4）
  - Gate：总分 **84.1 / C**，未通过
- **95 处残留按规则分布**（实测）：
  - TODO 块/行注释 28（块 16 + 行 12）— **P4-2**
  - SQLERRM 11 + SQLCODE 10 — **P4-3**
  - INLINED 8 — **P4-2 / P4-4**
  - EXECUTE IMMEDIATE 6 + UTL_FILE 6 + %NOTFOUND 6 — **P4-3**
  - %ISOPEN 4 + BULK COLLECT 4 + FORALL 4 — **P4-3**
  - SQL%ROWCOUNT 3 + FROM DUAL 3 + PIPE ROW 2 — **P4-3 / 规则遗留**
- **新发现实证**：`PACKAGE / PACKAGE_BODY` 的 `F_GET_SCORE_LIST` 冲突（“不能改变
  已经存在的函数的返回值类型”）—— **SPEC 与 BODY 同名函数返回类型不一致**，
  被旧基线（24 fail）归为 FUNCTION 类，本次单独出现于 PACKAGE，是
  关键技术决策 #4（SPEC 桩遮蔽 BODY）要防的实证。
- **副作用**：跑验证脚本**覆盖**了 `_verification_report.md` 与
  `_verification_results.json`，**事后已 `git checkout` 还原**；工作树干净，
  HEAD 仍为 `736840c`（P3-4）。
- **文档同步**（本期不建任务、不 commit 产物）：
  - **新增**：`docs/人工测试方案_2026-10-12.md`（9178 B）— 人工测试权威手册，
    逐个 DoD 说明执行步骤、通过条件与失败记录格式
  - **追加**：`oracle_poc/README.md` 末尾《人工测试入口》区块
    （3 段快速命令 + 当前基线摘要 + 权威手册链接）
  - **更新**：`HANDOFF.md` 顶部插入《⚡ 人工测试入口》导航区，
    §6 追加本条目
- **下一步**：不动代码/产物；交人工按 `docs/人工测试方案_2026-10-12.md`
  执行一轮人工验证，结果填入 `/tmp/manual_test_20261012.md`，
  之后再决定 P4-1 / P4-2 开工顺序。

### 2026-10-12（人工测试执行 · 详报）
- **执行人**：AI agent（按 `docs/人工测试方案_2026-10-12.md` 逐 DoD 跑）
- **环境**：PG 15.19、库 `manual_test_20261012`（临时）、scram-sha-256（密码 `postgres`）
- **隔离**：产物 `00_CREATE_ALL_PG.sql` 不带 schema 名前缀 → 默认装到 `public`
- **原始日志**：`_tmp_manual_test_log.md`（已合并入本条；测试库已 DROP）
- **逐 DoD 结果**：

| DoD | 状态 | 详情 |
|---|---|---|
| DoD-1 一键安装 | ❌ FAIL | 1 个 FUNCTION 第 111 行 "i_vsql" 错；`ON_ERROR_STOP` 未生效，后续 31 FUNC + PACKAGE_BODY 全跳 |
| DoD-2 残留扫描 | ❌ FAIL | critical=0 / major=95 / minor=0；exit=1 |
| DoD-3 冒烟调用 | ⚠️ 部分 | **5 SELF_xxx fail 全部复现**（与 HANDOFF §1.1 精确吻合）；§3.2.2 其他对象因 §1 装不上测不到 |
| DoD-4 业务等价 | n/a | 未跑（§1 失败连锁导致下游中断）|
| DoD-5 Gate | n/a | 未跑（会覆盖报告）|
| DoD-6 流程纪律 | ✅ PASS | 文档 commit 不触发重转 |
| DoD-7 对象数 | ✅ PASS | dropped_objects=0 |

- **SELF_xxx 5 fail 复现确认**（与 HANDOFF §1.1 表一一对应）：
  1. `GET_STATUS_of_SCORE_RECORD()` → `字段 self_score_record_is_pass 不存在`
  2. `GET_LETTER_GRADE_OF_SCORE_RECORD()` → `字段 self_score_record_score 不存在`
  3. `GET_AGE_of_STUDENT_RECORD()` → `字段 self_student_record_birth_date 不存在`
  4. `GET_GENDER_NAME_of_STUDENT_RECORD()` → `字段 self_student_record_gender 不存在`
  5. `GET_RANK_STR_of_STUDENT_RECORD()` → `字段 self_student_record_rank_no 不存在`
- **§3.2.2 复现确认**（与 HANDOFF §1.1 表逐条一致）：
  - `F_GET_TOTAL_SCORE(10001)` → **-1** ✅（现象一致）
  - 其余 object 因 §1 报错未装上未能验证

- **🔴 新发现 3 个缺陷**：

| # | 缺陷 | 现象 | 修复路径 |
|---|---|---|---|
| **Defect-1** | 产物无 schema 隔离 | `00_CREATE_ALL_PG.sql` 不设 search_path，所有对象装到 public | 生成器注入 `SET search_path = <schema>;` 或加 schema 前缀 |
| **Defect-2** | 单语句错"链锁中断" | `\ir` 嵌套文件中 `ON_ERROR_STOP` 不生效；后续 FUNCTION/PACKAGE 全跳 | 生成器加 `BEGIN ... EXCEPTION ... END;` 或子文件独立 ON_ERROR_STOP |
| **Defect-3** | F_GET_TOTAL_SCORE 返 -1 | （已知）| P4-2 / P4-4 |

- **副作用清理**：
  - 测试库 `manual_test_20261012` 已 DROP（`pg_terminate_backend` 后 DROP）
  - public schema 产物已卸装（所有触发器/函数/视图/表/类型/序列 0 事件后）
  - 产物文件 `_verification_report.md` **未变**（diff `/tmp/_vr_backup.md` 一致）
  - 临时脚本 `_tmp_obj_count.py` / `_tmp_cleanup.psql` / `/tmp/manual_install.log` 已删

- **结论 & 下一步**：
  - **DoD-1 不能装 → DoD-3/5 测不全** — Defect-1/2 是**产物层小改**（不动转换引擎），
    修后可补齐 §3 测不全的部分。**优先级：P4-1 之前先修**
  - P4-2 / P4-3 / P4-4 仍为原计任务，只在 Defect-1/2 后起手

### 2026-10-12（D0 阶段完成：Defect-1 + Defect-2 修复）
- **背景**：人工测试发现 Defect-1（产物无 schema 隔离）+ Defect-2（psql `\ir` 嵌套 ON_ERROR_STOP 不生效），
  两个均为**产物安装层小改**（不动转换引擎），按计划起手 D0 阶段。
- **改动**：
  - `scripts/convert_oracle_poc.py:build_pg_driver`：驱动脚本头部加 `CREATE SCHEMA IF NOT EXISTS :schema; SET search_path :schema;`（Defect-1，默认 `oracle_poc_verify`）
  - **新增** `scripts/install_oracle_poc.py`（6728 B）：per-file psql 调用，set search_path 到 -v schema，默认错 STOP（--keep-going 收集全部错误），提供 drop 重置
  - **新增** `workspace/tests/test_install_oracle_poc.py`（11 单测）：find_psql / ordered_sql_files / run_psql / cmd_install 全部路径覆盖
- **实测验证（PG 15）**：
  - `install_oracle_poc.py install --keep-going --schema oracle_poc_test_d0`：
    6 文件 OK（TABLE×2 + SEQUENCE + TYPE + VIEW + TRIGGER）/ 4 文件 FAIL（FUNCTION / PROCEDURE / PACKAGE / PACKAGE_BODY）/ 2 文件 OK（SYNONYM + DATA）
    → **不再链锁中断**，每个错误能精确定位（vs 之前 `"未提供"` 假成功 152/176）
  - 默认（不加 --keep-going）失败立刻 STOP，exit=1
  - 验证原 validate 脚本：L3 152/176 + L3.5 37/5 + Gate 84.1/C 仍与基线一致（**无回归**）
  - 重转产物：dropped_objects=0、残留 95 major（与基线一致）、DoD-7 仍 PASS
- **单测**：176/176 通过（原 165 + 新 11）
- **副作用**：PG 测试 schema `oracle_poc_test_d0` 已 DROP（57 个对象递回删）；产物报告仅 `_conversion_report.json` 内容变（重转正常），`_verification_*` 未变
- **下一步**：P4-1（Agent 工具链）正式起手（阶段三有 D0 阶段作前置）

### 2026-10-12（P4-1 完成 · Agent 工具链基础设施）
- **背景**：人工测试发现 Defect-1/2 后，先完成 D0；现按实施路径 §P4-1 推进 Agent 工具链。
- **决策**（与 `pipeline/` 对比）：
  - LLM provider = ollama（pipeline 一致）
  - model = `qwen3.8:27b`（pipeline 默认）
  - temperature = 0（项目硬约束：AI 可复现）
  - **不引入 Strands SDK**（pipeline 强绑定，难调试；我们用纯 Python 编排）
- **新增**（8 模块 / 1 CLI）：
  - `workspace/agent/config.py`（60）：配置 dataclass，环境变量覆盖
  - `workspace/agent/quota.py`（60）：全局配额（persistent、= 200 上限、QuotaExceeded）
  - `workspace/agent/llm_client.py`（130）：OllamaClient（cache + retry + quota）
  - `workspace/agent/validate_pg.py`（200）：强化版硬校验（5 项正则 + 真 PG 试装）
  - `workspace/agent/chunker.py`（80）：按函数边界切块（栈式计数，不粗略）
  - `workspace/agent/prompt_builder.py`（90）：SYSTEM_PROMPT（中文 + 28 处 TODO 特征）+ extract_code
  - `workspace/agent/snapshot.py`（70）：git 快照（create/restore）
  - `workspace/agent/orchestrator.py`（200）：主流程编排（LLM → 校验 → 替换 / 占位）
  - `scripts/agent_refill_todos.py`（200）：CLI 入口（P4-2 起手接入点）
- **单测**：50/50 通过（test_llm_client 16 + test_agent_components 34）
- **全量**：226/226（原 176 + 新 50）
- **DoD-6 验证**：重转产物 12/12 成功、dropped_objects=0、安装 6/12（与 P3-4 基线一致，**无回归**）
- **未接主路径**：orchestrator 可单独跑测试；CLI 默认 `--dry-run`，**不写文件**
  - P4-2 阶段：启用 `--write-back`，实现真实 TODO 转换
- **关键技术决定**（固化在代码）：
  - 失败必有 `RAISE EXCEPTION 'not implemented'`（不静默 NULL）
  - BEGIN/END 嵌套感知（栈式状态机，非粗略正则）
  - $$ 不剥（PG 里是函数体界）
  - 真 PG 试装用事务 + ROLLBACK（不留痕）
- **下一步**：P4-2（TODO 桩 28 处 Agent 重写，**关键路径**）

---

## 7. 已知风险与对策（实施路径 §六 摘要）

| 风险 | 对策 | 触发时回退 |
|---|---|---|
| ANTLR 官方 grammar 对本语料解析失败率高 | T7 预留排障缓冲；失败段落降级"文本级+Agent"双通道 | 退回 T8/T9 文本级 |
| Agent 产码质量不稳 | validate_pg 硬校验 + 不过必留 TODO 进人工清单 | 拆分 chunk / 换策略 |
| SPEC 桩遮蔽 BODY 失败 | 桩改为 `RAISE EXCEPTION 'not implemented'` | — |
| 修复规则引发老项目（gkb）回归 | 所有规则改动挂 golden 测试（T15） | revert 规则 commit |
| 流程纪律违反 | DoD-6 强制 + `verify_oracle_poc_output.py` 加产物 mtime 检查 | — |
| 业务侧不能拍板 H 类决策 | 决策项登记到 `docs/迁移注意事项.md` 不阻塞 | 等待业务 |

---

## 8. 外部资源需求

| 资源 | 数量 | 阻塞任务 |
|---|---|---|
| **PostgreSQL 15.19 实例**（目标即 15，非 8.4/13——见实施路径 Final v3 v3.4）| 1 | 全部 |
| AI Agent 调用额度（T11 用 60–200 次，含 retry/错杀回滚）| 一份 | T11 |
| 业务方对 5 项 H 类决策的确认 | UTL_FILE / GTT / MAXVALUE / REF CURSOR / 5 个丢失触发器 | T6/T8/T12 |
| **Oracle 实例（可选）** | 1 | **P5-1 / DoD-4**——见 §6「D4」：65 条 golden 用例期望值需实测复核 |
| 回归人天（gkb 全量 + 其他语料）| 5–10 人天 | T21 / T26 / P5-3 |

---

*本文件 2026-10-09 整合重写，**2026-10-13 按代码级对照评审同步**（实施路径 Final v3）。*
*下一接手人阅读顺序：**本文件**（尤其 §2.1.1 与 §2.2.1）→ `docs/最终实施路径_生产级_2026-10-09.md`（唯一权威，看文末 Final v3 修订记录）→ `docs/整合任务计划_2026-10-13.md` → `AGENTS.md` → 再行动。*
*⛔ `docs/改进任务书_达到可用状态_2026-10-09.md` 已归档，勿据其实施。*
