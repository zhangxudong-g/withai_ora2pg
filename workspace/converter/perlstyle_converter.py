"""
perlstyle 转换器 - 包装 ora2pg_py_perlstyle 模块

功能:
1. 文件级转换 (Oracle SQL -> PG SQL)
2. 类型检测 (FUNCTION/PROCEDURE/PACKAGE/...)
3. 批量转换
4. 错误处理与统计

无 AI 依赖，纯规则转换。
"""
from __future__ import annotations

import sys
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# 添加 perlstyle 路径
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PERLSTYLE_PATH = _REPO_ROOT / "ora2pg_py_perlstyle"
if str(_PERLSTYLE_PATH) not in sys.path:
    sys.path.insert(0, str(_PERLSTYLE_PATH))

# 添加 workspace 父目录以便其他模块导入
_WORKSPACE_PARENT = _REPO_ROOT / "workspace"
if str(_WORKSPACE_PARENT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_PARENT))

from perlstyle_engine import detect_and_convert  # noqa: E402


@dataclass
class ConversionResult:
    """单文件转换结果"""
    source_path: Path
    output_path: Optional[Path]
    object_type: str
    success: bool
    error: str = ""
    warnings: List[str] = field(default_factory=list)
    input_size: int = 0
    output_size: int = 0
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source": str(self.source_path),
            "output": str(self.output_path) if self.output_path else None,
            "object_type": self.object_type,
            "success": self.success,
            "error": self.error,
            "warnings": self.warnings,
            "input_size": self.input_size,
            "output_size": self.output_size,
            "duration_ms": round(self.duration_ms, 1),
        }


def detect_type(sql: str) -> str:
    """
    检测 SQL 对象类型

    Returns: FUNCTION / PROCEDURE / PACKAGE / TRIGGER / TABLE / VIEW / TYPE / UNKNOWN
    """
    upper = _stripped_upper(sql)

    if upper.startswith("CREATE OR REPLACE PACKAGE BODY") or upper.startswith("CREATE PACKAGE BODY"):
        return "PACKAGE_BODY"
    if upper.startswith("CREATE OR REPLACE PACKAGE") or upper.startswith("CREATE PACKAGE"):
        return "PACKAGE_SPEC"
    if upper.startswith("CREATE OR REPLACE TRIGGER") or upper.startswith("CREATE TRIGGER"):
        return "TRIGGER"
    if upper.startswith("CREATE OR REPLACE TYPE") or upper.startswith("CREATE TYPE"):
        return "TYPE"
    if upper.startswith("CREATE OR REPLACE VIEW") or upper.startswith("CREATE VIEW"):
        return "VIEW"
    if upper.startswith("CREATE OR REPLACE PROCEDURE") or upper.startswith("CREATE PROCEDURE"):
        return "PROCEDURE"
    if upper.startswith("CREATE OR REPLACE FUNCTION") or upper.startswith("CREATE FUNCTION"):
        return "FUNCTION"
    if upper.startswith("CREATE TABLE") or upper.startswith("CREATE GLOBAL TEMPORARY"):
        return "TABLE"
    if upper.startswith("CREATE SEQUENCE"):
        return "SEQUENCE"
    if upper.startswith("CREATE PUBLIC SYNONYM") or upper.startswith("CREATE SYNONYM"):
        return "SYNONYM"
    if upper.startswith("CREATE INDEX"):
        return "INDEX"
    if upper.startswith("INSERT") or upper.startswith("MERGE") or upper.startswith("UPDATE"):
        return "DATA"
    if "@@" in sql and "BEGIN" not in upper:
        return "DRIVER_SCRIPT"
    return "UNKNOWN"


_COMMENT_LINE = re.compile(r"^\s*--[^\n]*", re.MULTILINE)
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _stripped_upper(sql: str) -> str:
    """剥除注释后取大写，供 detect_type / 关键字匹配使用"""
    no_block = _COMMENT_BLOCK.sub("", sql)
    no_line = _COMMENT_LINE.sub("", no_block)
    return no_line.strip().upper()


