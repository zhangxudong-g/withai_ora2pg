"""workspace/agent/schema_dict.py

P4-2 关键：从产物文件扫描已存在的函数/过程名，构造"可用函数字典"。

问题：Agent 盲写时会引用 Oracle 嵌套子程序名（如 BUILD_QUERY、VALIDATE_STUDENT_NO），
但这些函数没被 lift 到 schema 级，PG 装时报"函数不存在"。

解决：prompt 里明确告诉 Agent "只能调用以下函数，其他嵌套逻辑必须内联实现"。
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Iterable


CREATE_FUNC_RE = re.compile(
    r"^CREATE\s+OR\s+REPLACE\s+(FUNCTION|PROCEDURE)\s+([A-Za-z_][\w.]*)",
    re.IGNORECASE | re.MULTILINE,
)


def scan_existing_functions(product_dir: Path) -> set[str]:
    """扫描产物目录下所有 SQL 文件，返回已存在的函数/过程名集合。

    返回名字归一化为小写（PG 不区分大小写）。
    """
    names: set[str] = set()
    for sql_file in sorted(product_dir.glob("*/*.sql")):
        if sql_file.name.startswith("_"):
            continue
        text = sql_file.read_text(encoding="utf-8", errors="replace")
        for m in CREATE_FUNC_RE.finditer(text):
            kind = m.group(1).upper()
            raw_name = m.group(2).strip().lower()
            # 去掉 schema 前缀的点号保留（如 pkg_score_manage.func_calc_total）
            names.add(raw_name)
    return names


def build_schema_context(existing_funcs: Iterable[str]) -> str:
    """构造注入到 prompt 的 schema 上下文文本。"""
    func_list = sorted(set(existing_funcs))
    if not func_list:
        return "(无可用函数)"

    lines = ["## 已有可用函数（只能调用这些，其他嵌套逻辑必须内联）", ""]
    for name in func_list:
        lines.append(f"- {name}")
    lines.append("")
    lines.append("⚠️ 如果你要调用的函数不在以上列表里，**不要写调用语句**——")
    lines.append("   直接把嵌套逻辑内联到当前函数体里（用 PL/pgSQL 变量/循环实现）。")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    product = Path(sys.argv[1] if len(sys.argv) > 1 else "oracle_poc/output")
    funcs = scan_existing_functions(product)
    print(f"扫描到 {len(funcs)} 个可用函数：")
    for f in sorted(funcs):
        print(f"  {f}")