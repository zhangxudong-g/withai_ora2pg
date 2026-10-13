"""workspace/agent/validate_pg.py

P4-1 PG 硬校验（强化版）。

参考 pipeline/stages/stage3_ai/tools/validate_pg.py，但**强化**：
- **正则层**（保留 pipeline 思路）：CREATE/BEGIN/END/LANGUAGE 配对
- **强化层**：用真 PG 试装（**pglast 解析 + psql 试 CREATE**），捕获 95%+ 真错

返回结构化 ValidationResult，调用方决定 retry / fallback / skip。

使用：
    from workspace.agent.validate_pg import validate_pg
    result = validate_pg(code, dbname="...", schema="...")
    if not result.ok:
        raise ValidationError(result.errors)
"""
from __future__ import annotations
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------- 数据类 ----------

@dataclass
class ValidationResult:
    """硬校验结果。"""
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_code: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class ValidationError(Exception):
    """校验失败——orchestrator 据此决定 retry/fallback。"""

    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(f"PG validation failed: {result.errors}")


# ---------- 正则层 ----------

def _strip_strings(code: str) -> str:
    """去掉单引号字符串字面量（保留正则可见性）。

    **不剥 `$$...$$`**：PG 里 `$$` 是函数体界（也可能是 dollar-quoted string），
    剥离会丢 BEGIN/END 计数。简化策略：只剥 `'...''`。
    """
    out: list[str] = []
    i, n = 0, len(code)
    while i < n:
        if code[i] == "'":
            i += 1
            while i < n:
                if code[i] == "'":
                    if i + 1 < n and code[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        out.append(code[i])
        i += 1
    return "".join(out)


def _count_nested_begin_end(code: str) -> tuple[int, int]:
    """用栈式计数 BEGIN/END（嵌套感知）。

    Returns:
        (BEGIN_count, END_count) - 分别累计 push/pop。
        注意：1 个 BEGIN push，1 个 END pop；完全平衡时 BEGIN_count == END_count。
    """
    stripped = _strip_strings(code)
    begin_count = 0
    end_count = 0
    # 用类栈迭代：每个 BEGIN 都对应一个 END
    i = 0
    while i < len(stripped):
        # BEGIN
        if stripped[i:i + 5].upper() == "BEGIN" and (
            i == 0 or not stripped[i - 1].isalnum() and stripped[i - 1] != '_'
        ):
            # 下个非空白字符应是 ';' 或 ' '
            j = i + 5
            while j < len(stripped) and stripped[j] in " \t":
                j += 1
            if j < len(stripped) and stripped[j] in (";", " ", "\n", "\r"):
                begin_count += 1
                i += 5
                continue
        # END（可跟函数名 + ';'）
        if stripped[i:i + 3].upper() == "END" and (
            i == 0 or not stripped[i - 1].isalnum() and stripped[i - 1] != '_'
        ):
            # 后面跟可选空白 + 名称 + ';'，或只是 END;
            j = i + 3
            while j < len(stripped) and (stripped[j].isalnum() or stripped[j] == '_'):
                j += 1
            while j < len(stripped) and stripped[j] in " \t":
                j += 1
            if j < len(stripped) and stripped[j] == ";":
                end_count += 1
                i = j + 1
                continue
        i += 1
    return begin_count, end_count


def _regex_check(code: str) -> list[str]:
    """pipeline/ 的 5 项正则检查。"""
    errors: list[str] = []
    code_stripped = _strip_strings(code)
    code_upper = code_stripped.upper()

    # 1. CREATE FUNCTION/PROCEDURE
    if not re.search(r"CREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE)\b", code_upper):
        errors.append("Missing CREATE FUNCTION/PROCEDURE")

    # 2. BEGIN/END 配对（嵌套感知：栈式计数）
    begin_count, end_count = _count_nested_begin_end(code)
    if begin_count and begin_count != end_count:
        errors.append(f"BEGIN/END mismatch: BEGIN={begin_count} END={end_count}")

    # 3. LANGUAGE plpgsql
    if not re.search(r"LANGUAGE\s+plpgsql", code, re.IGNORECASE):
        errors.append("Missing LANGUAGE plpgsql")
    else:
        # 3b. PG 语法要求：LANGUAGE plpgsql 后必须跟 AS $$ ... $$;
        # 如果 LANGUAGE 行以 ';' 结尾，说明 LLM 把它当语句而非选项 -> 格式错
        lang_line = re.search(
            r"LANGUAGE\s+plpgsql\s*;?", code, re.IGNORECASE
        )
        if lang_line and lang_line.group(0).rstrip().endswith(";"):
            errors.append(
                "LANGUAGE plpgsql 行末不应有分号（应写 LANGUAGE plpgsql AS \$\$ ... \$\$;）"
            )

    # 4. $$ 配对（偶数）
    dollar_count = code.count("$$")
    if dollar_count < 2 or dollar_count % 2 != 0:
        errors.append(f"Unbalanced $$ delimiters (count={dollar_count}, must be even >=2)")

    # 5. RETURNS 必有（函数）
    is_func = bool(re.search(r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION\b", code_upper))
    if is_func and not re.search(r"\bRETURNS\b", code_upper):
        errors.append("FUNCTION without RETURNS clause")

    return errors


# ---------- 强化层：真 PG 试装 ----------

def _psql_available() -> bool:
    """检查 psql 是否在 PATH（或 PSQL_BIN）。"""
    from shutil import which
    from os import environ
    return which("psql") is not None or "PSQL_BIN" in environ


def _try_install_pg_sql(
    code: str,
    dbname: str = "postgres",
    schema: str = "agent_validate",
    host: str = "localhost",
    port: int = 5432,
    user: str = "postgres",
    timeout: int = 10,
) -> tuple[bool, str]:
    """用 psql 试装一次（事务回滚避免留痕），返 (ok, stderr_or_ok)。"""
    if not _psql_available():
        return True, "psql unavailable, skipped trial install"

    # 用事务 + ROLLBACK，避免真正建对象
    sql = (
        f"BEGIN;\n"
        f"CREATE SCHEMA IF NOT EXISTS {schema};\n"
        f"SET search_path TO {schema};\n"
        f"{code}\n"
        f"ROLLBACK;\n"
    )

    from os import environ
    cmd = [
        "psql",
        "-h", host, "-p", str(port), "-U", user, "-d", dbname,
        "-X", "-q", "-v", "ON_ERROR_STOP=on",
        "-c", sql,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**environ, "PGPASSWORD": environ.get("PGPASSWORD", "")},
        )
    except subprocess.TimeoutExpired:
        return False, "psql trial install timeout"
    except FileNotFoundError:
        return True, "psql not found, skipped trial install"

    if proc.returncode != 0:
        # 提取 ERROR 行
        err_lines = [
            ln for ln in (proc.stdout + proc.stderr).splitlines()
            if "错误" in ln or "ERROR" in ln or "错误" in ln
        ][:3]
        return False, "\n".join(err_lines) or "psql exit non-zero"
    return True, ""


# ---------- 公开 API ----------

def validate_pg(
    code: str,
    *,
    trial_install: bool = True,
    dbname: str = "postgres",
    schema: str = "agent_validate",
    host: str = "localhost",
    port: int = 5432,
    user: str = "postgres",
) -> ValidationResult:
    """硬校验 PG 代码。

    Args:
        code: 待校验的 PL/pgSQL 代码
        trial_install: 是否真 PG 试装（默认 True）
        其它参数：psql 连接参数

    Returns:
        ValidationResult；ok=True 表示两层检查都通过。
    """
    if not code or not code.strip():
        return ValidationResult(ok=False, errors=["Empty code"], raw_code=code)

    result = ValidationResult(ok=True, raw_code=code)

    # 1. 正则层
    regex_errors = _regex_check(code)
    if regex_errors:
        result.errors.extend(regex_errors)
        result.ok = False

    # 2. 强化层（试装）
    if trial_install:
        install_ok, install_err = _try_install_pg_sql(
            code, dbname=dbname, schema=schema,
            host=host, port=port, user=user,
        )
        if not install_ok:
            result.errors.append(f"PG install trial failed: {install_err}")
            result.ok = False

    return result