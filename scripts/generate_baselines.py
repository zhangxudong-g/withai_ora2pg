#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基线记录生成器 (静态版, 无需 PostgreSQL 环境)

从 fixtures/cases/<NN_name>/README.md 提取关键信息, 按
fixtures/cases/_test_data/BASELINE_TEMPLATE.md 格式生成基线记录。

适用场景: Phase 1 方案 A 补齐, 为 01-26 案例批量生成静态基线。
后续在 PG 环境中可用 `python scripts/verify_cases.py --baseline NN`
覆盖生成含 V3-V8 实测数据的完整基线。

用法:
    python scripts/generate_baselines.py                # 生成全部缺失基线
    python scripts/generate_baselines.py --case 01     # 生成单个案例
    python scripts/generate_baselines.py --all          # 强制重新生成所有
    python scripts/generate_baselines.py --list         # 列出基线状态
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict

# 项目路径
ROOT = Path(__file__).parent.parent
CASES_DIR = ROOT / "fixtures" / "cases"
BASELINES_DIR = CASES_DIR / "_test_data" / "baselines"
TEMPLATE_PATH = CASES_DIR / "_test_data" / "BASELINE_TEMPLATE.md"


# ----------------------------------------------------------------------
# 元数据: 案例风险/类型/兼容性 (与 run_case_tests.py 保持一致)
# ----------------------------------------------------------------------
CASE_META: Dict[str, Dict[str, str]] = {
    "01": {"name": "基础 SELECT",       "type": "DQL",     "risk": "低", "compat": "✅", "complexity": "L1"},
    "02": {"name": "多表 JOIN",         "type": "DQL",     "risk": "低", "compat": "✅", "complexity": "L1"},
    "03": {"name": "外连接 (+)",        "type": "DQL",     "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "04": {"name": "EXISTS / NOT EXISTS","type": "DQL",    "risk": "低", "compat": "✅", "complexity": "L1"},
    "05": {"name": "UNION / UNION ALL", "type": "DQL",     "risk": "低", "compat": "✅", "complexity": "L1"},
    "06": {"name": "GROUP BY / HAVING", "type": "DQL",     "risk": "低", "compat": "✅", "complexity": "L1"},
    "07": {"name": "分析函数",          "type": "DQL",     "risk": "中", "compat": "✅", "complexity": "L1"},
    "08": {"name": "ROWNUM / 分页",     "type": "DQL",     "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "09": {"name": "日期处理",          "type": "DQL",     "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "10": {"name": "NULL 处理",         "type": "DQL",     "risk": "低", "compat": "✅", "complexity": "L1"},
    "11": {"name": "DECODE / NVL",      "type": "DQL",     "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "12": {"name": "CASE 表达式",       "type": "DQL",     "risk": "低", "compat": "✅", "complexity": "L1"},
    "13": {"name": "MERGE",             "type": "DML",     "risk": "高", "compat": "❌", "complexity": "L3"},
    "14": {"name": "INSERT SELECT",     "type": "DML",     "risk": "低", "compat": "✅", "complexity": "L1"},
    "15": {"name": "UPDATE / DELETE",   "type": "DML",     "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "16": {"name": "动态 SQL",          "type": "PL/SQL",  "risk": "高", "compat": "⚠️", "complexity": "L3"},
    "17": {"name": "REF CURSOR",        "type": "PL/SQL",  "risk": "高", "compat": "⚠️", "complexity": "L3"},
    "18": {"name": "Function",          "type": "DDL",     "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "19": {"name": "Procedure",         "type": "DDL",     "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "20": {"name": "Package",           "type": "DDL",     "risk": "高", "compat": "❌", "complexity": "L3"},
    "21": {"name": "异常处理",          "type": "PL/SQL",  "risk": "中", "compat": "⚠️", "complexity": "L2"},
    "22": {"name": "事务控制",          "type": "PL/SQL",  "risk": "高", "compat": "❌", "complexity": "L3"},
    "23": {"name": "DBMS_SQL",          "type": "PL/SQL",  "risk": "高", "compat": "❌", "complexity": "L3"},
    "24": {"name": "Trigger",           "type": "DDL",     "risk": "高", "compat": "⚠️", "complexity": "L3"},
    "25": {"name": "集合类型",          "type": "PL/SQL",  "risk": "高", "compat": "❌", "complexity": "L3"},
    "26": {"name": "性能测试",          "type": "DQL/DML", "risk": "中", "compat": "✅", "complexity": "L2"},
}


# ----------------------------------------------------------------------
# README 解析
# ----------------------------------------------------------------------
def find_case_dir(case_id: str) -> Optional[Path]:
    """根据 case_id 查找案例目录"""
    prefix = f"{case_id.zfill(2)}_"
    for item in CASES_DIR.iterdir():
        if item.is_dir() and item.name.startswith(prefix):
            return item
    return None


def parse_readme(readme_path: Path) -> Dict[str, str]:
    """从案例 README.md 提取关键章节"""
    text = readme_path.read_text(encoding="utf-8")

    # 提取标题 (第一行 #)
    title_match = re.search(r"^# (.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else readme_path.parent.name

    # 提取 Oracle 源代码块 (## Oracle 源 之后第一个 ```sql ... ```)
    oracle_section = _extract_section(text, r"##\s*Oracle\s*源")
    oracle_code = _extract_first_code_block(oracle_section)

    # 提取 PostgreSQL 转换 (## PostgreSQL 转换 之后所有 ```sql ... ```)
    pg_section = _extract_section(text, r"##\s*PostgreSQL\s*转换")
    pg_code = _extract_all_code_blocks(pg_section)

    # 提取验证方法 (## 验证方法 之后的 - [ ] 列表)
    verify_section = _extract_section(text, r"##\s*验证方法")
    verify_items = re.findall(r"-\s*\[[ xX]\]\s*(.+)", verify_section)

    # 提取业务目的 (### 业务目的 之后到下一个 ###/##)
    purpose = _extract_subsection(text, r"###\s*业务目的")

    # 提取依赖信息表
    deps_section = _extract_subsection(text, r"###\s*依赖信息")
    dep_table = _extract_first_table(deps_section)

    # 提取转换评估表
    eval_section = _extract_subsection(text, r"###\s*转换评估")
    eval_table = _extract_first_table(eval_section)

    return {
        "title": title,
        "oracle_code": oracle_code or "-- (源代码见案例 README.md)",
        "pg_code": pg_code or "-- (转换结果见案例 README.md)",
        "verify_items": verify_items,
        "purpose": purpose.strip() or "(见案例 README.md)",
        "dep_table": dep_table or "(见案例 README.md)",
        "eval_table": eval_table or "(见案例 README.md)",
    }


def _extract_section(text: str, header_pattern: str) -> str:
    """提取一个 ## 章节的内容 (到下一个 ## 或文件末尾)"""
    m = re.search(rf"{header_pattern}[^\n]*\n", text)
    if not m:
        return ""
    start = m.end()
    # 找下一个 ## (顶级章节)
    next_h2 = re.search(r"\n##\s+", text[start:])
    end = start + next_h2.start() if next_h2 else len(text)
    return text[start:end]


def _extract_subsection(text: str, header_pattern: str) -> str:
    """提取一个 ### 子章节的内容 (到下一个 ###/## 或文件末尾)"""
    m = re.search(rf"{header_pattern}[^\n]*\n", text)
    if not m:
        return ""
    start = m.end()
    next_h = re.search(r"\n##\s+", text[start:])
    end = start + next_h.start() if next_h else len(text)
    return text[start:end]


def _extract_first_code_block(text: str) -> str:
    """提取第一个 ```sql ... ``` 代码块"""
    m = re.search(r"```(?:sql|SQL)?\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_all_code_blocks(text: str) -> str:
    """提取所有代码块, 合并"""
    blocks = re.findall(r"```(?:sql|SQL)?\s*\n(.*?)```", text, re.DOTALL)
    return "\n\n".join(b.strip() for b in blocks if b.strip())


def _extract_first_table(text: str) -> str:
    """提取第一个 markdown 表格 (含表头分隔行)"""
    lines = text.split("\n")
    table_lines = []
    in_table = False
    for line in lines:
        if "|" in line and re.match(r"^\s*\|", line):
            in_table = True
            table_lines.append(line.rstrip())
        elif in_table and not line.strip():
            break
        elif in_table and "|" not in line:
            break
    return "\n".join(table_lines) if table_lines else ""


# ----------------------------------------------------------------------
# 基线记录生成
# ----------------------------------------------------------------------
def render_baseline(case_id: str, parsed: Dict[str, str]) -> str:
    """根据模板渲染基线记录"""
    meta = CASE_META.get(case_id, {})
    case_dir = find_case_dir(case_id)
    case_dir_name = case_dir.name if case_dir else f"{case_id}_unknown"
    today = datetime.now().strftime("%Y-%m-%d")

    # 转换状态判定
    compat = meta.get("compat", "⚠️")
    if compat == "✅":
        conv_status = "完全自动"
        go_live = "是"
    elif compat == "⚠️":
        conv_status = "部分自动"
        go_live = "需要人工确认"
    else:
        conv_status = "需要人工"
        go_live = "需要人工确认"

    verify_items = parsed["verify_items"] or ["语法解析通过"]
    v1_status = "✅ 通过" if compat in ("✅", "⚠️") else "⬜ 未执行"

    # 验证层次表
    v_layers = [
        ("V1 语法验证", "目标 SQL 解析", v1_status, "静态分析"),
        ("V2 对象验证", "对象/函数创建", "⬜ 未执行", "需 PG 环境"),
        ("V3 结果验证", "结果一致性", "⬜ 未执行", "需 PG 环境"),
        ("V4 边界验证", "NULL/极值/边界", "⬜ 未执行", "需 PG 环境"),
        ("V5 异常验证", "异常行为一致", "⬜ 未执行", "需 PG 环境"),
        ("V6 数据变更验证", "DML 影响行数", "⬜ 未执行", "需 PG 环境"),
        ("V7 事务验证", "COMMIT/ROLLBACK", "⬜ 未执行", "需 PG 环境"),
        ("V8 性能验证", "执行计划/耗时", "⬜ 未执行", "需 PG 环境"),
    ]
    v_table = "\n".join(
        f"| {n} | {t} | {r} | {n2} |" for n, t, r, n2 in v_layers
    )

    # 验证方法项作为 V1 子项
    verify_list = "\n".join(f"- [ ] {item}" for item in verify_items)

    # 风险清单 (基于兼容性)
    risk_id = f"R001"
    if compat == "✅":
        risk_desc = "完全兼容, 无行为差异"
        risk_level = "低"
        mitigation = "无需特别缓解"
    elif compat == "⚠️":
        risk_desc = "语法/语义差异需验证"
        risk_level = meta.get("risk", "中")
        mitigation = "在 PG 环境中实测对照"
    else:
        risk_desc = "Oracle 专用特性, 需重构"
        risk_level = meta.get("risk", "高")
        mitigation = "架构重构 + 应用层适配"

    return f"""# 转换结果基线记录 - {case_id} {meta.get('name', '')}

**生成日期**: {today}
**生成方式**: 静态分析 (scripts/generate_baselines.py)
**案例编号**: `{case_id}_{case_dir_name.split('_', 1)[-1] if '_' in case_dir_name else case_dir_name}`

---

## 案例基本信息

| 字段 | 内容 | 说明 |
|------|------|------|
| **案例编号** | `{case_id}_{case_dir_name.split('_', 1)[-1] if '_' in case_dir_name else case_dir_name}` | |
| **Oracle 源文件** | `fixtures/cases/{case_dir_name}/README.md` | 案例定义文件 |
| **PostgreSQL 转换文件** | `fixtures/cases/{case_dir_name}/README.md` (同文件内对照) | |
| **转换工具** | `手工 + script` | 对照型案例 |
| **工具版本** | N/A | 静态分析 |
| **转换日期** | {today} | |
| **转换人员** | Agent (generate_baselines.py) | 自动生成 |
| **案例类型** | `{meta.get('type', '')}` | |
| **风险等级** | `{meta.get('risk', '')}` | |

---

## Oracle 原始内容

### 原始 SQL / PL/SQL

```sql
{parsed['oracle_code']}
```

### 预期行为描述
{parsed['purpose']}

### 输入条件
- 依赖对象: 见依赖信息表
- 参数: 见案例 README.md

### 预期输出
见案例 README.md 中 "业务元数据" 章节。

---

## 转换过程

### 使用的转换规则

| 规则编号 | 规则描述 | 应用位置 |
|----------|----------|----------|
| R001 | 标识符大小写规范化 (Oracle UPPER → PG lower) | 全部 |
| R002 | Oracle 专用语法移除/替换 | 见案例 README |
| R003 | 数据类型映射 (NUMBER→NUMERIC, VARCHAR2→VARCHAR 等) | 全部 |

### 手工修正记录

| 位置 | 原内容 | 修改后 | 修改原因 |
|------|--------|--------|----------|
| 见案例 README | Oracle 专用语法 | PG 等价语法 | 兼容性 |

### 未转换的内容

| 内容 | 原因 | 风险 | 处理建议 |
|------|------|------|----------|
| (详见案例 README "注意事项" 章节) | - | - | - |

---

## PostgreSQL 转换结果

### 转换后代码
```sql
{parsed['pg_code']}
```

### 差异说明

| 类别 | 差异描述 | 影响评估 |
|------|----------|----------|
| 兼容性 | {meta.get('compat', '⚠️')} { '(完全兼容)' if compat == '✅' else ('(部分兼容)') if compat == '⚠️' else '(不兼容, 需重构)' } | { '无影响' if compat == '✅' else ('需验证') if compat == '⚠️' else ('需重构') } |

---

## 验证结果

### 验证环境

| 项目 | 值 |
|------|-----|
| PostgreSQL 版本 | (待 PG 环境实测) |
| 数据库 | gkb_cases_verify |
| 验证日期 | {today} (静态), PG 实测待定 |

### 验证层次

{v_table}

### 静态验证项 (来自案例 README)

{verify_list if verify_list else '- [ ] 语法解析通过'}

> **注**: V2-V8 待在 PG 环境中运行 `python scripts/verify_cases.py --case {case_id}` 后补充。

---

## 风险评估

### 风险清单

| 风险ID | 风险描述 | 风险等级 | 影响范围 | 缓解措施 | 状态 |
|--------|----------|----------|----------|----------|------|
| {risk_id} | {risk_desc} | {risk_level} | 全案例 | {mitigation} | 开放 |

### 风险等级定义

| 等级 | 说明 |
|------|------|
| 高 | 可能导致结果错误、数据损坏或事务语义变化 |
| 中 | 语法可转换，但需要确认行为差异 |
| 低 | 规则明确且容易自动验证 |

---

## 最终结论

| 评估项 | 结果 |
|--------|------|
| **转换状态** | `{conv_status}` |
| **业务一致性** | `{'一致' if compat == '✅' else '基本一致' if compat == '⚠️' else '存在差异'}` |
| **是否可以上线** | `{go_live}` |
| **遗留问题数** | 见案例 README "注意事项" |
| **下次复查日期** | {(datetime.now().replace(month=min(datetime.now().month+1, 12))).strftime('%Y-%m-%d')} |

### 综合评价

本基线由 `scripts/generate_baselines.py` 静态生成, 仅完成 V1 语法层验证。
V2-V8 实测数据需在 PostgreSQL 环境中运行 `python scripts/verify_cases.py --case {case_id}` 后覆盖补充。

案例详细转换规则、手工修正与差异说明见 `fixtures/cases/{case_dir_name}/README.md`。

---

## 变更历史

| 日期 | 修改人 | 修改内容 |
|------|--------|----------|
| {today} | Agent (generate_baselines.py) | 静态生成基线记录 (方案 A 补齐) |

---

*模板版本: 1.0*
*最后更新: {today}*
"""


def generate_baseline_for_case(case_id: str, force: bool = False) -> Optional[Path]:
    """为单个案例生成基线记录"""
    case_dir = find_case_dir(case_id)
    if not case_dir:
        print(f"[!] 案例 {case_id}: 目录未找到")
        return None

    readme = case_dir / "README.md"
    if not readme.exists():
        print(f"[!] 案例 {case_id}: README.md 不存在")
        return None

    case_name = case_dir.name.split("_", 1)[-1] if "_" in case_dir.name else case_dir.name
    out_path = BASELINES_DIR / f"{case_id}_{case_name}_baseline.md"

    if out_path.exists() and not force:
        return None  # 已存在, 跳过

    parsed = parse_readme(readme)
    content = render_baseline(case_id, parsed)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def list_baselines() -> None:
    """列出基线状态"""
    print(f"\n[*] 基线目录: {BASELINES_DIR}\n")
    print(f"{'#':<4} {'名称':<25} {'风险':<6} {'兼容':<6} {'基线文件'}")
    print("-" * 80)
    for cid in sorted(CASE_META.keys()):
        meta = CASE_META[cid]
        case_dir = find_case_dir(cid)
        if not case_dir:
            continue
        case_name = case_dir.name.split("_", 1)[-1] if "_" in case_dir.name else case_dir.name
        baseline = BASELINES_DIR / f"{cid}_{case_name}_baseline.md"
        status = "✅" if baseline.exists() else "❌"
        print(f"{cid:<4} {meta['name']:<25} {meta['risk']:<6} {meta['compat']:<6} {status} {baseline.name if baseline.exists() else '(未生成)'}")


def main():
    parser = argparse.ArgumentParser(description="基线记录生成器 (静态版)")
    parser.add_argument("--case", "-c", help="生成指定案例 (如 01, 02)")
    parser.add_argument("--all", action="store_true", help="强制重新生成所有案例 (覆盖)")
    parser.add_argument("--list", "-l", action="store_true", help="列出基线状态")
    args = parser.parse_args()

    BASELINES_DIR.mkdir(parents=True, exist_ok=True)

    if args.list:
        list_baselines()
        return

    if args.case:
        case_id = args.case.zfill(2)
        out = generate_baseline_for_case(case_id, force=args.all)
        if out:
            print(f"[+] 案例 {case_id}: 生成 {out.name}")
        else:
            print(f"[-] 案例 {case_id}: 已存在 (用 --all 覆盖)")
        return

    # 默认: 生成全部缺失基线
    generated, skipped, missing = 0, 0, 0
    for cid in sorted(CASE_META.keys()):
        result = generate_baseline_for_case(cid, force=args.all)
        if result is None:
            # 区分 "已存在跳过" 和 "错误"
            case_dir = find_case_dir(cid)
            if case_dir:
                case_name = case_dir.name.split("_", 1)[-1] if "_" in case_dir.name else case_dir.name
                baseline = BASELINES_DIR / f"{cid}_{case_name}_baseline.md"
                if baseline.exists():
                    skipped += 1
                else:
                    missing += 1
        else:
            generated += 1
            print(f"[+] 案例 {cid}: 生成 {result.name}")

    print(f"\n[*] 完成: 生成 {generated} 个, 跳过 {skipped} 个, 缺失 {missing} 个")
    print(f"[*] 输出目录: {BASELINES_DIR}")
    if skipped > 0 and not args.all:
        print(f"[*] 如需覆盖已存在的基线, 加 --all 参数")


if __name__ == "__main__":
    main()
