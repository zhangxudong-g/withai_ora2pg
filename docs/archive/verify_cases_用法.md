# verify_cases.py 用法说明

**脚本路径**: `scripts/verify_cases.py`
**功能**: Oracle → PostgreSQL 案例验证框架, 执行 V3-V8 多层验证 (需 PostgreSQL 环境)
**配套脚本**: `scripts/run_case_tests.py` (无 PG 环境的语法/对象层 V1-V2 检查), `scripts/generate_baselines.py` (无 PG 环境的静态基线生成)

---

## 1. 概述

`verify_cases.py` 实现 Phase 1 要求的验证层次 V3-V8, 在真实 PostgreSQL 数据库中执行案例 SQL, 对比 Oracle 行为, 输出结构化验证报告。共注册 30 个案例 (01-30), 涵盖 DDL/DML/DQL/PL/SQL 全部对象类型。

### 验证层次

| 层次 | 名称 | 测试内容 | 自动化 |
|------|------|----------|--------|
| V3 | 结果验证 | 相同输入下结果是否一致 | ✅ |
| V4 | 边界验证 | NULL、空集合、极值、重复数据、日期边界 | ✅ |
| V5 | 异常验证 | 异常触发条件及异常处理行为 | ✅ |
| V6 | 数据变更验证 | INSERT/UPDATE/DELETE/MERGE 影响行数和最终数据 | ✅ |
| V7 | 事务验证 | COMMIT/ROLLBACK、异常时回滚范围 | ✅ |
| V8 | 性能验证 | 执行计划、执行时间、Buffer、索引使用 | ✅ |

> **V1 语法验证 / V2 对象验证** 由 `run_case_tests.py` 负责, 本脚本不重复。

---

## 2. 前置条件

### 2.1 安装依赖

```bash
pip install psycopg2-binary
```

### 2.2 PostgreSQL 配置

脚本默认连接参数 (硬编码于文件头):

```python
PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "postgres"
PG_PASSWORD = "postgres"
TEST_DB_NAME = "gkb_cases_verify"
```

