"""
Level 4: 行为等价验证

用金标准用例（golden cases）在 PG 沙箱里执行真实断言，检验语义是否等价。

用例类型:
  scalar     执行 SELECT，取第一行第一列与期望值比较（支持数值容差）
  rows       执行 SELECT，比较结果集（可只比较部分列）
  procedure  执行 CALL/SELECT 过程，然后跑若干后置断言（校验副作用）

"对象未安装"的用例记为 skipped 而不是 failed：
未安装是 Level 3 的判定，混进 L4 会让两层指标互相污染。
但如果**所有**用例都被跳过，L4 记 0 分并触发 gate 失败——
什么都没验证到，不能算通过。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import CaseResult
from .pg_sandbox import PGSandbox

DEFAULT_TOLERANCE = 0.001


# ---------------------------------------------------------------------------
# 用例加载
# ---------------------------------------------------------------------------

def load_cases(path: Any) -> List[Dict[str, Any]]:
    """从 JSON 文件加载用例库"""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("cases", [])


# ---------------------------------------------------------------------------
# 值比较
# ---------------------------------------------------------------------------

def values_equal(actual: Any, expect: Any, tolerance: float = DEFAULT_TOLERANCE) -> bool:
    """比较实际值与期望值，数值走容差比较"""
    if actual is None or expect is None:
        return actual is None and expect is None

    # 布尔：PG 返回 True/False，期望可能是 "t"/1/True
    if isinstance(actual, bool) or isinstance(expect, bool):
        return bool(actual) == bool(expect)

    # 数值：PG 的 NUMERIC 返回 Decimal
    try:
        return abs(float(actual) - float(expect)) <= tolerance
    except (ValueError, TypeError):
        pass

    return str(actual) == str(expect)


def rows_equal(actual: List[tuple], expect: List[list], columns: Optional[List[int]] = None) -> bool:
    """
    比较结果集。

    Args:
        columns: 只比较这些列的下标（0-based）。用例里用 "columns": [0, 2] 声明。
    """
    if len(actual) != len(expect):
        return False
    for a_row, e_row in zip(actual, expect):
        a = list(a_row)
        e = list(e_row)
        if columns:
            a = [a[i] for i in columns]
            e = [e[i] for i in columns]
        if len(a) != len(e):
            return False
        for av, ev in zip(a, e):
            if not values_equal(av, ev):
                return False
    return True


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def _qualified(sandbox: PGSandbox, name: str) -> str:
    return name if "." in name else f'"{sandbox.schema}"."{name}"'


def run_case(case: Dict[str, Any], sandbox: PGSandbox) -> CaseResult:
    """执行单条用例"""
    cid = case.get("id", "?")
    desc = case.get("description", "")
    ctype = case.get("type", "scalar")
    obj = case.get("object", "")
    tags = case.get("tags", [])

    # 前置检查：目标对象是否装上了
    if obj:
        kind = case.get("object_kind")
        # 未声明 object_kind 时按任意类型查找——默认按 FUNCTION 判定
        # 会让表/视图类用例被误判为"对象不存在"
        exists = (sandbox.object_exists_any(obj) if not kind
                  else sandbox.object_exists(kind, obj))
        if not exists:
            hint = f"（类型 {kind}）" if kind else "（任意类型）"
            return CaseResult(
                case_id=cid, description=desc, status="skipped",
                detail=f"对象 {obj} 未安装到沙箱{hint}，见 Level 3",
                object_name=obj, tags=tags,
            )

    start = time.perf_counter()

    if ctype == "scalar":
        sql = case.get("sql") or f'SELECT {_qualified(sandbox, obj)}()'
        rows, err = sandbox.rows(sql, limit=1)
        if err:
            return CaseResult(
                case_id=cid, description=desc, status="error", expected=case.get("expect"),
                detail=err, duration_ms=(time.perf_counter() - start) * 1000,
                object_name=obj, tags=tags,
            )
        actual = rows[0][0] if rows else None
        ok = values_equal(actual, case.get("expect"), case.get("tolerance", DEFAULT_TOLERANCE))
        return CaseResult(
            case_id=cid, description=desc,
            status="pass" if ok else "fail",
            expected=case.get("expect"), actual=actual,
            detail="" if ok else "返回值与金标准不一致",
            duration_ms=(time.perf_counter() - start) * 1000,
            object_name=obj, tags=tags,
        )

    if ctype == "rows":
        rows, err = sandbox.rows(case["sql"], limit=case.get("limit", 200))
        if err:
            return CaseResult(
                case_id=cid, description=desc, status="error",
                expected=case.get("expect"), detail=err,
                duration_ms=(time.perf_counter() - start) * 1000,
                object_name=obj, tags=tags,
            )
        ok = rows_equal(rows, case.get("expect", []), case.get("columns"))
        actual = [list(r) for r in rows[:10]]
        return CaseResult(
            case_id=cid, description=desc,
            status="pass" if ok else "fail",
            expected=case.get("expect"), actual=actual,
            detail="" if ok else f"结果集不一致（实际 {len(rows)} 行，期望 {len(case.get('expect', []))} 行）",
            duration_ms=(time.perf_counter() - start) * 1000,
            object_name=obj, tags=tags,
        )

    if ctype == "procedure":
        call = case.get("call")
        if not call:
            return CaseResult(
                case_id=cid, description=desc, status="error",
                detail="procedure 用例缺少 call 字段", object_name=obj, tags=tags,
            )
        ok, err, state = sandbox.execute(call)
        elapsed = (time.perf_counter() - start) * 1000
        if not ok:
            return CaseResult(
                case_id=cid, description=desc, status="error",
                detail=f"{err} (SQLSTATE={state})", expected=call,
                duration_ms=elapsed, object_name=obj, tags=tags,
            )
        for pa in case.get("post_assert", []):
            rows, aerr = sandbox.rows(pa["sql"], limit=1)
            if aerr:
                return CaseResult(
                    case_id=cid, description=desc, status="error",
                    detail=f"后置断言执行失败: {aerr}", expected=pa.get("expect"),
                    duration_ms=(time.perf_counter() - start) * 1000,
                    object_name=obj, tags=tags,
                )
            actual = rows[0][0] if rows else None
            if not values_equal(actual, pa.get("expect"), pa.get("tolerance", DEFAULT_TOLERANCE)):
                return CaseResult(
                    case_id=cid, description=desc, status="fail",
                    expected=pa.get("expect"), actual=actual,
                    detail=f"后置断言不通过: {pa.get('description', pa['sql'])}",
                    duration_ms=(time.perf_counter() - start) * 1000,
                    object_name=obj, tags=tags,
                )
        return CaseResult(
            case_id=cid, description=desc, status="pass", expected=call,
            detail=f"{len(case.get('post_assert', []))} 条后置断言通过",
            duration_ms=(time.perf_counter() - start) * 1000,
            object_name=obj, tags=tags,
        )

    return CaseResult(
        case_id=cid, description=desc, status="error",
        detail=f"未知用例类型: {ctype}", object_name=obj, tags=tags,
    )


def run_cases(cases: List[Dict[str, Any]], sandbox: PGSandbox) -> List[CaseResult]:
    return [run_case(c, sandbox) for c in cases]


def score_cases(results: List[CaseResult]) -> Dict[str, Any]:
    """
    计算 L4 分数与统计

    分母只算"可执行"的用例（pass + fail + error），skipped 不计入。
    execution_ratio = 可执行数 / 总数，用来暴露"其实什么都没验"的情况：
    2/65 跑通不等于行为等价通过，必须同时看执行覆盖率。
    """
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errored = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    applicable = passed + failed + errored

    score = round(passed / applicable * 100, 1) if applicable else 0.0
    return {
        "score": score,
        "total": total,
        "applicable": applicable,
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "skipped": skipped,
        "execution_ratio": round(applicable / total, 4) if total else 0.0,
        "no_applicable_cases": applicable == 0 and total > 0,
    }
