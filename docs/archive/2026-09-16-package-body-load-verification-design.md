# PACKAGE_BODY 加载验证设计 (子项目 α-4)

> **状态**: 已批准，待实现
> **日期**: 2026-09-16
> **范围**: 子项目 α（PACKAGE_BODY 端到端加载验证）
> **前置**: 用户已确认分解策略（α/β/γ 三阶段），本 spec 只覆盖 α

---

## 一、目标与背景

### 1.1 背景

`convert/convert_package_body.py` 已实现 100% 转换成功率（1222/1222 子程序），输出物为 `out/pkg_body/*.sql`（70 个文件）。

但当前**没有任何端到端验证**：这些 SQL 文件从未被实际加载到真实 PostgreSQL，仅通过静态分析（无 blocked）确认产物存在。

### 1.2 目标

**将 70 个 PACKAGE_BODY 产物文件全部加载到真实 PostgreSQL，验证全部成功 CREATE（无语法/类型/依赖错误）**。

### 1.3 非目标（明确排除）

- ❌ 业务分支正确性验证（属 β/γ 范围）
- ❌ Oracle/PG 结果对比（属 γ 范围）
- ❌ 性能/并发测试
- ❌ 修改任何 `convert/*.py` 转换器脚本

### 1.4 成功标准

| 指标 | 标准 |
|------|------|
| 全部 70 个包加载 | 0 失败 OR 失败率 < 5%（已知 Oracle→PG 语法偏差） |
| 加载时长 | < 2 分钟 |
| 错误分类完整 | 每失败记录含 文件名/函数名/错误类型/消息 |
| 集成验证 | `scripts/verify_all.py` 一步到位 |

---

## 二、架构

### 2.1 组件

```
scripts/
├── verify_all.py                 ← 修改：添加第 7 步
└── verify_package_body_load.py    ← 新增：主验证脚本

out/pkg_body/
├── README.md                     ← 修改：添加"加载验证"章节引用 LOAD_REPORT.md
├── LOAD_REPORT.md                ← 新增：人类可读报告
├── LOAD_ERRORS.json              ← 新增：机器可读错误清单
└── *.sql                         ← 已存在：70 个转换产物
```

### 2.2 数据流

```
启动
  ↓
setup_test_db()  → DROP/CREATE schema in gkb_100_coverage
  ↓
load_ddl_files() → 197 张表 DDL 加载
  ↓
load_pkg_stubs() → 外部包桩函数加载
  ↓
load_pkg_bodies() → 70 个包加载（容错：失败继续）
  ↓
generate_report() → LOAD_REPORT.md + LOAD_ERRORS.json
  ↓
退出（return 0/1）
```

---

## 三、详细设计

### 3.1 `scripts/verify_package_body_load.py`（新增）

**结构（单文件，6 个函数）**：

```python
TEST_DB = "gkb_100_coverage"  # 与现有 verify_*.py 一致

def setup_test_db() -> None:
    """连接 gkb_100_coverage, DROP SCHEMA public CASCADE, CREATE SCHEMA public"""

def load_ddl_files(conn) -> dict:
    """加载 out/ddl/*.pgsql, 返回 {success_count, failed_files: [(file, error)]}"""

def load_pkg_stubs(conn) -> dict:
    """加载 out/pkg_stubs.sql, 返回 {success, error}"""

def load_pkg_bodies(conn) -> list:
    """加载 out/pkg_body/*.sql, 返回失败清单 [(file, func_name, error_type, msg_preview)]"""

def generate_report(stats: dict) -> None:
    """生成 LOAD_REPORT.md 和 LOAD_ERRORS.json"""

def main() -> int:
    """主入口, return 0=全成功, 1=有失败"""
```

**关键技术决策**：

1. **psycopg2 连接**：每个阶段共享一个连接（避免频繁连接/断开）
2. **事务管理**：每个 `*.sql` 文件作为独立事务（一文件失败不影响其他）
3. **错误捕获**：使用 `psycopg2.Error` 捕获；从 `e.diag.statement` 解析失败的 SQL；从 `e.diag.message_primary` 解析错误类型
4. **错误分类规则**（基于 PostgreSQL SQLSTATE 前缀）：
   - `42xxx` → 语法错误（syntax_error）
   - `42P01` → 关系缺失（undefined_table，依赖缺失）
   - `42703` → 列不存在（undefined_column）
   - `42883` → 函数不存在（undefined_function）
   - `22xxx` → 数据异常
   - `23xxx` → 完整性约束违反
   - 其他 → 归类为 "其他"

5. **失败函数名提取**：从失败的 SQL 语句正则匹配 `CREATE OR REPLACE FUNCTION (\w+)` 或 `CREATE OR REPLACE PROCEDURE (\w+)`

### 3.2 `scripts/verify_all.py`（修改）

在现有 6 组验证后**追加第 7 组**：

```python
STEPS = [
    # ... 现有 6 组保持不变
    ("DDL 映射自检",             "python", ["convert/type_mapping.py"]),
    ("DDL 转换回归 (514列)",     "python", ["convert/verify_ddl_conversion.py"]),
    ("DDL 79 项检查",            "python", ["scripts/verify_ddl_equivalence.py"]),
    ("全量等价 44 项",           "python", ["scripts/verify_all_equivalence.py"]),
    ("存储过程专项 49 项",       "python", ["scripts/verify_complete_coverage.py"]),
    ("★ 业务 100% 覆盖 93 项",   "python", ["scripts/verify_business_coverage_100.py"]),
    # ★ 新增第 7 组
    ("★ PACKAGE_BODY 加载 (1222 函数)", "python", ["scripts/verify_package_body_load.py"]),
]
```