def convert_file(source: Path, output_dir: Path) -> ConversionResult:
    """
    转换单个文件

    Args:
        source: Oracle 源文件路径
        output_dir: 输出目录

    Returns:
        ConversionResult
    """
    start = time.perf_counter()
    source = Path(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ConversionResult(
            source_path=source,
            output_path=None,
            object_type="ERROR",
            success=False,
            error=f"读取失败: {e}",
        )

    obj_type = detect_type(text)

    # 驱动脚本和数据脚本直通
    if obj_type in ("DRIVER_SCRIPT", "DATA"):
        output_path = output_dir / source.name
        try:
            output_path.write_text(text, encoding="utf-8")
            return ConversionResult(
                source_path=source,
                output_path=output_path,
                object_type=obj_type,
                success=True,
                input_size=len(text),
                output_size=len(text),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as e:
            return ConversionResult(
                source_path=source,
                output_path=None,
                object_type=obj_type,
                success=False,
                error=f"写入失败: {e}",
                input_size=len(text),
            )

    # 调用 perlstyle 转换
    try:
        converted, warnings = detect_and_convert(text)
    except Exception as e:
        return ConversionResult(
            source_path=source,
            output_path=None,
            object_type=obj_type,
            success=False,
            error=f"转换异常: {type(e).__name__}: {e}",
            input_size=len(text),
        )

    # 写入输出
    output_path = output_dir / source.name
    try:
        output_path.write_text(converted, encoding="utf-8")
        return ConversionResult(
            source_path=source,
            output_path=output_path,
            object_type=obj_type,
            success=True,
            warnings=warnings or [],
            input_size=len(text),
            output_size=len(converted),
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except Exception as e:
        return ConversionResult(
            source_path=source,
            output_path=None,
            object_type=obj_type,
            success=False,
            error=f"写入失败: {e}",
            warnings=warnings or [],
            input_size=len(text),
        )


def convert_files(
    sources: List[Path],
    output_dir: Path,
    patterns: Tuple[str, ...] = ("*.sql", "*.SQL", "*.pks", "*.pkb", "*.fnc", "*.prc"),
    recursive: bool = True,
) -> List[ConversionResult]:
    """
    批量转换文件

    Args:
        sources: 输入文件列表或目录列表
        output_dir: 输出目录
        patterns: 文件匹配模式
        recursive: 是否递归查找子目录

    Returns:
        ConversionResult 列表
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 展开文件列表
    files: List[Path] = []
    for src in sources:
        src = Path(src)
        if src.is_file():
            files.append(src)
        elif src.is_dir():
            for pat in patterns:
                if recursive:
                    files.extend(src.rglob(pat))
                else:
                    files.extend(src.glob(pat))

    # 去重
    seen = set()
    unique = []
    for f in files:
        try:
            key = f.resolve()
        except Exception:
            key = f
        if key not in seen:
            seen.add(key)
            unique.append(f)

    # 转换
    results = []
    for f in sorted(unique):
        try:
            r = convert_file(f, output_dir)
        except Exception as e:
            r = ConversionResult(
                source_path=f,
                output_path=None,
                object_type="ERROR",
                success=False,
                error=f"未捕获异常: {e}",
            )
        results.append(r)

    return results


def summarize(results: List[ConversionResult]) -> dict:
    """汇总转换结果"""
    total = len(results)
    success = sum(1 for r in results if r.success)
    by_type: dict = {}
    for r in results:
        d = by_type.setdefault(r.object_type, {"total": 0, "success": 0})
        d["total"] += 1
        if r.success:
            d["success"] += 1

    total_in = sum(r.input_size for r in results)
    total_out = sum(r.output_size for r in results)
    total_ms = sum(r.duration_ms for r in results)

    return {
        "total": total,
        "success": success,
        "failed": total - success,
        "by_type": by_type,
        "input_bytes": total_in,
        "output_bytes": total_out,
        "total_duration_ms": round(total_ms, 1),
    }