如需修改, 编辑 [scripts/verify_cases.py](file:///d:/JIP/poc/scripts/verify_cases.py) 第 46-50 行, 或通过环境变量覆盖 (`.env` 文件中)。

### 2.3 PostgreSQL 服务运行

确保本地 PG 服务已启动:

```bash
# Linux/Mac
sudo systemctl status postgresql
# Windows
Get-Service postgresql*
```

---

## 3. 命令行参数

| 参数 | 简写 | 类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--case <ID>` | `-c` | str | 无 | 指定案例编号 (如 `01`, `13`, `27`) |
| `--level <N>` | `-l` | str/int | `3` | 验证层次 `3-8` 或 `all` |
| `--report` | `-r` | flag | False | 生成验证报告 (运行所有 V3-V8) |
| `--setup` | 无 | flag | False | 创建测试数据库 `gkb_cases_verify` + 灌入种子数据 |
| `--cleanup` | 无 | flag | False | 删除测试数据库 |
| `--baseline <ID>` | `-b` | str | 无 | 生成指定案例的基线记录 (需配合 `--case` 时单独使用) |
| `--large <N>` | 无 | int | `0` | 生成大数据量种子数据 (N 行, 如 `--large 10000`) |

---

## 4. 典型工作流

### 4.1 首次使用: 准备测试环境

```bash
# (1) 创建测试数据库 + 表结构 + 种子数据
python scripts/verify_cases.py --setup

# (2) (可选) 生成 10000 行大数据量场景
python scripts/verify_cases.py --setup --large 10000
```

### 4.2 运行验证

```bash
# 运行所有案例的所有 V3-V8 验证
python scripts/verify_cases.py

# 仅运行案例 01 的所有层次
python scripts/verify_cases.py --case 01 --level all

# 仅运行案例 13 的 V6 (数据变更验证)
python scripts/verify_cases.py --case 13 --level 6

# 仅运行所有案例的 V8 (性能验证)
# (需要 --level 不与 --case 同时使用时, 默认运行所有案例 V3)
# 推荐先 --case 单独运行, 再批量对比
```

### 4.3 查看报告

```bash
# 生成报告并保存到 fixtures/cases/_test_data/verification_report.md
python scripts/verify_cases.py --report

# 查看报告
cat fixtures/cases/_test_data/verification_report.md
```

### 4.4 生成基线记录

```bash
# 生成案例 01 的基线记录 (仅打印元数据, 详细基线需配合静态生成器)
python scripts/verify_cases.py --baseline 01
```

> **注**: 当前 `--baseline` 仅打印案例元数据, 完整基线记录由 `scripts/generate_baselines.py` 静态生成, 见 [baselines/README.md](file:///d:/JIP/poc/fixtures/cases/_test_data/baselines/README.md)。

### 4.5 清理环境

```bash
# 删除测试数据库 (谨慎操作, 数据不可恢复)
python scripts/verify_cases.py --cleanup
```

---

## 5. 案例注册清单

脚本通过 `register_all_cases()` 注册 30 个案例, 与 `fixtures/cases/NN_*/README.md` 一一对应:

| ID | 名称 | 类型 | 风险 | V4 边界测试 | V5 异常测试 |
|----|------|------|------|------------|------------|
| 01 | 基础 SELECT | DQL | 低 | ✅ (NULL/极值/重复) | - |
| 02 | 多表 JOIN | DQL | 低 | - | - |
| 03 | 外连接 (+) | DQL | 中 | - | - |
| ... | (04-22) | ... | ... | 部分 | 部分 |
| 23 | DBMS_SQL | PL/SQL | 高 | - | ✅ (标识符/SQL 解析) |
| 24 | Trigger | DDL | 高 | - | - |
| 25 | 集合类型 | PL/SQL | 高 | - | - |
| 26 | 性能测试 | DQL | 中 | ✅ (全表/主键/聚合) | - |
| 27 | Index | DDL | 中 | ✅ (索引存在/函数索引/Bitmap) | - |
| 28 | Constraint | DDL | 中 | - | ✅ (PK/UQ/CHECK/FK 违规) |
| 29 | Sequence | DDL | 低 | ✅ (定义存在/CYCLE 回绕) | - |
| 30 | Scheduled Job | DDL | 高 | ✅ (pg_cron/INTERVAL 转 cron) | - |

完整案例元数据见 [fixtures/cases/README.md](file:///d:/JIP/poc/fixtures/cases/README.md) 覆盖矩阵。

---

## 6. 输出格式

### 6.1 单案例单层次输出

```
v3_result: PASS (12.3ms)
  row_count: 5
  columns: ['kojin_no', 'gakunen', 'genzon_kbn']
  expected_columns: ['kojin_no', 'gakunen', 'genzon_kbn']
```

### 6.2 全量报告 (保存到 verification_report.md)

```
# Oracle → PostgreSQL 验证报告

## 概要
| 指标 | 数值 |
|------|------|
| 总案例数 | 30 |
| 总检查项 | 79+ |
| 通过 | 61 |
| 失败 | 0 |
| 跳过 | 18 |
| 通过率 | 77.2% |
```

### 6.3 报告位置

- 测试报告: `fixtures/cases/_test_data/test_report.md` (由 `run_case_tests.py` 生成)
- 验证报告: `fixtures/cases/_test_data/verification_report.md` (由 `verify_cases.py` 生成)
- 基线记录目录: `fixtures/cases/_test_data/baselines/` (由 `generate_baselines.py` 生成)

---

## 7. 常见问题排查

### 7.1 `psycopg2.OperationalError: could not connect to server`

**原因**: PostgreSQL 服务未启动, 或连接参数错误。

**解决**:
```bash
# 检查服务状态
sudo systemctl status postgresql

# 检查端口
ss -tlnp | grep 5432

# 修改脚本中的 PG_HOST/PG_PORT/PG_USER/PG_PASSWORD
```

### 7.2 `database "gkb_cases_verify" does not exist`

**原因**: 未执行 `--setup`。

**解决**:
```bash
python scripts/verify_cases.py --setup
```

### 7.3 `relation "gkbtgakureibo" does not exist`

**原因**: 表未创建, 通常因 setup 未完成或被中断。

**解决**:
```bash
# 清理后重建
python scripts/verify_cases.py --cleanup
python scripts/verify_cases.py --setup
```

### 7.4 大量测试 SKIPPED

**原因**: 部分案例依赖特定扩展 (如 `pg_cron` for case 30) 或特定数据 (如大数据量需 `--large`)。

**解决**:
```bash
# 安装 pg_cron 扩展 (case 30 需要)
# postgresql.conf: shared_preload_libraries = 'pg_cron'
# CREATE EXTENSION pg_cron;

# 生成大数据量 (case 26 需要)
python scripts/verify_cases.py --setup --large 10000
```

### 7.5 中文乱码 (Windows PowerShell)

**解决**:
```powershell
$env:PYTHONIOENCODING = "utf-8"
python scripts/verify_cases.py
```

---

## 8. 相关文件

| 文件 | 说明 |
|------|------|
| [scripts/verify_cases.py](file:///d:/JIP/poc/scripts/verify_cases.py) | 本脚本 (V3-V8 验证) |
| [scripts/run_case_tests.py](file:///d:/JIP/poc/scripts/run_case_tests.py) | V1-V2 框架 (无 PG 环境) |
| [scripts/generate_baselines.py](file:///d:/JIP/poc/scripts/generate_baselines.py) | 静态基线生成器 (无 PG 环境) |
| [fixtures/cases/](file:///d:/JIP/poc/fixtures/cases/) | 30 个案例定义 (README.md) |
| [fixtures/cases/_test_data/BASELINE_TEMPLATE.md](file:///d:/JIP/poc/fixtures/cases/_test_data/BASELINE_TEMPLATE.md) | 基线记录模板 |
| [fixtures/cases/_test_data/baselines/](file:///d:/JIP/poc/fixtures/cases/_test_data/baselines/) | 已生成基线记录 (30 个) |
| [fixtures/cases/_test_data/verification_report.md](file:///d:/JIP/poc/fixtures/cases/_test_data/verification_report.md) | 验证报告输出 |

---

## 9. 完整工作流示例 (新机器首次运行)

```bash
# Step 1: 安装依赖
pip install psycopg2-binary

# Step 2: 确保 PostgreSQL 服务运行 (5432 端口, postgres/postres)

# Step 3: 创建测试数据库 + 种子数据
python scripts/verify_cases.py --setup

# Step 4: (可选) 大数据量场景
python scripts/verify_cases.py --setup --large 5000

# Step 5: 运行单个案例做冒烟测试
python scripts/verify_cases.py --case 01 --level all

# Step 6: 运行全部验证并生成报告
python scripts/verify_cases.py

# Step 7: 查看报告
cat fixtures/cases/_test_data/verification_report.md

# Step 8: 生成静态基线 (补充 V2-V8 实测数据待后续覆盖)
python scripts/generate_baselines.py --all

# Step 9: 清理 (如需)
python scripts/verify_cases.py --cleanup
```

---

*文档版本: 1.0*
*最后更新: 2026-09-18*
*对应脚本版本: verify_cases.py (含 case 27-30 注册)*