### 3.3 报告格式

**`out/pkg_body/LOAD_REPORT.md`**（人类可读）：

```markdown
# PACKAGE_BODY 加载验证报告

**生成时间**: 2026-09-16 HH:MM:SS
**测试库**: gkb_100_coverage @ localhost:5432

## 总览
| 指标 | 数值 |
|------|------|
| 包文件总数 | 70 |
| 子程序总数 | 1222 |
| 加载成功包 | X |
| 加载成功子程序 | Y |
| 加载失败子程序 | Z |

## 错误分类
| 错误类型 | 数量 |
|---------|------|
| 语法错误 (42xxx) | 0 |
| 类型错误 (42P01/42703/42883) | N |
| 完整性约束 (23xxx) | 0 |
| 其他 | 0 |

## 失败清单
| 包文件 | 函数名 | 错误类型 | 错误消息 |
|--------|--------|---------|---------|
| gkbpb00130b.sql | FUNC_X | 类型错误 | ... |
```

**`out/pkg_body/LOAD_ERRORS.json`**（机器可读）：

```json
{
  "timestamp": "2026-09-16T12:34:56",
  "test_db": "gkb_100_coverage",
  "summary": {
    "total_files": 70,
    "total_functions": 1222,
    "successful_files": 70,
    "successful_functions": 1220,
    "failed_functions": 2
  },
  "error_breakdown": {
    "syntax_error": 0,
    "type_error": 1,
    "integrity_error": 0,
    "other": 1
  },
  "failures": [
    {
      "file": "gkbpb00130b.sql",
      "function": "gkbpkg_gkp00130_func_x",
      "error_type": "type_error",
      "sqlstate": "42P01",
      "message_preview": "relation \"table_yyy\" does not exist"
    }
  ]
}
```

---

## 四、测试策略

### 4.1 单元测试（手动）

| 测试场景 | 期望结果 |
|---------|---------|
| PG 库不存在 | 报错并退出码 2 |
| PG 库无权限 | 报错并退出码 2 |
| `out/pkg_body/` 为空 | 报告"0 个包，0 错误"，退出码 0 |
| 全部成功 | LOAD_REPORT.md 显示 100%，退出码 0 |
| 部分失败 | LOAD_REPORT.md 列出失败项，退出码 1 |

### 4.2 集成测试

- 集成到 `verify_all.py` 后跑一次完整流程
- 验证 `verify_package_body_load.py` 输出被汇总到 `verify_all.py` 的最终报告

---

## 五、风险与缓解

| 风险 | 缓解策略 |
|------|---------|
| PG 服务未启动 | `setup_test_db()` 中显式 `psycopg2.connect` 失败，抛 `SystemExit(2)` |
| PG 用户无 CREATE 权限 | 捕获 `psycopg2.errors.InsufficientPrivilege`，友好报错 |
| 加载超 2 分钟 | 单文件 >5 秒时打印 warning，不中止 |
| LOAD_REPORT.md 与现有 `out/*.md` 风格不一致 | 复用现有报告模板（参考 `out/PLSQL_CONVERSION_REPORT.md`） |
| 失败包数量多导致报告过长 | LOAD_REPORT.md 仅列前 20 个失败；完整清单在 LOAD_ERRORS.json |

---

## 六、依赖

### 6.1 输入依赖

| 依赖 | 来源 | 必须 |
|------|------|------|
| PostgreSQL 服务运行 | localhost:5432 | ✅ |
| `gkb_100_coverage` 库 | 用户/现有脚本 | ✅ |
| `out/ddl/*.pgsql` | `convert/ddl_converter.py --all` | ✅（已存在） |
| `out/pkg_stubs.sql` | `convert/generate_pkg_stubs.py` | ✅（已存在） |
| `out/pkg_body/*.sql` | `convert/convert_package_body.py --all` | ✅（已存在） |

### 6.2 Python 依赖

- `psycopg2-binary`（已记录在 `requirements.txt`）
- `json`, `datetime`, `pathlib`, `re`（标准库）
- `subprocess`（仅在 `verify_all.py` 调用时使用，不在本脚本中直接使用）

---

## 七、变更清单

| 动作 | 文件 | 说明 |
|------|------|------|
| 新增 | `scripts/verify_package_body_load.py` | 主验证脚本（~200 行） |
| 修改 | `scripts/verify_all.py` | 追加第 7 组验证 |
| 新增 | `out/pkg_body/LOAD_REPORT.md` | 验证报告（脚本生成） |
| 新增 | `out/pkg_body/LOAD_ERRORS.json` | 错误清单（脚本生成） |
| 修改 | `out/pkg_body/README.md` | 添加"加载验证"章节引用新报告 |
| 修改 | `README.md` | 更新总览 + 验证步骤说明 |

---

## 八、退出码约定

| 退出码 | 含义 |
|--------|------|
| 0 | 全部 1222 函数加载成功 |
| 1 | 有失败（LOAD_REPORT.md 含失败清单） |
| 2 | 环境错误（库不存在、无权限、服务未启动） |

**报告文件策略**：
- `out/pkg_body/LOAD_REPORT.md` — 每次运行覆盖（最新结果）
- `out/pkg_body/LOAD_ERRORS.json` — 每次运行覆盖
- 如需保留历史快照，用户可手动复制到 `LOAD_REPORT_YYYYMMDD-HHMMSS.md`

---

## 九、后续（不在本 spec 范围）

- **β：桩函数连通 + 调用链测试**（依赖 α）
- **γ：业务流对比**（依赖 β，使用 `gkb/` 真实测试数据）
- 自动修复 α 报告中的失败函数（需修改 `convert/convert_package_body.py`，不在本 spec 范围）