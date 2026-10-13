# Workspace

独立工作目录：使用 perlstyle 转换 + 独立 verification 验证。

**不依赖** `test_reports/` 和 `pipeline/`，**不依赖 AI**。

## 目录结构

```
workspace/
├── pipeline.py              # 主入口
├── README.md                # 本文档
│
├── converter/               # 转换模块 (perlstyle)
│   ├── __init__.py
│   └── perlstyle_converter.py
│
├── verification/            # 独立验证框架 (4-Level)
│   ├── __init__.py
│   ├── models.py
│   ├── patterns.py
│   ├── sqlsplit.py
│   ├── static_check.py      # L1
│   ├── syntax_check.py      # L2
│   ├── pg_sandbox.py        # L3
│   ├── behavior_check.py    # L4
│   ├── framework.py
│   └── report.py
│
├── config/                  # 配置
│   ├── __init__.py
│   └── settings.py
│
├── input/                   # 输入 (Oracle 源文件)
├── output/                  # 输出 (转换后的 PG)
├── results/                 # 验证结果
│   └── latest/
│       ├── conversion_results.json
│       ├── verification_results.json
│       └── verification_report.md
```

## 转换引擎

`converter/perlstyle_converter.py` 调用 `ora2pg_py_perlstyle/perlstyle_engine.py`，
即 Ora2Pg Perl 原版的 Python 重写（功能覆盖率 ~71%）。

**特点**：
- 纯规则转换，无 AI
- 支持对象类型: FUNCTION / PROCEDURE / PACKAGE / TRIGGER / TYPE / VIEW / TABLE / DATA
- 自动识别驱动脚本并直通

## 验证框架

4 层验证，权重 15/20/25/40：

| Level | 名称 | 权重 | 说明 |
|-------|------|------|------|
| L1 | 静态分析 | 15% | Oracle 残留 / 逻辑错误 / 占位符 |
| L2 | 语法验证 | 20% | pglast 逐语句 |
| L3 | 安装验证 | 25% | PG 沙箱真实建对象 |
| L4 | 行为等价 | 40% | 金标准用例 |

## 使用

```bash
cd D:/JIP/poc/workspace

# 1. 放入 Oracle 源文件到 input/
# 2. 完整流程
python pipeline.py

# 3. 分步
python pipeline.py convert
python pipeline.py verify --no-pg
python pipeline.py report

# 4. 高级
python pipeline.py verify --cases path/to/golden.json
python pipeline.py verify --keep-schema
```

## 与其他目录的关系

| 目录 | 关系 |
|------|------|
| `ora2pg_py_perlstyle/` | 转换引擎（只读依赖） |
| `test_reports/` | 无 |
| `pipeline/` | 无 |
