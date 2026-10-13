"""P4-3 批次 2：BULK COLLECT / FORALL 改写

策略：
- FORALL i IN 1..arr.COUNT <dml>; → FOR i IN 1..array_length(arr,1) LOOP <dml>; END LOOP;
  关键：FORALL 的 body 是**单条 DML**（到第一个分号结束），不是块。
- BULK COLLECT INTO vars FROM ... WHERE ...;
  单变量 → SELECT ARRAY_AGG(...) INTO var FROM ... WHERE ...;
  多变量 → RAISE EXCEPTION 桩
- EXECUTE v_sql BULK COLLECT INTO var; → temp table 方案
- UTL_FILE → RAISE NOTICE 桩（PG 无等价文件 API）

字符串/注释豁免复用 exception_map 的剥离工具。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 复用 exception_map 的剥离工具
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _strip_strings_and_comments,
    _restore_placeholders,
)


# ===========================================================================
# 工具：找 DML 语句的结束位置（第一个 depth=0 的分号）
# ===========================================================================

def _find_dml_end(lines: list[str], start: int) -> int:
    """从 lines[start] 开始，找到 DML 语句的结束行（第一个 depth=0 的 `;`）。

    返回结束行的 index。如果找不到，返回 len(lines)-1。
    """
    depth = 0
    i = start
    while i < len(lines):
        for ch in lines[i]:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ';' and depth == 0:
                return i
        i += 1
    return len(lines) - 1


# ===========================================================================
# FORALL 改写
# ===========================================================================

def rewrite_forall(sql: str) -> str:
    """FORALL i IN 1..arr.COUNT <dml>; → FOR i IN 1..array_length(arr,1) LOOP <dml>; END LOOP;

    关键点：
    - FORALL body 是单条 DML 语句，到第一个 depth=0 的 `;` 结束
    - 不是扫到下一个 `END;`（那是外层 DECLARE-END 块）
    - `arr.COUNT` → `array_length(arr, 1)`
    """
    stripped, restore = _strip_strings_and_comments(sql)
    lines = stripped.split("\n")
    new_lines: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"(\s*)FORALL\s+(\w+)\s+IN\s+(.+?)\s*$", line, re.IGNORECASE)
        if m:
            indent = m.group(1)
            idx_var = m.group(2)
            range_expr = m.group(3)

            # 拆分 start..end，只替换 end 部分的 .COUNT → array_length(arr, 1)
            if ".." in range_expr:
                start_part, end_part = range_expr.split("..", 1)
                # v_list.COUNT → array_length(v_list, 1)
                end_part = re.sub(
                    r"(\S+)\.COUNT\s*$", r"array_length(\1, 1)", end_part.strip()
                )
                range_expr = f"{start_part}..{end_part}"
            else:
                # 没有 .. 的情况（直接数字）
                range_expr = re.sub(
                    r"(\S+)\.COUNT\s*$", r"array_length(\1, 1)", range_expr
                )

            new_lines.append(f"{indent}FOR {idx_var} IN {range_expr} LOOP")
            i += 1

            # 找 DML 行的结束（第一个 depth=0 的 `;`）
            dml_end = _find_dml_end(lines, i)
            # 原样保留 DML 行
            new_lines.extend(lines[i:dml_end + 1])
            new_lines.append(f"{indent}END LOOP;")
            i = dml_end + 1
            continue
        new_lines.append(line)
        i += 1
    return _restore_placeholders("\n".join(new_lines), restore)


# ===========================================================================
# BULK COLLECT 单变量 → ARRAY_AGG
# ===========================================================================

# 匹配: BULK COLLECT INTO <single_var>（无逗号 = 单变量）
_BULK_SINGLE_LINE_RE = re.compile(
    r"\bBULK\s+COLLECT\s+INTO\s+(\w+)\s*$",
    re.IGNORECASE,
)


def rewrite_bulk_collect_singlevar(sql: str) -> str:
    """单变量 BULK COLLECT → SELECT ARRAY_AGG(cols) INTO var FROM ... WHERE ...;

    用行扫描避免 DOTALL 跨 BEGIN...END; 块。
    Oracle:
        SELECT col1, col2, ...
        BULK COLLECT INTO v_arr
        FROM table WHERE ...;

    PG (单列):
        SELECT ARRAY_AGG(col1) INTO v_arr FROM table WHERE ...;
    PG (多列):
        SELECT ARRAY_AGG(ROW(col1, col2, ...)) INTO v_arr FROM table WHERE ...;
    """
    stripped, restore = _strip_strings_and_comments(sql)
    lines = stripped.split("\n")
    new_lines: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.search(
            r"\bBULK\s+COLLECT\s+INTO\s+(\w+)\s*$",
            line, re.IGNORECASE,
        )
        if m:
            var = m.group(1)
            # 回溯找 SELECT 起始行（向上找最近的 SELECT）
            sel_start = i - 1
            while sel_start >= 0 and not re.match(
                r"\s*SELECT\b", lines[sel_start], re.IGNORECASE
            ):
                # 碰到 BEGIN/END/EXCEPTION 等块边界就停
                if re.match(
                    r"\s*(BEGIN|END|EXCEPTION|LOOP|IF|ELSIF|ELSE|WHILE|FOR)\b",
                    lines[sel_start], re.IGNORECASE,
                ):
                    break
                sel_start -= 1
            if sel_start >= 0 and re.match(
                r"\s*SELECT\b", lines[sel_start], re.IGNORECASE
            ):
                # 回退 new_lines 中已 add 的 SELECT 行及中间行
                num_between = i - sel_start
                for _ in range(num_between):
                    if new_lines:
                        new_lines.pop()
                if new_lines and re.match(r"\s*SELECT\b", new_lines[-1], re.IGNORECASE):
                    new_lines.pop()
                if new_lines and re.match(r"\s*--.*BULK\s*COLLECT", new_lines[-1], re.IGNORECASE):
                    new_lines.pop()
                # 收集 SELECT 行到 BULK COLLECT 行之间的列表达式
                cols_parts = []
                for k in range(sel_start + 1, i):
                    cols_parts.append(lines[k].strip())
                # 也取 SELECT 行本身的列
                sel_line = lines[sel_start]
                sel_match = re.match(r"\s*SELECT\s+(.*)", sel_line, re.IGNORECASE)
                if sel_match and sel_match.group(1).strip():
                    cols_parts.insert(0, sel_match.group(1).strip())
                cols_text = ", ".join(c for c in cols_parts if c)

                # 前向找语句结束 `;`
                stmt_end = i
                while stmt_end < len(lines) and not lines[stmt_end].rstrip().endswith(";"):
                    stmt_end += 1
                # FROM... 部分保持不变
                from_parts = []
                for k in range(i + 1, min(stmt_end + 1, len(lines))):
                    from_parts.append(lines[k])
                from_text = "\n".join(from_parts)

                # 数逗号（depth 0）判断单列/多列
                depth = 0
                comma_count = 0
                for ch in cols_text:
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                    elif ch == ',' and depth == 0:
                        comma_count += 1

                indent_m = re.match(r"^(\s*)", lines[sel_start])
                indent = indent_m.group(1) if indent_m else "    "

                if comma_count == 0:
                    new_sel = f"{indent}SELECT ARRAY_AGG({cols_text}) INTO {var}"
                else:
                    new_sel = f"{indent}SELECT ARRAY_AGG(ROW({cols_text})) INTO {var}"

                # 替换 SELECT...BULK COLLECT INTO 部分，保留 FROM...;
                new_lines.append(new_sel)
                new_lines.extend(from_parts)
                i = stmt_end + 1
                continue
            # 找不到 SELECT，原样保留
            new_lines.append(line)
            i += 1
            continue
        new_lines.append(line)
        i += 1
    return _restore_placeholders("\n".join(new_lines), restore)


# ===========================================================================
# EXECUTE v_sql BULK COLLECT INTO var → temp table
# ===========================================================================

_EXEC_BULK_RE = re.compile(
    r"\bEXECUTE\s+(\w+)\s+BULK\s+COLLECT\s+INTO\s+(\w+)\s*;",
    re.IGNORECASE,
)


def rewrite_execute_bulk_collect(sql: str) -> str:
    """EXECUTE v_sql BULK COLLECT INTO var; → temp table 方案。

    Oracle:
        EXECUTE IMMEDIATE v_sql BULK COLLECT INTO v_ranks;
        FORALL i IN 1..v_ranks.COUNT ...

    PG (简化):
        -- temp table 替代 BULK COLLECT
        DROP TABLE IF EXISTS _tmp_v_ranks;
        CREATE TEMP TABLE _tmp_v_ranks AS EXECUTE (v_sql);
    """
    stripped, restore = _strip_strings_and_comments(sql)

    def _replace(m: re.Match) -> str:
        sql_var = m.group(1)
        arr_var = m.group(2)
        tmp_name = f"_tmp_{arr_var}"
        return (
            f"DROP TABLE IF EXISTS {tmp_name};\n"
            f"CREATE TEMP TABLE {tmp_name} AS EXECUTE ({sql_var});"
        )

    new = _EXEC_BULK_RE.sub(_replace, stripped)
    return _restore_placeholders(new, restore)


# ===========================================================================
# UTL_FILE → RAISE NOTICE 桩
# ===========================================================================

_Utl_FILE_TYPE_RE = re.compile(
    r"Utl_FILE\.FILE_TYPE", re.IGNORECASE
)
_UTL_FILE_FOPEN_RE = re.compile(
    r"Utl_FILE\.FOPEN\s*\(", re.IGNORECASE
)
_UTL_FILE_PUT_RE = re.compile(
    r"Utl_FILE\.PUT_LINE\s*\(", re.IGNORECASE
)
_UTL_FILE_FCLOSE_RE = re.compile(
    r"Utl_FILE\.FCLOSE\s*\(", re.IGNORECASE
)
_UTL_FILE_IS_OPEN_RE = re.compile(
    r"Utl_FILE\.IS_OPEN\s*\(", re.IGNORECASE
)


def rewrite_utl_file(sql: str) -> str:
    """UTL_FILE 包调用 → PG RAISE NOTICE 桩。

    PG 没有 UTL_FILE 等价功能。用 RAISE NOTICE 替代，
    让产物可安装但文件导出功能不生效（P4-3 后续完善）。
    """
    stripped, restore = _strip_strings_and_comments(sql)

    # 1. UTL_FILE.FILE_TYPE 类型引用 → RECORD（兼容性好）
    new = _Utl_FILE_TYPE_RE.sub("RECORD", stripped)

    # 2. v_FILE := UTL_FILE.FOPEN(...) → RAISE NOTICE + v_FILE := NULL;
    #    匹配完整的赋值语句（含变量名）
    new = re.sub(
        r"(\w+)\s*:=\s*Utl_FILE\s*\.\s*FOPEN\s*\([^)]*\)\s*;",
        r"RAISE NOTICE 'UTL_FILE.FOPEN stub (PG 不支持文件写入)';\n        \1 := NULL;",
        new, flags=re.IGNORECASE,
    )
    # UTL_FILE.FOPEN 不带 := 的（独立语句）
    new = re.sub(
        r"Utl_FILE\.FOPEN\s*\([^)]*\)\s*;?",
        r"RAISE NOTICE 'UTL_FILE.FOPEN stub (PG 不支持文件写入)'",
        new, flags=re.IGNORECASE,
    )

    # 3. UTL_FILE.PUT_LINE(fh, data); → RAISE LOG 'stub: %', data;
    def _put_line_repl(m: re.Match) -> str:
        args = m.group(1).strip()
        # 如果只有2个参数 (fh, data)，直接拼格式字符串
        parts = [a.strip() for a in args.split(',')]
        if len(parts) == 2:
            return f"RAISE LOG 'UTL_FILE.PUT_LINE stub: %', {parts[1]}"
        else:
            # 多参数: 第一个是 file handle，取最后一个作为数据
            return f"RAISE LOG 'UTL_FILE.PUT_LINE stub: %', {parts[-1]}"
    new = re.sub(
        r"Utl_FILE\.PUT_LINE\s*\(([^)]+)\)", _put_line_repl, new, flags=re.IGNORECASE
    )

    # 4. UTL_FILE.FCLOSE(fh) → NULL（no-op）
    new = re.sub(
        r"Utl_FILE\.FCLOSE\s*\([^)]*\)",
        r"PERFORM 1 -- UTL_FILE.FCLOSE stub",
        new, flags=re.IGNORECASE,
    )

    # 5. UTL_FILE.IS_OPEN(fh) → TRUE
    new = re.sub(
        r"Utl_FILE\.IS_OPEN\s*\([^)]*\)",
        r"TRUE",
        new, flags=re.IGNORECASE,
    )

    return _restore_placeholders(new, restore)


# ===========================================================================
# BULK COLLECT 多变量 → RAISE EXCEPTION 桩（完整语句替换）
# ===========================================================================

def rewrite_bulk_collect_multivar(sql: str) -> str:
    """多变量 BULK COLLECT 整句替换为 RAISE EXCEPTION 桩。

    关键：替换整个 SELECT...BULK COLLECT INTO v1, v2, ...; 语句。
    用行扫描避免 DOTALL 跨越 BEGIN...END; 块。
    SELECT 行可能已被 add 到 new_lines，遇到 BULK COLLECT 时需回退。
    """
    stripped, restore = _strip_strings_and_comments(sql)
    lines = stripped.split("\n")
    new_lines: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.search(
            r"\bBULK\s+COLLECT\s+INTO\s+(\w+\s*,\s*\w+(?:\s*,\s*\w+)*?)\s*$",
            line, re.IGNORECASE,
        )
        if m:
            var_count = len([v for v in m.group(1).split(",") if v.strip()])
            if var_count >= 2:
                indent = re.match(r"^(\s*)", line).group(1) if line.strip() else "    "
                # 回溯找 SELECT 起始行（在 original lines 中）
                sel_start = i
                while sel_start > 0 and not re.match(r"\s*SELECT\b", lines[sel_start], re.IGNORECASE):
                    sel_start -= 1
                # 前向找语句结束 `;`
                stmt_end = i
                while stmt_end < len(lines) and not lines[stmt_end].rstrip().endswith(";"):
                    stmt_end += 1
                # 回退 new_lines 中已 add 的 SELECT 行（从 sel_start 到 i-1）
                # new_lines 当前长度 = i（每行 add 一次），需要移除 sel_start..i-1 的行
                # 但实际上 i 可能不等于 len(new_lines)（如果之前有跳过）
                # 安全做法：从 new_lines 尾部移除直到 SELECT 行
                num_to_remove = i - sel_start  # 行数（不含 SELECT 行本身）
                for _ in range(num_to_remove):
                    if new_lines:
                        new_lines.pop()
                # 再移除 SELECT 行本身
                if new_lines and re.match(r"\s*SELECT\b", new_lines[-1], re.IGNORECASE):
                    new_lines.pop()
                # 移除 SELECT 行上方的 comment 行（-- BULK COLLECT 注释）
                if new_lines and re.match(r"\s*--.*BULK\s*COLLECT", new_lines[-1], re.IGNORECASE):
                    new_lines.pop()
                # 替换整句
                new_lines.append(f"{indent}-- BULK COLLECT into {var_count} variables: PG 不支持")
                new_lines.append(
                    f"{indent}RAISE EXCEPTION 'not implemented: BULK COLLECT into "
                    f"{var_count} variables, please use composite type array';"
                )
                i = stmt_end + 1
                continue
            # 单变量：留给 rewrite_bulk_collect_singlevar
        new_lines.append(line)
        i += 1
    return _restore_placeholders("\n".join(new_lines), restore)


# ===========================================================================
# 组合
# ===========================================================================
def apply_bulk_operations(sql: str) -> str:
    """P4-3 批次 2：FORALL 改写 + BULK COLLECT 改写 + EXECUTE BULK + UTL_FILE。"""
    # 1. 多变量 BULK COLLECT → 桩（先处理，避免和单变量规则冲突）
    sql = rewrite_bulk_collect_multivar(sql)
    # 2. 单变量 BULK COLLECT → ARRAY_AGG
    sql = rewrite_bulk_collect_singlevar(sql)
    # 3. EXECUTE ... BULK COLLECT INTO → temp table
    sql = rewrite_execute_bulk_collect(sql)
    # 4. FORALL → FOR LOOP（DML 到第一个 `;` 结束）
    sql = rewrite_forall(sql)
    # 5. UTL_FILE → RAISE NOTICE 桩
    sql = rewrite_utl_file(sql)
    return sql
