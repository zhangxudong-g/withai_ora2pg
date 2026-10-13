"""
oracle_poc 金标准用例库生成器

为什么要有生成器而不是手写期望值：
1. 手填的数字无法复现和审计，评审时只能"相信"；
2. 本机没有可用的 Oracle 实例（无 oracledb 驱动，Docker 守护进程未运行），
   无法从真实 Oracle 侧抓取基线；
3. oracle_poc 的数据是固定的（00_TEST_DATA.sql，8 个学生 / 24 条成绩），
   期望值完全可以从 DDL + 数据 + 函数源码语义推导出来。

因此这里的做法是：解析 00_TEST_DATA.sql，按 oracle_poc 各函数的源码语义
在 Python 里重算一遍期望值，写入 golden/oracle_poc_cases.json。
每个用例都带 provenance 字段标明推导来源。

oracle_poc 没有可用的实时 Oracle 实例，所以这属于"源码推导基线"，
不是"Oracle 实测基线"。等 Oracle 环境可用时，
应改用 differential 模式（同一用例同时跑 Oracle 与 PG 比对）复核本文件。

用法:
    cd D:/JIP/poc
    python pipeline/verification/golden/build_oracle_poc_cases.py
"""
from __future__ import annotations

import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
ORACLE_POC = ROOT / "oracle_poc" / "01.SQL"
DATA_FILE = ORACLE_POC / "DATA" / "00_TEST_DATA.sql"
OUT_FILE = Path(__file__).resolve().parent / "oracle_poc_cases.json"

PROVENANCE = "derived_from_source+data"


# ---------------------------------------------------------------------------
# 数据解析
# ---------------------------------------------------------------------------

_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _split_values(raw: str) -> List[str]:
    """按逗号切分 VALUES 内容，正确处理单引号字符串"""
    out: List[str] = []
    buf: List[str] = []
    in_quote = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if in_quote:
            if ch == "'":
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_quote = False
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_quote = True
            i += 1
            continue
        if ch == ",":
            out.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return out


