# run_case_tests.py 用法说明

**脚本路径**: `scripts/run_case_tests.py`
**功能**: V1-V2 验证框架 — 列出/运行 Oracle → PostgreSQL 案例的语法与对象层验证 (无需 PostgreSQL 环境)
**配套脚本**: `scripts/verify_cases.py` (PG 环境 V3-V8 实测), `scripts/generate_baselines.py` (静态基线生成)

---

## 1. 概述

`run_case_tests.py` 实现 Phase 1 要求的验证层次 V1-V2, 通过案例发现 + README 解析完成轻量级验证, 不依赖 PostgreSQL 数据库。共注册 26 个案例 (01-22 原有 + 27-30 新增对象类型), 与 `fixtures/cases/NN_*/README.md` 一一对应。

### 验证层次

| 层次 | 名称 | 本脚本 | 说明 |
|------|------|--------|------|
| V1 | 语法验证 | ✅ | 检查案例 README.md 可读 + 案例已注册 |
| V2 | 对象验证 | ✅ (轻量) | 检查案例目录结构与 README 完整性 |
| V3-V8 | 结果/边界/异常/数据变更/事务/性能 | ❌ | 见 [verify_cases_用法.md](file:///d:/JIP/poc/docs/verify_cases_用法.md) |

### 与其他脚本的关系

| 脚本 | 环境 | 验证层次 | 速度 | 用途 |
|------|------|----------|------|------|
| `run_case_tests.py` | 无 PG | V1-V2 (轻量) | 毫秒级 | CI 检查、案例完整性验证 |
| `verify_cases.py` | 需 PG | V3-V8 (实测) | 秒-分级 | 业务行为深度验证 |
| `generate_baselines.py` | 无 PG | 静态基线生成 | 秒级 | 基线记录批量产出 |

---

## 2. 前置条件

### 2.1 依赖

无外部依赖, 仅使用 Python 标准库 (`argparse`, `pathlib`, `dataclasses`, `enum`, `datetime`, `time`)。

### 2.2 Python 版本

Python 3.7+ (使用 dataclass, type hints, f-string)

### 2.3 文件结构要求

```
fixtures/cases/
├── 01_basic_select/
│   └── README.md       # 必须存在, 用于 V1-V2 检查
├── 02_multi_join/
│   └── README.md
├── ...
└── 27_index/
    └── README.md
```

---

## 3. 命令行参数

| 参数 | 简写 | 类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--case <ID>` | `-c` | str | 无 | 运行指定案例 (如 `01`, `13`, `27`) |
| `--list` | `-l` | flag | False | 列出所有注册案例 (不运行) |
| `--report` | `-r` | flag | False | 运行所有测试并生成报告 (默认行为, 无参数时也运行) |
| `--risk <level>` | 无 | str | 无 | 按风险等级筛选 (low/medium/high) — 仅与 `--list` 配合 |

> **注**: 不带任何参数运行时, 默认执行全部 26 个案例并保存报告。

---

## 4. 典型工作流

### 4.1 列出所有注册案例

```bash
python scripts/run_case_tests.py --list
```

输出示例:
```
[*] Case List:

| # | Name | Risk | Complexity | PG Compatible |
|---|------|------|-----------|--------------|
| 01 | 基础 SELECT | low | L1 | Yes |
| 02 | 多表 JOIN | low | L1 | Yes |
...
| 27 | Index | medium | L2 | No |
| 28 | Constraint | medium | L2 | Yes |
| 29 | Sequence | low | L1 | Yes |
| 30 | Scheduled Job | high | L3 | No |
```

### 4.2 运行单个案例

```bash
python scripts/run_case_tests.py --case 01
```

输出:
```
案例 01: 基础 SELECT
状态: pass
耗时: 0.0ms
```

### 4.3 运行全部测试并生成报告

```bash
python scripts/run_case_tests.py
```

输出:
```
[*] Running tests...

# Oracle → PostgreSQL 典型案例测试报告

生成时间: 2026-09-18 16:27:53

## 概要
| 指标 | 数值 |
|------|------|
| 总案例数 | 26 |
| 通过 | 26 |
| 失败 | 0 |
| 跳过 | 0 |
| 通过率 | 100.0% |

## 详细结果
| # | 案例名称 | 状态 | 耗时 | 说明 |
|---|----------|------|------|------|
| 01 | 基础 SELECT | [PASS] | 0.0ms |  |
...

## 风险等级统计
| 风险等级 | 总数 | 已覆盖 | 覆盖率 |
|----------|------|--------|--------|
| high | 6 | 6 | 100.0% |
| low | 9 | 9 | 100.0% |
| medium | 11 | 11 | 100.0% |

[*] Report saved: D:\JIP\poc\fixtures\cases\_test_data\test_report.md
```

### 4.4 显式生成报告 (与默认行为等价)

```bash
python scripts/run_case_tests.py --report
```

### 4.5 与基线生成器协作

```bash
# Step 1: 用本脚本验证案例框架完整性
python scripts/run_case_tests.py

# Step 2: 用基线生成器批量产出基线记录
python scripts/generate_baselines.py

# Step 3: 在 PG 环境中跑深度验证
python scripts/verify_cases.py --case 01 --level all
```

---

## 5. 案例注册清单

脚本通过 `CaseRegistry.CASES` 字典注册 26 个案例:

| ID | 名称 | 类型 | 风险 | 复杂度 | PG 兼容 | 验证项 |
|----|------|------|------|--------|---------|--------|
| 01 | 基础 SELECT | DQL | low | L1 | Yes | 语法解析/结果行数/排序一致 |
| 02 | 多表 JOIN | DQL | low | L1 | Yes | 语法解析/结果行数/JOIN 类型 |
| 03 | 外连接 (+) | DQL | medium | L2 | No | (+) 语法移除/LEFT JOIN 正确/结果一致 |
| 04 | EXISTS / NOT EXISTS | DQL | low | L1 | Yes | 语法解析/逻辑正确 |
| 05 | UNION / UNION ALL | DQL | low | L1 | Yes | 去重正确/列数匹配/类型兼容 |
| 06 | GROUP BY / HAVING | DQL | low | L1 | Yes | 聚合结果/HAVING 条件 |
| 07 | 分析函数 | DQL | medium | L1 | Yes | 窗口分区/排序顺序/聚合结果 |
| 08 | ROWNUM / 分页 | DQL | medium | L2 | No | 分页数量/排序一致/TOTAL_CNT |
| 09 | 日期处理 | DQL | medium | L2 | No | 日期格式/SYSDATE 替换/时区处理 |
| 10 | NULL 处理 | DQL | low | L1 | Yes | NULL 替换/类型一致 |
| 11 | DECODE / NVL | DQL | medium | L2 | No | CASE 转换/映射正确/NULL 处理 |
| 12 | CASE 表达式 | DQL | low | L1 | Yes | WHEN 条件/THEN 返回值 |
| 13 | MERGE | DML | high | L3 | No | INSERT 正常/UPDATE 正常/冲突检测 |
| 14 | INSERT SELECT | DML | low | L1 | Yes | 列数匹配/约束验证 |
| 15 | UPDATE / DELETE | DML | medium | L2 | No | 影响行数/数据正确 |
| 16 | 动态 SQL | PL/SQL | high | L3 | No | 参数绑定/SQL 注入防护 |
| 17 | REF CURSOR | PL/SQL | high | L3 | No | 游标打开/数据返回/事务处理 |
| 18 | Function | DDL | medium | L2 | No | 创建成功/参数正确/返回值正确 |
| 19 | Procedure | DDL | medium | L2 | No | 创建成功/OUT 参数/游标遍历 |
| 20 | Package | DDL | high | L3 | No | Schema 替代/调用方式/全局变量 |
| 21 | 异常处理 | PL/SQL | medium | L2 | No | 异常捕获/错误码/资源释放 |
| 22 | 事务控制 | PL/SQL | high | L3 | No | 事务边界/锁定生效/提交回滚 |
| 27 | Index | DDL | medium | L2 | No | 索引创建/EXPLAIN 使用索引/函数索引/CONCURRENTLY |
| 28 | Constraint | DDL | medium | L2 | Yes | 约束创建/违规报错/DEFERRABLE 延迟/NOT VALID |
| 29 | Sequence | DDL | low | L1 | Yes | 序列创建/nextval 递增/currval 一致/CYCLE 循环 |
| 30 | Scheduled Job | DDL | high | L3 | No | pg_cron 调度/cron 表达式等价/DISABLE/ENABLE/时区正确 |

> **注**: 案例 23-26 (DBMS_SQL/Trigger/集合类型/性能测试) 仅在 `verify_cases.py` 注册, 不在本脚本中。如需统一, 可参考 [run_case_tests.py 第 89-376 行](file:///d:/JIP/poc/scripts/run_case_tests.py) 的注册格式补充。

### 风险等级分布

| 风险 | 数量 | 占比 |
|------|------|------|
| 🟢 low | 9 | 35% |
| 🟡 medium | 11 | 42% |
| 🔴 high | 6 | 23% |

---

## 6. 输出格式

### 6.1 单案例输出 (`--case NN`)

```
案例 NN: <案例名>
状态: <pass|fail|skip>
耗时: <X.X>ms
错误: <error_message>   # 仅 fail/skip 时显示
```

### 6.2 报告输出 (默认或 `--report`)

报告保存到: `fixtures/cases/_test_data/test_report.md`

报告结构:
```
# Oracle → PostgreSQL 典型案例测试报告
生成时间: YYYY-MM-DD HH:MM:SS

## 概要 (总案例/通过/失败/跳过/通过率)
## 详细结果 (逐案例状态表)
## 风险等级统计 (按 low/medium/high 分组覆盖率)
```

### 6.3 报告位置

- 测试报告: `fixtures/cases/_test_data/test_report.md` (由本脚本生成)
- 验证报告: `fixtures/cases/_test_data/verification_report.md` (由 `verify_cases.py` 生成)
- 基线记录目录: `fixtures/cases/_test_data/baselines/` (由 `generate_baselines.py` 生成)

---

## 7. 案例发现机制

脚本运行时通过 `run_case_test(case_id)` 函数查找案例:

```python
# 按案例 ID 前缀查找目录
prefix = f"{case_id.zfill(2)}_"  # 如 "01_"
for item in CASES_DIR.iterdir():
    if item.is_dir() and item.name.startswith(prefix):
        readme_path = item / "README.md"
        break
```

**判定逻辑**:
1. 案例必须在 `CaseRegistry.CASES` 字典中注册 (否则 SKIP, 错误: "Case NN not found")
2. 案例目录必须存在于 `fixtures/cases/` (否则 SKIP, 错误: "README.md not found")
3. 满足以上两条 → PASS (V1-V2 通过)

> **注**: 当前实现未实际解析 README 内容, 仅检查存在性。如需深度解析, 见 `generate_baselines.py` 的 `parse_readme()` 函数。

---

## 8. 常见问题排查

### 8.1 案例 SKIP, 错误 "Case NN not found"

**原因**: 案例未在 `CaseRegistry.CASES` 字典中注册。

**解决**: 在 [run_case_tests.py](file:///d:/JIP/poc/scripts/run_case_tests.py) 第 89-376 行的 `CASES` 字典中添加:

```python
"NN": TestCase(
    id="NN",
    name="案例名",
    category="DQL/DML/DDL/PL/SQL",
    oracle_feature="Oracle 特性",
    pg_compatible=True/False,
    risk_level=RiskLevel.LOW/MEDIUM/HIGH,
    complexity=Complexity.L1/L2/L3,
    status=Status.PENDING,
    verification=["验证项1", "验证项2", ...]
),
```

### 8.2 案例 SKIP, 错误 "README.md not found"

**原因**: 案例在 `CaseRegistry` 注册但 `fixtures/cases/NN_*/` 目录不存在。

**解决**:
- 创建 `fixtures/cases/NN_<name>/README.md` (参考 [01_basic_select/README.md](file:///d:/JIP/poc/fixtures/cases/01_basic_select/README.md))
- 确保目录名前缀为 `NN_` (两位数字 + 下划线)

### 8.3 `--list` 显示的案例数与预期不符

**原因**: 案例 23-26 仅在 `verify_cases.py` 注册, 未在本脚本注册。

**解决**: 如需统一, 参考 [第 5 节](#5-案例注册清单) 的注册格式补全, 或使用 `verify_cases.py --list` 查看完整 30 案例。

### 8.4 `--risk` 参数无效果

**原因**: `--risk` 仅与 `--list` 配合使用, 用于筛选显示。运行测试时不生效。

**解决**:
```bash
# 仅列出高风险案例
python scripts/run_case_tests.py --list --risk high
```

### 8.5 报告生成但通过率异常

**原因**: 案例 PASS 仅表示 README 存在, 不代表 SQL 语义正确。深度验证需 `verify_cases.py`。

**解决**:
```bash
# 在 PG 环境中跑 V3-V8 实测
python scripts/verify_cases.py --case 01 --level all
```

### 8.6 中文乱码 (Windows PowerShell)

```powershell
$env:PYTHONIOENCODING = "utf-8"
python scripts/run_case_tests.py
```

---

## 9. 相关文件

| 文件 | 说明 |
|------|------|
| [scripts/run_case_tests.py](file:///d:/JIP/poc/scripts/run_case_tests.py) | 本脚本 (V1-V2 框架) |
| [scripts/verify_cases.py](file:///d:/JIP/poc/scripts/verify_cases.py) | PG 实测 V3-V8, 见 [用法说明](file:///d:/JIP/poc/docs/verify_cases_用法.md) |
| [scripts/generate_baselines.py](file:///d:/JIP/poc/scripts/generate_baselines.py) | 静态基线生成, 见 [用法说明](file:///d:/JIP/poc/docs/generate_baselines_用法.md) |
| [fixtures/cases/](file:///d:/JIP/poc/fixtures/cases/) | 案例定义目录 (30 个 README.md) |
| [fixtures/cases/README.md](file:///d:/JIP/poc/fixtures/cases/README.md) | 30 案例覆盖矩阵 |
| [fixtures/cases/_test_data/test_report.md](file:///d:/JIP/poc/fixtures/cases/_test_data/test_report.md) | 测试报告输出 |

---

## 10. 完整工作流示例 (新机器首次运行)

```bash
# Step 1: 列出所有注册案例, 确认框架可用
python scripts/run_case_tests.py --list

# Step 2: 运行单个案例做冒烟测试
python scripts/run_case_tests.py --case 01

# Step 3: 运行全部测试并生成报告
python scripts/run_case_tests.py

# Step 4: 查看报告
cat fixtures/cases/_test_data/test_report.md

# Step 5: (可选) 按风险筛选案例
python scripts/run_case_tests.py --list --risk high

# Step 6: (后续) 在 PG 环境中跑深度验证
python scripts/verify_cases.py --case 01 --level all

# Step 7: (后续) 生成静态基线记录
python scripts/generate_baselines.py
```

---

## 11. 设计说明

### 11.1 为什么案例 23-26 不在本脚本注册

历史原因: 案例 23-26 在 Phase 1 后期新增时直接注册到 `verify_cases.py` (因为需要 PG 实测), 未回填到 `run_case_tests.py`。如需统一, 可参考第 5 节格式补全。

### 11.2 为什么 PASS 仅表示 README 存在

`run_case_test()` 当前实现仅检查:
- 案例在 `CaseRegistry` 注册
- `fixtures/cases/NN_*/README.md` 文件存在

不实际解析 SQL 或对比 Oracle/PG 行为, 因为此类深度验证需要 PG 环境, 由 `verify_cases.py` 负责。

### 11.3 与 `generate_baselines.py` 的关系

| 维度 | `run_case_tests.py` | `generate_baselines.py` |
|------|---------------------|-------------------------|
| 主要产出 | `test_report.md` (测试报告) | `baselines/*.md` (基线记录) |
| 解析 README | 否 (仅检查存在) | 是 (提取 7 个章节) |
| 输出维度 | 26 个案例状态 | 30 个案例基线记录 |
| CASE_META | `CaseRegistry.CASES` (26 案例) | `CASE_META` (30 案例) |

### 11.4 元数据维护

`CaseRegistry.CASES` 需与以下来源保持一致:
- [verify_cases.py](file:///d:/JIP/poc/scripts/verify_cases.py) 的 `register_all_cases()`
- [generate_baselines.py](file:///d:/JIP/poc/scripts/generate_baselines.py) 的 `CASE_META`
- [fixtures/cases/README.md](file:///d:/JIP/poc/fixtures/cases/README.md) 覆盖矩阵

新增案例时, 需同步更新以上 4 处 (目前 23-26 在本脚本中缺失, 是已知偏差)。

---

*文档版本: 1.0*
*最后更新: 2026-09-18*
*对应脚本版本: run_case_tests.py (含 26 案例 CaseRegistry.CASES: 01-22 + 27-30)*
