# generate_baselines.py 用法说明

**脚本路径**: `scripts/generate_baselines.py`
**功能**: 静态基线记录生成器 — 从案例 README.md 提取信息, 按 `BASELINE_TEMPLATE.md` 格式批量生成基线记录 (无需 PostgreSQL 环境)
**配套脚本**: `scripts/verify_cases.py` (PG 环境实测 V3-V8, 覆盖静态基线), `scripts/run_case_tests.py` (V1-V2 框架)

---

## 1. 概述

`generate_baselines.py` 解决 Phase 1 方案 A 中"基线记录只有模板没有实际记录"的缺口。脚本解析每个案例的 `README.md`, 提取 Oracle 源码 / PostgreSQL 转换 / 业务元数据, 按 [BASELINE_TEMPLATE.md](file:///d:/JIP/poc/fixtures/cases/_test_data/BASELINE_TEMPLATE.md) 渲染为完整基线记录。

### 与 `verify_cases.py --baseline` 的区别

| 维度 | `generate_baselines.py` | `verify_cases.py --baseline` |
|------|--------------------------|------------------------------|
| 环境 | 无需 PG | 需 PG 环境 |
| V1-V2 | 静态推断 (基于兼容性元数据) | 实际执行 |
| V3-V8 | 标记"未执行" | 实测填充 |
| 速度 | 快 (秒级) | 慢 (依赖 PG 查询) |
| 用途 | 批量补齐 30 个基线 | 单案例深度验证 |
| 覆盖关系 | 后续 `--all` 可被 `verify_cases.py --baseline NN` 覆盖 | 覆盖静态版 |

### 元数据来源

脚本内置 `CASE_META` 字典, 与 [run_case_tests.py](file:///d:/JIP/poc/scripts/run_case_tests.py) 的 `CaseRegistry.CASES` 保持一致, 包含 30 个案例的:
- `name` (中文名)
- `type` (DQL/DML/DDL/PL/SQL)
- `risk` (低/中/高)
- `compat` (✅ / ⚠️ / ❌)
- `complexity` (L1/L2/L3)

---

## 2. 前置条件

### 2.1 依赖

无外部依赖, 仅使用 Python 标准库 (`argparse`, `re`, `pathlib`, `datetime`)。

### 2.2 Python 版本

Python 3.7+ (使用 f-string, type hints, dataclass 兼容性)

### 2.3 文件结构要求

脚本依赖以下目录结构:

```
fixtures/cases/
├── 01_basic_select/
│   └── README.md          # 必须存在, 含 Oracle 源 + PG 转换 + 业务元数据章节
├── 02_multi_join/
│   └── README.md
├── ...
└── _test_data/
    ├── BASELINE_TEMPLATE.md  # 基线模板 (仅作参考, 脚本内置渲染逻辑)
    └── baselines/            # 输出目录 (脚本自动创建)
```

---

## 3. 命令行参数

| 参数 | 简写 | 类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--case <ID>` | `-c` | str | 无 | 生成指定案例基线 (如 `01`, `13`, `27`) |
| `--all` | 无 | flag | False | 强制重新生成所有案例 (覆盖已存在) |
| `--list` | `-l` | flag | False | 列出所有案例的基线状态 (不生成) |

> **注**: 不带参数运行时, 默认生成所有缺失的基线 (跳过已存在)。

---

## 4. 典型工作流

### 4.1 查看当前基线状态

```bash
python scripts/generate_baselines.py --list
```

输出示例:
```
[*] 基线目录: D:\JIP\poc\fixtures\cases\_test_data\baselines

#    名称                        风险     兼容     基线文件
----------------------------------------------------------------
01   基础 SELECT                 低      ✅      ✅ 01_basic_select_baseline.md
02   多表 JOIN                   低      ✅      ✅ 02_multi_join_baseline.md
...
30   Scheduled Job               高      ❌      ✅ 30_scheduled_job_baseline.md
```

### 4.2 生成单个案例基线

```bash
python scripts/generate_baselines.py --case 01
# 输出: [+] 案例 01: 生成 01_basic_select_baseline.md
```

### 4.3 批量生成所有缺失基线 (推荐首次使用)

```bash
python scripts/generate_baselines.py
# 输出: [+] 案例 01: 生成 01_basic_select_baseline.md
#       [+] 案例 02: 生成 02_multi_join_baseline.md
#       ...
#       [*] 完成: 生成 26 个, 跳过 4 个 (27/28/29/30 已存在), 缺失 0 个
```

### 4.4 强制覆盖重新生成

```bash
# 案例 README 修改后, 强制刷新所有基线
python scripts/generate_baselines.py --all

# 或仅覆盖单个
python scripts/generate_baselines.py --case 13 --all
```

### 4.5 与 PG 实测基线协作

```bash
# Step 1: 静态生成全部 30 个基线
python scripts/generate_baselines.py

# Step 2: 在 PG 环境中跑实测验证
python scripts/verify_cases.py --case 01 --level all
# (verify_cases.py 当前 --baseline 仅打印元数据, 不覆盖文件)

# Step 3: 案例更新后重新生成
python scripts/generate_baselines.py --all
```

---

## 5. 输出文件结构

每个基线记录文件按以下模板渲染 (8 大章节):

```
fixtures/cases/_test_data/baselines/NN_<name>_baseline.md
├── 1. 案例基本信息 (案例编号/源文件/转换工具/日期/类型/风险)
├── 2. Oracle 原始内容 (代码块/预期行为/输入条件/输出)
├── 3. 转换过程 (规则表/手工修正表/未转换内容表)
├── 4. PostgreSQL 转换结果 (代码块/差异说明表)
├── 5. 验证结果 (V1-V8 层次表 + 静态验证项)
├── 6. 风险评估 (风险清单表)
├── 7. 最终结论 (转换状态/一致性/可上线/遗留问题/复查日期)
└── 8. 变更历史 (生成日期记录)
```

### 文件命名规则

`<case_id>_<case_dir_name_suffix>_baseline.md`

| 案例 ID | 案例目录 | 基线文件名 |
|---------|----------|------------|
| 01 | `01_basic_select` | `01_basic_select_baseline.md` |
| 13 | `13_merge` | `13_merge_baseline.md` |
| 27 | `27_index` | `27_index_baseline.md` |
| 30 | `30_scheduled_job` | `30_scheduled_job_baseline.md` |

---

## 6. 静态验证状态判定规则

脚本根据 `CASE_META` 中的兼容性 (`compat`) 字段自动判定 V1 状态:

| 兼容性 | V1 语法验证 | 转换状态 | 业务一致性 | 可上线 |
|--------|-------------|----------|------------|--------|
| ✅ 兼容 | ✅ 通过 | 完全自动 | 一致 | 是 |
| ⚠️ 需转换 | ✅ 通过 | 部分自动 | 基本一致 | 需要人工确认 |
| ❌ 无兼容 | ⬜ 未执行 | 需要人工 | 存在差异 | 需要人工确认 |

V2-V8 一律标记为 `⬜ 未执行`, 注明"需 PG 环境"。后续由 `verify_cases.py --baseline NN` 覆盖生成。

---

## 7. README 解析逻辑

脚本通过正则表达式从案例 `README.md` 提取以下章节:

| 章节 | 正则 | 提取内容 |
|------|------|----------|
| 标题 | `^# (.+)$` | 案例中文名 |
| Oracle 源 | `## Oracle 源` 后第一个 ```` ```sql ```` 代码块 | Oracle 原始 SQL |
| PostgreSQL 转换 | `## PostgreSQL 转换` 后所有代码块 | PG 转换结果 |
| 验证方法 | `## 验证方法` 后的 `- [ ]` 列表 | V1 子项 |
| 业务目的 | `### 业务目的` 到下一 `###`/`##` | 业务描述 |
| 依赖信息 | `### 依赖信息` 后第一个 markdown 表格 | 依赖表/字段 |
| 转换评估 | `### 转换评估` 后第一个表格 | 需人工修正/说明 |

如案例 README 缺少某章节, 脚本填入占位符 `(见案例 README.md)`, 不报错。

---

## 8. 常见问题排查

### 8.1 `[!] 案例 NN: 目录未找到`

**原因**: `CASE_META` 中存在 ID 但 `fixtures/cases/NN_*/` 目录不存在。

**解决**:
- 检查目录命名是否为 `NN_<name>` 格式 (如 `01_basic_select`)
- 在 `CASE_META` 中补充该案例的元数据

### 8.2 `[!] 案例 NN: README.md 不存在`

**原因**: 案例目录存在但缺少 README.md。

**解决**: 创建 `fixtures/cases/NN_<name>/README.md`, 参考其他案例格式 (如 [01_basic_select/README.md](file:///d:/JIP/poc/fixtures/cases/01_basic_select/README.md))。

### 8.3 生成内容显示 `(见案例 README.md)`

**原因**: 案例 README 缺少对应章节 (业务目的/依赖信息/转换评估等)。

**解决**: 补充 README 中对应章节, 然后重新运行:
```bash
python scripts/generate_baselines.py --case NN --all
```

### 8.4 `--list` 显示某案例为 ❌

**原因**: 该案例基线文件未生成。

**解决**:
```bash
python scripts/generate_baselines.py --case NN
```

### 8.5 中文乱码 (Windows PowerShell)

```powershell
$env:PYTHONIOENCODING = "utf-8"
python scripts/generate_baselines.py
```

---

## 9. 相关文件

| 文件 | 说明 |
|------|------|
| [scripts/generate_baselines.py](file:///d:/JIP/poc/scripts/generate_baselines.py) | 本脚本 |
| [scripts/verify_cases.py](file:///d:/JIP/poc/scripts/verify_cases.py) | PG 实测验证 (V3-V8), 见 [用法说明](file:///d:/JIP/poc/docs/verify_cases_用法.md) |
| [scripts/run_case_tests.py](file:///d:/JIP/poc/scripts/run_case_tests.py) | V1-V2 框架 (无 PG) |
| [fixtures/cases/_test_data/BASELINE_TEMPLATE.md](file:///d:/JIP/poc/fixtures/cases/_test_data/BASELINE_TEMPLATE.md) | 基线记录模板 (参考) |
| [fixtures/cases/_test_data/baselines/](file:///d:/JIP/poc/fixtures/cases/_test_data/baselines/) | 输出目录 (30 个基线 + README 索引) |
| [fixtures/cases/_test_data/baselines/README.md](file:///d:/JIP/poc/fixtures/cases/_test_data/baselines/README.md) | 基线索引 (详细版 vs 静态版区分) |
| [fixtures/cases/README.md](file:///d:/JIP/poc/fixtures/cases/README.md) | 30 案例覆盖矩阵 |

---

## 10. 完整工作流示例 (新机器首次运行)

```bash
# Step 1: 列出当前基线状态 (查看缺失情况)
python scripts/generate_baselines.py --list

# Step 2: 批量生成所有缺失基线
python scripts/generate_baselines.py

# Step 3: 抽查单个基线内容
cat fixtures/cases/_test_data/baselines/01_basic_select_baseline.md

# Step 4: 修改某个案例 README 后, 强制覆盖该案例基线
python scripts/generate_baselines.py --case 13 --all

# Step 5: 修改所有案例后, 批量覆盖
python scripts/generate_baselines.py --all

# Step 6: 确认所有 30 个基线已生成
python scripts/generate_baselines.py --list
```

---

## 11. 设计说明

### 11.1 为什么需要静态版

| 场景 | 用静态版 | 用 PG 实测版 |
|------|----------|--------------|
| CI/CD 检查基线完整性 | ✅ | ❌ (PG 环境难获取) |
| 文档批量生成 | ✅ | ❌ (耗时) |
| 单案例深度验证 | ❌ (V3-V8 未实测) | ✅ |
| 性能基线对比 | ❌ | ✅ (V8) |

### 11.2 为什么不直接读 `BASELINE_TEMPLATE.md`

脚本内置渲染逻辑而非读模板文件, 因为:
1. 模板是 markdown 文档 (人读), 不是模板引擎语法 (机器读)
2. 内置 f-string 渲染速度快, 不依赖外部模板引擎 (如 Jinja2)
3. 模板更新时, 脚本逻辑需同步更新 (代码注释已标明对应模板章节)

### 11.3 元数据维护

`CASE_META` 字典需与以下来源保持一致:
- [run_case_tests.py](file:///d:/JIP/poc/scripts/run_case_tests.py) 的 `CaseRegistry.CASES`
- [verify_cases.py](file:///d:/JIP/poc/scripts/verify_cases.py) 的 `register_all_cases()`
- [fixtures/cases/README.md](file:///d:/JIP/poc/fixtures/cases/README.md) 覆盖矩阵

新增案例时, 需同步更新以上 4 处。

---

*文档版本: 1.0*
*最后更新: 2026-09-18*
*对应脚本版本: generate_baselines.py (含 30 案例 CASE_META)*