def _coerce(v: str) -> Any:
    if v.upper() == "NULL":
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def parse_inserts(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """解析测试数据文件，返回 {表名: [行字典]}"""
    sql = path.read_text(encoding="utf-8")
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for m in _INSERT_RE.finditer(sql):
        table = m.group(1).upper()
        cols = [c.strip().upper() for c in m.group(2).split(",")]
        vals = [_coerce(v) for v in _split_values(m.group(3))]
        if len(cols) != len(vals):
            print(f"  [警告] {table} 列数({len(cols)})与值数({len(vals)})不符，已跳过")
            continue
        tables.setdefault(table, []).append(dict(zip(cols, vals)))
    return tables


# ---------------------------------------------------------------------------
# Oracle 语义
# ---------------------------------------------------------------------------

def oracle_round2(x: float) -> float:
    """
    Oracle ROUND(x, 2)：四舍五入，半数远离零。

    Python 内置 round() 用的是银行家舍入（round-half-even），
    95.125 -> 95.12 与 Oracle 的 95.13 不同，必须用 Decimal ROUND_HALF_UP。
    """
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# 各函数的 Oracle 语义重实现（严格对照 01.SQL/FUNCTION/00_FUNCTION.sql 源码）

def f_get_total_score(data, student_no: Optional[int], exam_type: Optional[int] = None) -> Any:
    """VALIDATE_STUDENT_NO 失败返回 0；否则 SUM(SCORE)"""
    students = data.get("STUDENT_MST", [])
    valid = any(
        r["STUDENT_NO"] == student_no and r.get("DELETE_FLG") == 0 for r in students
    )
    if not valid:
        return 0
    rows = [r for r in data.get("SCORE_REC", []) if r["STUDENT_NO"] == student_no]
    if exam_type is not None:
        rows = [r for r in rows if r["EXAM_TYPE"] == exam_type]
    return sum(r["SCORE"] for r in rows)


def f_get_avg_score(data, student_no: int, subject_cd: Optional[int] = None) -> Any:
    """subject_cd 非空 -> ROUND(AVG(SCORE),2)；否则按学分加权后 ROUND(...,2)"""
    scores = data.get("SCORE_REC", [])
    subjects = {s["SUBJECT_CD"]: s for s in data.get("SUBJECT_MST", [])}

    if subject_cd is not None:
        vals = [r["SCORE"] for r in scores
                if r["STUDENT_NO"] == student_no and r["SUBJECT_CD"] == subject_cd]
        return oracle_round2(sum(vals) / len(vals)) if vals else 0

    weighted_sum = 0
    total_weight = 0
    for r in scores:
        if r["STUDENT_NO"] != student_no:
            continue
        sub = subjects.get(r["SUBJECT_CD"])
        if not sub or sub.get("DELETE_FLG") != 0:
            continue
        credit = sub.get("CREDIT") or 1
        weighted_sum += r["SCORE"] * credit
        total_weight += credit
    return oracle_round2(weighted_sum / total_weight) if total_weight > 0 else 0


def f_get_grade_level(data, score: float) -> int:
    """阈值优先取 SYSTEM_CONFIG，缺失则回落到 90/80/60"""
    cfg = {r["CONFIG_CD"]: r["CONFIG_VALUE"] for r in data.get("SYSTEM_CONFIG", [])}

    def threshold(kbn: str, default: int) -> int:
        raw = cfg.get(f"GRADE_LEVEL_{kbn}")
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default

    exc = threshold("EXCELLENT", 90)
    good = threshold("GOOD", 80)
    pas = threshold("PASS", 60)
    if score >= exc:
        return 1
    if score >= good:
        return 2
    if score >= pas:
        return 3
    return 4


def f_calc_rank(data, student_no: int, grade: int, class_no: int, rank_type: int) -> int:
    """
    动态 SQL 语义：组内总分严格高于我的学生数 + 1。

    组 = GRADE=grade，且 rank_type=1 时再加 CLASS_NO=class_no。
    SQL 用 (+ ) 外连接，学生无成绩时 SUM(NVL(SCORE,0)) = 0，同样参与排名。
    """
    my_total = f_get_total_score(data, student_no, None)

    totals: Dict[int, int] = {}
    for s in data.get("STUDENT_MST", []):
        if s.get("DELETE_FLG") != 0 or s.get("GRADE") != grade:
            continue
        if rank_type == 1 and s.get("CLASS_NO") != class_no:
            continue
        totals[s["STUDENT_NO"]] = f_get_total_score(data, s["STUDENT_NO"], None)

    return sum(1 for t in totals.values() if t > my_total) + 1


# ---------------------------------------------------------------------------
# 用例构造
# ---------------------------------------------------------------------------

def build_cases(data) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    students = sorted(r["STUDENT_NO"] for r in data.get("STUDENT_MST", []))
    subject_cds = sorted({r["SUBJECT_CD"] for r in data.get("SCORE_REC", [])})

    # ---- F_GET_GRADE_LEVEL：阈值边界（纯函数，最容易暴露转换错误）----
    for score, level, why in [
        (100, 1, "满分 -> 优秀"),
        (90, 1, "优秀边界 90 含等号"),
        (89.99, 2, "优秀边界下方 -> 良好"),
        (85, 2, "典型良好"),
        (80, 2, "良好边界 80 含等号"),
        (79.99, 3, "良好边界下方 -> 及格"),
        (65, 3, "典型及格"),
        (60, 3, "及格边界 60 含等号"),
        (59.99, 4, "及格边界下方 -> 不及格"),
        (55, 4, "典型不及格"),
        (0, 4, "零分"),
        (-10, 4, "负分走异常/兜底分支"),
    ]:
        expect = f_get_grade_level(data, score)
        assert expect == level, f"F_GET_GRADE_LEVEL({score}) 推导={expect} 期望={level}"
        cases.append({
            "id": f"GRADE_{int(score)}_{len(cases):02d}",
            "object": "f_get_grade_level",
            "object_kind": "FUNCTION",
            "type": "scalar",
            "sql": f"SELECT f_get_grade_level({score})",
            "expect": level,
            "description": f"F_GET_GRADE_LEVEL({score}) = {level}（{why}）",
            "tags": ["pure_function", "threshold_boundary", "nested_4_level"],
            "provenance": PROVENANCE,
        })

    # ---- F_GET_TOTAL_SCORE：动态 SQL + 嵌套校验 ----
    for stu in students:
        expect = f_get_total_score(data, stu, None)
        cases.append({
            "id": f"TOTAL_{stu}",
            "object": "f_get_total_score",
            "object_kind": "FUNCTION",
            "type": "scalar",
            "sql": f"SELECT f_get_total_score({stu}, NULL)",
            "expect": expect,
            "description": f"学生 {stu} 全科目总分 = {expect}",
            "tags": ["dynamic_sql", "nested", "aggregate"],
            "provenance": PROVENANCE,
        })

    # 按考试类型过滤（验证动态 SQL 的条件拼接分支）
    exam_types = sorted({r["EXAM_TYPE"] for r in data.get("SCORE_REC", [])})
    for stu in students[:3]:
        for et in exam_types:
            expect = f_get_total_score(data, stu, et)
            cases.append({
                "id": f"TOTAL_EXAM_{stu}_{et}",
                "object": "f_get_total_score",
                "object_kind": "FUNCTION",
                "type": "scalar",
                "sql": f"SELECT f_get_total_score({stu}, {et})",
                "expect": expect,
                "description": f"学生 {stu} 考试类型 {et} 的总分 = {expect}",
                "tags": ["dynamic_sql", "filter_branch"],
                "provenance": PROVENANCE,
            })

    # 无效输入：走 VALIDATE_STUDENT_NO 的 CHECK_FORMAT / 查表失败分支
    for stu, why in [(99999, "不存在的学生"), (0, "编号为 0，CHECK_FORMAT 拒绝"),
                     (-1, "负数编号，CHECK_FORMAT 拒绝")]:
        cases.append({
            "id": f"TOTAL_INVALID_{abs(stu)}",
            "object": "f_get_total_score",
            "object_kind": "FUNCTION",
            "type": "scalar",
            "sql": f"SELECT f_get_total_score({stu}, NULL)",
            "expect": 0,
            "description": f"F_GET_TOTAL_SCORE({stu}) = 0（{why}）",
            "tags": ["edge_case", "validation_branch"],
            "provenance": PROVENANCE,
        })

    # ---- F_GET_AVG_SCORE：学分加权 + 单科 AVG 两个分支 ----
    for stu in students:
        expect = f_get_avg_score(data, stu, None)
        cases.append({
            "id": f"AVGW_{stu}",
            "object": "f_get_avg_score",
            "object_kind": "FUNCTION",
            "type": "scalar",
            "sql": f"SELECT f_get_avg_score({stu}, NULL)",
            "expect": expect,
            "tolerance": 0.005,
            "description": f"学生 {stu} 学分加权平均分 = {expect}",
            "tags": ["weighted_avg", "cursor_loop", "nested"],
            "provenance": PROVENANCE,
        })

    for stu in students[:4]:
        for cd in subject_cds[:2]:
            expect = f_get_avg_score(data, stu, cd)
            cases.append({
                "id": f"AVGS_{stu}_{cd}",
                "object": "f_get_avg_score",
                "object_kind": "FUNCTION",
                "type": "scalar",
                "sql": f"SELECT f_get_avg_score({stu}, {cd})",
                "expect": expect,
                "tolerance": 0.005,
                "description": f"学生 {stu} 科目 {cd} 平均分 = {expect}",
                "tags": ["single_subject", "dynamic_sql"],
                "provenance": PROVENANCE,
            })

    # ---- F_CALC_RANK：动态 SQL + (+) 外连接 + HAVING ----
    grades = sorted({r["GRADE"] for r in data.get("STUDENT_MST", [])})
    classes = sorted({r["CLASS_NO"] for r in data.get("STUDENT_MST", [])})
    for stu in students:
        g, c = next((r["GRADE"], r["CLASS_NO"]) for r in data["STUDENT_MST"]
                    if r["STUDENT_NO"] == stu)
        expect = f_calc_rank(data, stu, g, c, 1)
        cases.append({
            "id": f"RANK_CLS_{stu}",
            "object": "f_calc_rank",
            "object_kind": "FUNCTION",
            "type": "scalar",
            "sql": f"SELECT f_calc_rank({stu}, {g}, {c}, 1)",
            "expect": expect,
            "description": f"学生 {stu} 在 {g} 年 {c} 班的排名 = {expect}",
            "tags": ["rank", "dynamic_sql", "outer_join"],
            "provenance": PROVENANCE,
        })
        if g in grades and len(classes) > 1:
            expect_g = f_calc_rank(data, stu, g, classes[0], 2)
            cases.append({
                "id": f"RANK_GRD_{stu}",
                "object": "f_calc_rank",
                "object_kind": "FUNCTION",
                "type": "scalar",
                "sql": f"SELECT f_calc_rank({stu}, {g}, {classes[0]}, 2)",
                "expect": expect_g,
                "description": f"学生 {stu} 在 {g} 年级的排名 = {expect_g}",
                "tags": ["rank", "dynamic_sql", "group_branch"],
                "provenance": PROVENANCE,
            })

    # ---- 数据装载完整性：表行数 ----
    for table, kind in [("STUDENT_MST", "TABLE"), ("SCORE_REC", "TABLE"),
                        ("SUBJECT_MST", "TABLE"), ("TEACHER_MST", "TABLE"),
                        ("SYSTEM_CONFIG", "TABLE")]:
        n = len(data.get(table, []))
        if not n:
            continue
        cases.append({
            "id": f"DATA_{table}",
            "object": table.lower(),
            "object_kind": kind,
            "type": "scalar",
            "sql": f"SELECT count(*) FROM {table.lower()}",
            "expect": n,
            "description": f"表 {table} 应装载 {n} 行",
            "tags": ["data_load", "integrity"],
            "provenance": PROVENANCE,
        })

    # ---- 视图：与源库定义对拍（只比行数，避免列序差异误报）----
    for view, src in [("V_STUDENT_SCORE", None), ("V_SCORE_RANK", None)]:
        cases.append({
            "id": f"VIEW_{view}",
            "object": view.lower(),
            "object_kind": "VIEW",
            "type": "scalar",
            "sql": f"SELECT count(*) FROM {view.lower()}",
            "expect": None,          # 期望值由下方 --view-expect 填充
            "description": f"视图 {view} 可查询（行数待校验）",
            "tags": ["view"],
            "provenance": "placeholder",
        })

    return cases


def main():
    if not DATA_FILE.exists():
        print(f"找不到测试数据: {DATA_FILE}")
        return 1

    print(f"解析测试数据: {DATA_FILE}")
    data = parse_inserts(DATA_FILE)
    for t, rows in sorted(data.items()):
        print(f"  {t}: {len(rows)} 行")

    cases = build_cases(data)

    # 视图期望值：V_STUDENT_SCORE 关联学生与成绩，V_SCORE_RANK 同源
    n_students_with_score = len({
        r["STUDENT_NO"] for r in data.get("SCORE_REC", [])
    } & {r["STUDENT_NO"] for r in data.get("STUDENT_MST", [])})
    view_expect = {"V_STUDENT_SCORE": n_students_with_score, "V_SCORE_RANK": n_students_with_score}
    for c in cases:
        if c.get("provenance") == "placeholder" and c["object"].upper() in view_expect:
            c["expect"] = view_expect[c["object"].upper()]
            c["description"] = f"视图 {c['object'].upper()} 行数 = {c['expect']}"
            c["provenance"] = PROVENANCE

    payload = {
        "version": "1.0",
        "target": "oracle_poc",
        "generated_by": "pipeline/verification/golden/build_oracle_poc_cases.py",
        "source_data": "oracle_poc/01.SQL/DATA/00_TEST_DATA.sql",
        "note": (
            "期望值由 oracle_poc 的 DDL + 测试数据 + 函数源码语义推导，"
            "非 Oracle 实例实测。具备 Oracle 环境时应改用 differential 模式复核。"
        ),
        "case_count": len(cases),
        "cases": cases,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n已生成 {len(cases)} 条用例 -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
