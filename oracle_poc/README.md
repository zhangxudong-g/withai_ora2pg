# Oracle PL/SQL 业务模拟系统

> 用于 Oracle → PostgreSQL 转换测试的学生成绩管理系统

## 🚀 你是谁？要看哪里？

| 你是 | 你要做什么 | 从哪里开始 |
|---|---|---|
| 🎯 **手动测试者**（业务/转测） | 装 PG、调函数、看返回对不对 | **[§ 人工测试 30 秒上手](#-30秒上手)** → `docs/人工测试方案_2026-10-12.md` |
| 🔧 **转换开发者**（改规则） | 改完规则想知道产物怎么变 | **[§ 改完规则怎么验](#-改完规则怎么验)** → `HANDOFF.md` §4 |
| 📦 **任务接手者**（接 P4 / 跳任务） | 哪里未做 / 本为 P0 / 落点纪律 | **`HANDOFF.md` 顶部三条** → `docs/最终实施路径_生产级_2026-10-09.md` |

---

## 📋 三个动作，各司其职

这是整个项目的核心命令表——**所有任务（手测 / 改规则 / 验收）都从这三条出发**。

| # | 动词 | 脚本 | 一行命令 | 副作用 / 注意 |
|---|---|---|---|---|
| ① | **转换**：Oracle 源 → PG 产物 | `scripts/convert_oracle_poc.py` | `python scripts/convert_oracle_poc.py --clean` | **会覆盖** `oracle_poc/output/` 下所有产物 |
| ② | **安装**：把产物装到真 PG | `scripts/install_oracle_poc.py` | `PGPASSWORD=postgres python scripts/install_oracle_poc.py install` | 创建/覆盖 schema；默认 schema `oracle_poc_verify`；可 `--schema xxx` 改 |
| ③ | **验证**：跑 Gate 报告 | `scripts/verify_oracle_poc_output.py` | `PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py` | **会覆盖** `oracle_poc/output/_verification_*.{md,json}`；需先备份 |

> **附加只读工具**：`scripts/scan_oracle_residuals.py oracle_poc/output` 扫产物里的 Oracle 关键字（DoD-2 门禁，无副作用）。

---

## 🎯 30 秒上手（手测者必读）

```bash
export PGPASSWORD=postgres   # PG 密码；Windows 中文环境还得 chcp 65001

# 装到 PG（错立刻停；6/12 文件能装上）
python scripts/install_oracle_poc.py install --schema manual_test

# 调个函数看看
psql -h localhost -U postgres -d postgres -c \
  "SET search_path TO manual_test; SELECT GET_SUBJECT_NAME(1);"
```

更详细的 DoD-1~7 手动核对 → [`docs/人工测试方案_2026-10-12.md`](../docs/人工测试方案_2026-10-12.md)

---

## 🔧 改完规则怎么验（开发闭环）

改完 `ora2pg_py_perlstyle/perlstyle_engine.py` 或 `workspace/` 下规则后，**必须**走完整闭环：

```bash
# 0. 单测（5 秒内，不会改产物）
cd D:/JIP/poc && python -m pytest workspace/tests/test_<your_task>.py -v

# 1. 转换：重转产物
python scripts/convert_oracle_poc.py --clean

# 2. 安装：装到 PG
PGPASSWORD=postgres python scripts/install_oracle_poc.py install --schema dev_test

# 3. 验证：跑 Gate
cp oracle_poc/output/_verification_report.md /tmp/_vr_backup.md      # 备份（脚本会覆盖）
PYTHONIOENCODING=utf-8 python scripts/verify_oracle_poc_output.py

# 4. 残留扫描
PYTHONIOENCODING=utf-8 python scripts/scan_oracle_residuals.py oracle_poc/output
```

### 副作用一览（必看）

| 脚本 | 会改/覆盖什么 | 是否阻塞输出 |
|---|---|---|
| `convert_oracle_poc.py` | `oracle_poc/output/` 12 个产物文件 + `_conversion_report.json` | 是——DoD-6 强制重转 |
| `install_oracle_poc.py install` | 真 PG 库（建/装 schema `oracle_poc_verify` 或 `--schema` 指定） | 是——但用 `--schema dev_test` 可隔离 |
| `verify_oracle_poc_output.py` | `oracle_poc/output/_verification_report.md` + `_verification_results.json` | **会覆盖 L3 真数据**；`--no-pg` 更会用降级数据覆盖，**且 Windows 中文环境会写 GBK 乱码**——**跑前备份 / 跑后 `git checkout` 还原** |
| `scan_oracle_residuals.py` | （只读）| 否 |

### 何时用 `--keep-going`

- 默认 `install_oracle_poc.py install`：**第一个错立刻停**（保护 L3 真基线）
- `--keep-going`：收集全错，用于"一次拿全 L3 数据"分析
- `verify_oracle_poc_output.py` 无此选项（其内部逻辑已自带收集）

---

## 📁 目录结构

```
oracle_poc/
├── 01.SQL/                # Oracle 源（13 类 / 55 个对象）
├── output/                # 转换后 PG 产物（12 文件）
│   ├── 00_CREATE_ALL_PG.sql  # 一键安装脚本（含 schema 隔离）
│   └── _conversion_report.json  # 转换记录（`dropped_objects` DoD-7 门禁）
└── HANDOFF.md             # 转换状态 / 实测基线 / 接任务指南
```

---

## 📊 当前实测基线（2026-10-12，PG 15，commit `d4119bd` / D0）

| DoD | 值 | 说明 |
|---|---|---|
| 总分 / Gate | 84.1 / C / **未通过** | 门槛 > A（≥90） |
| 残留扫描 | **95 处 major** | TODO 28 + SQLCODE/SQLERRM 21 + INLINED 8 + 复杂体 38 |
| L3 安装 | **152/176**（24 fail）| PACKAGE_BODY 12 + FUNCTION 7 + PROCEDURE 4 + PACKAGE 1 |
| L3.5 冒烟 | **37 pass / 5 fail** | 5 fail 全部 SELF_xxx 伪标识符 → P4-4 |
| 完整性 | **dropped_objects=0** | DoD-7 ✓ |
| 单测 | **226/226** | P1~P3+D0+P4-1 全部 |

---

## 📊 代码统计（`01.SQL/`）

| 类型 | 数量 | 行数 |
|---|---|---|
| 表 | 9 | 178 |
| 序列 | 6 | 63 |
| 同义词 | 13 | 18 |
| 类型 | 8 | 166 |
| 视图 | 7 | 215 |
| 触发器 | 6 | 171 |
| 函数 | 10 | 940 |
| 存储过程 | 5 | 371 |
| 包（SPEC+BODY） | 1 | 1508 |
| 测试数据 | — | 162 |
| **总计** | **55** | **3855** |

---

## Oracle 特有特性 / 转换复杂度

| 特性 | 数量 | PG 转换难点 |
|---|---|---|
| SEQUENCE | 6 | ⚠️ 生成策略 |
| SYNONYM | 13 | ❌ 需替换 |
| OBJECT TYPE | 8 | ⚠️ SELF/MEMBER 重写 |
| NESTED TABLE | 4 | ⚠️ 数组转换 |
| REF CURSOR | 3 | ❌ SETOF |
| TRIGGER | 6 | ⚠️ 语法差异 |
| PACKAGE | 1 | ❌ SCHEMA |
| PIPELINED | 2 | ⚠️ RETURNS SETOF |
| BULK COLLECT / FORALL | 4 | ❌ 循环替代 |
| 自治事务 | 1 | ❌ 需重构 |
| **多层嵌套** | **4 层** | ❌ lift 子程序 |

---

## 版本信息

- 版本: 1.0.200
- 创建日期: 2024
- 作者: POC Team
- 许可证: MIT