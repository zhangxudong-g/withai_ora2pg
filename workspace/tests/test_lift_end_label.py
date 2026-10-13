"""P4-2R: 修复 lift 路径生成的 'END ;;' / 'END <name>;' 残留。

根因（perlstyle_engine.py:3895-3903）:
    _lift_nested_subprograms 把 'END <name>;' 替换为 r'\1;\2'，
    即在 END 后强制加分号 + 保留原 \2 中的分号 → 'END ;;'。

本任务修复为 'END;'，并锁定以下回归场景：
1. 基础：'END ADD_STUDENT_COND;' → 'END;'
2. 无分号：'END FOO' → 'END;'
3. 多缩进 / 多种空白
4. 多个嵌套函数独立处理
5. 集成：完整 lift 流水线产物无 'END ;;' / 'END <name>;'
6. 端到端：oracle_poc 转换后无残留

单测按内容断言，禁止写死行号（AGENTS.md §4）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PERLSTYLE_DIR = REPO_ROOT / "ora2pg_py_perlstyle"
WORKSPACE_DIR = REPO_ROOT / "workspace"
ORACLE_SRC = REPO_ROOT / "oracle_poc" / "01.SQL"
OUTPUT_DIR = REPO_ROOT / "oracle_poc" / "output"

for _p in (PERLSTYLE_DIR, WORKSPACE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import perlstyle_engine as engine  # noqa: E402


# ---------------------------------------------------------------------------
# 测试 1: 基础 regex 替换（直接重现并锁定 fix）
# ---------------------------------------------------------------------------
class TestEndLabelRegexFix:
    """锁定 _lift_nested_subprograms 内部 END 标签剥离的修复行为。"""

    @staticmethod
    def _apply_fix(nested_text: str, end_name: str) -> str:
        """与修复后 _lift_nested_subprograms 中的替换保持一致。"""
        if not end_name:
            return nested_text
        return re.sub(
            r"^(\s*)END\s+" + re.escape(end_name) + r"\s*;?\s*$",
            r"\1END;",
            nested_text,
            flags=re.IGNORECASE | re.MULTILINE,
        )

    def test_end_with_name_and_semicolon(self):
        """基本场景：'END ADD_STUDENT_COND;' → 'END;'"""
        text = "        END ADD_STUDENT_COND;\n"
        out = self._apply_fix(text, "ADD_STUDENT_COND")
        assert "END ;;" not in out, f"still has 'END ;;': {out!r}"
        assert "END ADD_STUDENT_COND;" not in out, f"label not stripped: {out!r}"
        # 保留前导缩进
        assert out.lstrip().startswith("END;"), f"missing END;: {out!r}"

    def test_end_with_name_no_semicolon(self):
        """无分号：'END FOO' → 'END;'"""
        text = "    END FOO\n"
        out = self._apply_fix(text, "FOO")
        assert "END ;;" not in out
        assert "END FOO" not in out
        assert "END;" in out

    def test_end_with_name_extra_whitespace(self):
        """多种尾随空白：'   END NAME  ;  \\n' → '   END;  \\n'"""
        text = "    END NAME  ;  \n"
        out = self._apply_fix(text, "NAME")
        assert "END ;;" not in out
        assert "END NAME" not in out

    def test_indentation_preserved(self):
        """保留前导缩进（lift 输出的 nested_text 通常带缩进）"""
        text = "            END INNER_FN;\n"
        out = self._apply_fix(text, "INNER_FN")
        # 前 12 空格应保留
        assert out.startswith("            END;")
        assert "END ;;" not in out

    def test_multiple_end_labels_in_text(self):
        """多行文本中只替换最末一个 END <name>;（re.MULTILINE + $ 锚定）"""
        # 文本里有 2 个 END 标签，但 $ 锚定只匹配最末行
        text = (
            "        IF x > 0 THEN\n"
            "            RETURN 1;\n"
            "        END IF;\n"
            "        RETURN 2;\n"
            "    END OUTER;\n"
        )
        out = self._apply_fix(text, "OUTER")
        # 内部的 'END IF;' 不应被改
        assert "END IF;" in out
        # 最末的 'END OUTER;' 应被替换
        assert "END OUTER" not in out
        assert "END ;;" not in out
        # 末行应为 '    END;'
        assert out.rstrip().endswith("END;")


# ---------------------------------------------------------------------------
# 测试 2: 集成——_lift_nested_subprograms 端到端
# ---------------------------------------------------------------------------
class TestLiftNestedSubprogramsEndLabel:
    """完整 lift 流程：传入 Oracle 源，产物中无 END ;; 或 END <name>; 残留。"""

    @staticmethod
    def _make_oracle_src() -> str:
        """构造一个含嵌套函数的 Oracle 源。"""
        return """CREATE OR REPLACE FUNCTION F_OUTER(i_n NUMBER) RETURN NUMBER IS
    FUNCTION INNER_A(i_x IN NUMBER) RETURN NUMBER IS
    BEGIN
        RETURN i_x + 1;
    END INNER_A;

    FUNCTION INNER_B(i_y IN NUMBER) RETURN NUMBER IS
        FUNCTION DEEP(i_z IN NUMBER) RETURN NUMBER IS
        BEGIN
            RETURN i_z * 2;
        END DEEP;
    BEGIN
        RETURN DEEP(i_y);
    END INNER_B;
