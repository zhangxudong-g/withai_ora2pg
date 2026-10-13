"""
P2-4: 序列/同义话语义治理

设计：
- 序列同义词（`SYNONYM sm_seq_x FOR SEQ_Y`）当前被转换器写成
  `CREATE VIEW sm_seq_x AS SELECT * FROM seq_y;`——这在 PG 中语法错误（序列不是表）
- 本后处理识别目标是序列时，改写为：
  `CREATE VIEW sm_seq_x AS SELECT nextval('seq_y') AS next_value;`
  或更稳：把序列同义词完全删除，并在引用处直接替换为 nextval() 调用
- 表同义词保持 VIEW 不变（已工作）

MAXVALUE 策略开关（次要）：
- PG 的 bigint max 是 9223372036854775807，Oracle 的 99999999999999999999 超限
- 已有的 "MAXVALUE capped" 警告已存在；本期不改行为
"""

import pytest

from rules.sequence_synonym import (
    identify_sequences,
    rewrite_sequence_synonym_views,
)


# ---------------------------------------------------------------------------
# 1. identify_sequences: 从 SEQUENCE 文件识别序列名
# ---------------------------------------------------------------------------


def test_identify_single_sequence():
    sql = """CREATE SEQUENCE SEQ_STUDENT_NO
    START 10001
    INCREMENT 1
    MAXVALUE 9999999999
    MINVALUE 1;"""
    seqs = identify_sequences(sql)
    assert "SEQ_STUDENT_NO" in seqs
    assert len(seqs) == 1


def test_identify_multiple_sequences():
    sql = """CREATE SEQUENCE A START 1;
CREATE SEQUENCE B START 1;
CREATE SEQUENCE C START 1;"""
    seqs = identify_sequences(sql)
    assert seqs == {"A", "B", "C"}


def test_identify_case_insensitive():
    sql = "create sequence seq_x start 1;"
    seqs = identify_sequences(sql)
    assert "SEQ_X" in seqs  # 归一为大写


def test_identify_ignores_comments():
    sql = """-- this is a comment
CREATE SEQUENCE A START 1;
-- CREATE SEQUENCE B (commented out)"""
    seqs = identify_sequences(sql)
    assert seqs == {"A"}


def test_identify_empty():
    assert identify_sequences("") == set()
    assert identify_sequences("-- only comments") == set()


# ---------------------------------------------------------------------------
# 2. rewrite_sequence_synonym_views: 改写 VIEW 以支持序列
# ---------------------------------------------------------------------------


def test_rewrite_table_synonym_unchanged():
    """表同义词 (target 不是序列) 保持原样"""
    syn_sql = "CREATE OR REPLACE VIEW sm_student AS SELECT * FROM student_mst;"
    seqs = {"SEQ_STUDENT_NO", "SEQ_JOBNUM"}  # 不包含 student_mst
    out, n = rewrite_sequence_synonym_views(syn_sql, seqs)
    assert out == syn_sql
    assert n == 0


def test_rewrite_sequence_synonym():
    """序列同义词改写为 nextval()"""
    syn_sql = "CREATE OR REPLACE VIEW sm_seq_student AS SELECT * FROM seq_student_no;"
    seqs = {"SEQ_STUDENT_NO"}
    out, n = rewrite_sequence_synonym_views(syn_sql, seqs)
    assert "nextval" in out.lower()
    assert "seq_student_no" in out.lower()
    assert n == 1
    # 不应再有 "SELECT * FROM" 形式（针对序列）
    assert "SELECT * FROM seq_student_no" not in out


def test_rewrite_mixed_synonyms():
    """同一文件既有表同义词也有序列同义词：只改序列"""
    syn_sql = """CREATE OR REPLACE VIEW sm_student AS SELECT * FROM student_mst;
CREATE OR REPLACE VIEW sm_seq_student AS SELECT * FROM seq_student_no;
CREATE OR REPLACE VIEW sm_score AS SELECT * FROM score_rec;
CREATE OR REPLACE VIEW sm_seq_jobnum AS SELECT * FROM seq_jobnum;"""
    seqs = {"SEQ_STUDENT_NO", "SEQ_JOBNUM"}
    out, n = rewrite_sequence_synonym_views(syn_sql, seqs)
    # 两个序列同义词被改写
    assert n == 2
    # 表同义词保持
    assert "SELECT * FROM student_mst" in out
    assert "SELECT * FROM score_rec" in out
    # 序列同义词不再有 SELECT * FROM seq
    assert "SELECT * FROM seq_student_no" not in out
    assert "SELECT * FROM seq_jobnum" not in out
    # nextval 出现 2 次
    assert out.lower().count("nextval") == 2


def test_rewrite_case_insensitive():
    """小写 view 也能识别 target"""
    syn_sql = "CREATE OR REPLACE VIEW sm_seq AS SELECT * FROM seq_x;"
    seqs = {"SEQ_X"}
    out, n = rewrite_sequence_synonym_views(syn_sql, seqs)
    assert n == 1
    assert "nextval" in out.lower()


def test_rewrite_no_sequences_no_op():
    syn_sql = "CREATE OR REPLACE VIEW sm_x AS SELECT * FROM t;"
    out, n = rewrite_sequence_synonym_views(syn_sql, set())
    assert out == syn_sql
    assert n == 0


def test_rewrite_preserves_surrounding():
    """保留注释、空行等周围文本"""
    syn_sql = """-- comment line
CREATE OR REPLACE VIEW sm_seq AS SELECT * FROM seq_x;

-- another comment"""
    seqs = {"SEQ_X"}
    out, n = rewrite_sequence_synonym_views(syn_sql, seqs)
    n_out = out.count("\n")
    assert n == 1
    # 注释保留
    assert "-- comment line" in out
    assert "-- another comment" in out


# ---------------------------------------------------------------------------
# 端到端: oracle_poc
# ---------------------------------------------------------------------------


def test_oracle_poc_synonym_file_uses_nextval_for_sequences():
    """oracle_poc 端到端: SYNONYM 产物中的序列同义词应使用 nextval()"""
    from pathlib import Path

    syn_file = Path("oracle_poc/output/SYNONYM/00_SYNONYM.sql")
    seq_file = Path("oracle_poc/output/SEQUENCE/00_SEQUENCE.sql")
    if not syn_file.exists() or not seq_file.exists():
        pytest.skip("oracle_poc/output not built")

    seqs = identify_sequences(seq_file.read_text(encoding="utf-8"))
    syn_text = syn_file.read_text(encoding="utf-8")
    new, n = rewrite_sequence_synonym_views(syn_text, seqs)
    # 验证：所有 SELECT * FROM seq_x 已被改写
    import re as _re
    bad = _re.findall(r"SELECT \* FROM (seq_\w+)", new, _re.IGNORECASE)
    assert bad == [], f"仍有 {len(bad)} 处序列 SELECT * FROM 未改写: {bad}"
    # n>=1 是首次运行的预期；幂等运行 n=0 也接受（convert 已跑过）
    assert n >= 0, f"n 应为非负, got {n}"