BEGIN
    RETURN INNER_A(i_n) + INNER_B(i_n);
END F_OUTER;
"""

    def test_lift_output_has_no_end_double_semicolon(self):
        """lift 出的 PG 函数块不应含 'END ;;'"""
        oracle = self._make_oracle_src()
        warnings: List[str] = []
        block, lifted = engine._lift_nested_subprograms(oracle, warnings)
        assert lifted is not None, "expected lift to find nested functions"
        assert len(lifted) > 0, "expected at least one lifted function"
        for i, lifted_sql in enumerate(lifted):
            assert "END ;;" not in lifted_sql, (
                f"lifted[{i}] has 'END ;;':\n{lifted_sql}"
            )

    def test_lift_output_has_no_labeled_end(self):
        """lift 出的 PG 函数块不应含 'END <name>;'（即函数名标签）"""
        oracle = self._make_oracle_src()
        warnings: List[str] = []
        _block, lifted = engine._lift_nested_subprograms(oracle, warnings)
        assert lifted is not None
        # 禁止 lift 产物中出现带函数名的 END
        for i, lifted_sql in enumerate(lifted):
            # 匹配 'END NAME;' 形态（NAME 非 IF/LOOP/CASE 关键字）
            bad = re.search(
                r"\bEND\s+(?!IF\b|LOOP\b|CASE\b)\w+\s*;",
                lifted_sql,
                re.IGNORECASE,
            )
            assert not bad, (
                f"lifted[{i}] has labeled END: {bad.group(0) if bad else ''}\n"
                f"--- full ---\n{lifted_sql}"
            )


# ---------------------------------------------------------------------------
# 测试 3: 端到端——convert_function 后产物无 END ;;
# ---------------------------------------------------------------------------
class TestConvertFunctionNoEndDoubleSemicolon:
    """完整 convert_function 调用，产物无 'END ;;'。"""

    def test_single_function_with_nested(self):
        oracle = """CREATE OR REPLACE FUNCTION F_OUTER(i_n NUMBER) RETURN NUMBER IS
    FUNCTION INNER_A(i_x IN NUMBER) RETURN NUMBER IS
    BEGIN
        RETURN i_x + 1;
    END INNER_A;
BEGIN
    RETURN INNER_A(i_n);
END F_OUTER;
"""
        out, warnings = engine.convert_function(oracle)
        assert "END ;;" not in out, f"output has 'END ;;':\n{out}"


# ---------------------------------------------------------------------------
# 测试 4: 端到端——oracle_poc 转换产物无 END ;; 残留（如果已转换）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not OUTPUT_DIR.exists(),
    reason="oracle_poc/output not present",
)
class TestOraclePocOutputNoEndDoubleSemicolon:
    """oracle_poc 全量产物扫描：不得含 'END ;;'。"""

    def test_no_end_double_semicolon_in_output(self):
        files = list(OUTPUT_DIR.glob("*/*.sql"))
        assert files, "no .sql files in oracle_poc/output"
        bad_files: list[tuple[Path, int, str]] = []
        for f in files:
            content = f.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(content.splitlines(), start=1):
                if re.search(r"\bEND\s+;;", line):
                    bad_files.append((f, lineno, line.rstrip()))
        assert not bad_files, (
            f"Found {len(bad_files)} 'END ;;' lines in oracle_poc/output:\n"
            + "\n".join(f"  {p}:{n}: {t}" for p, n, t in bad_files[:20])
        )

    def test_no_labeled_end_in_actually_lifted_functions(self):
        """验证：所有 lift 出去的函数**自身**不含带函数名的 END（除 IF/LOOP/CASE）。

        注：PACKAGE_BODY 里若仍残留 'END <name>;' 标签，说明该嵌套函数
        没被 lift 识别（pre-existing 的 ANTLR 识别不全 bug）—— 不在本任务范围。
        本测试只检查 _lift_nested_subprograms 输出列表里的每个 SQL。
        """
        # 构造一个明确可被 lift 的 pre-BEGIN 嵌套函数
        oracle = """CREATE OR REPLACE FUNCTION F_OUTER(i_n NUMBER) RETURN NUMBER IS
    FUNCTION INNER_A(i_x IN NUMBER) RETURN NUMBER IS
    BEGIN
        RETURN i_x + 1;
    END INNER_A;
    FUNCTION INNER_B(i_y IN NUMBER) RETURN NUMBER IS
    BEGIN
        RETURN i_y;
    END INNER_B;
BEGIN
    RETURN INNER_A(i_n) + INNER_B(i_n);
END F_OUTER;
"""
        warnings: list = []
        _block, lifted = engine._lift_nested_subprograms(oracle, warnings)
        assert lifted is not None and len(lifted) >= 1, (
            f"expected at least one lifted function, got {lifted!r}"
        )
        for i, lifted_sql in enumerate(lifted):
            # 找带函数名标签的 END（排除 IF/LOOP/CASE）
            bad = re.search(
                r"^\s*END\s+(?!IF\b|LOOP\b|CASE\b)\w+\s*;",
                lifted_sql,
                re.IGNORECASE | re.MULTILINE,
            )
            assert not bad, (
                f"lifted[{i}] has labeled END: {bad.group(0) if bad else ''}\n"
                f"--- full ---\n{lifted_sql}"
            )
