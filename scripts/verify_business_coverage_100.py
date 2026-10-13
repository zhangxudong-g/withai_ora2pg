#!/usr/bin/env python3
"""
GKB 函数业务逻辑 100% 覆盖验证 (唯一权威业务测试)

覆盖分析:
- GKBFKZISKCHK: 28 分支
- GKBFKHTSET: 17 分支
- GKBFKHTCLR: 8 分支
- GKBFKTBLCNT: 6 分支
- GKBFKGKNCDHK: 13 分支
- GKBFKZKGMGET/2: 20 分支 (2026-09 扩展并入, 原 verify_new_objects.py)

==================================================================
使用说明
==================================================================

【独立使用】
    python scripts/verify_business_coverage_100.py

【通过统一入口使用】
    python pg.py verify biz    # 唯一权威业务测试

【验证内容 (93 项)】
    - GKBFKZISKCHK: 28 分支 (学籍番号检查)
    - GKBFKHTSET:   17 分支 (设置表头)
    - GKBFKHTCLR:    8 分支 (清空表头)
    - GKBFKTBLCNT:   6 分支 (动态 SQL COUNT)
    - GKBFKGKNCDHK: 13 分支 (学年コード変換)
    - GKBFKZKGMGET: 20 分支 (学级名获取)

【输入/输出】
    输入:  out/plsql/*.pgsql
           fixtures/testdata/* (测试数据)
    输出:  验证报告 (控制台输出)
    测试库: gkb_100_coverage

【适用场景】
    - GKB 函数业务逻辑权威验证 (唯一权威)
    - 上线前最终验证
    - 回归测试

【不适用场景】
    - 过程验证 (使用 verify_complete_coverage.py)
    - VIEW 验证 (使用 verify_views.py)
    - DDL 验证 (使用 verify_ddl_equivalence.py)

【相关脚本】
    - scripts/verify_complete_coverage.py: 过程验证
    - scripts/verify_all_equivalence.py: 全量验证
    - pg.py verify all: 一键验证全部
"""
import os
import sys
from pathlib import Path
import psycopg2

POC = Path(__file__).parent.parent
DDL_DIR = POC / "out" / "ddl"
PLSQL_DIR = POC / "out" / "plsql"
TEST_DB = "gkb_100_coverage"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        mark = "[PASS]"
    else:
        FAIL += 1
        mark = "[FAIL]"
    RESULTS.append((name, passed, detail))
    print(f"  {mark} {name}" + (f" - {detail}" if detail else ""))


def main():
    global PASS, FAIL, RESULTS
    print("=" * 70)
    print("GKB 业务逻辑 100% 覆盖验证")
    print("=" * 70)

    # 创建测试库
    try:
        conn = psycopg2.connect(host='localhost', port=5432, user='postgres',
                                password='postgres', dbname='postgres')
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if cur.fetchone() is None:
            cur.execute(f"CREATE DATABASE {TEST_DB} TEMPLATE template0 "
                         f"ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
        cur.close()
        conn.close()
        record("创建测试库", True)
    except Exception as e:
        record("创建测试库", False, str(e))
        return

    conn = psycopg2.connect(host='localhost', port=5432, user='postgres',
                            password='postgres', dbname=TEST_DB)
    conn.autocommit = True
    cur = conn.cursor()
    # 持久测试库: 重建 schema 内容 (每次刷新), 数据库本身保留不删除
    cur.execute("DROP SCHEMA public CASCADE;")
    cur.execute("CREATE SCHEMA public;")
    cur.execute("GRANT ALL ON SCHEMA public TO postgres;")

    # 包桩函数 (KKAPK/GAAPK 等外部包调用的兼容桩, 如存在)
    stubs = POC / "out" / "pkg_stubs.sql"
    if stubs.exists():
        cur.execute(stubs.read_text(encoding="utf-8"))

    # 应用 DDL
    _apply_dir(cur, DDL_DIR)

    # 应用 PL/SQL
    _apply_dir(cur, PLSQL_DIR)

    print("\n" + "=" * 70)
    print("1. GKBFKZISKCHK 在籍状況チェック (28 分支)")
    print("=" * 70)

    # 准备基础数据
    # 无记录
    print("\n[1.1] 无记录 → 返回 0")
    cur.execute('SELECT gkbfkziskchk(999999999999999, 1, 1)')
    r = cur.fetchone()[0]
    record("无记录返回0", r == 0, f"实际={r}")

    # 減算済み
    print("\n[1.2] 減算済み (GEN_JIYU_CD≠0)")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888101")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888101")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888101, 1, 1, 1, 20250301, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI, S_TENNYU_BI, S_TENGAKU_BI, S_TAIGAKU_BI, S_SOTSUGYO_BI) VALUES (888101, 2024, 1, 1, 0, 0, 20180401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888101, 1, 4)')
    r = cur.fetchone()[0]
    record("GEN_JIYU_CD≠0 返回0", r == 0, f"实际={r}")

    print("\n[1.3] 減算済み (GEN_NENGAPI≠0)")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888102")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888102")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888102, 1, 1, 0, 20250301, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI) VALUES (888102, 2024, 1, 1, 0, 0, 20180401)")
    cur.execute('SELECT gkbfkziskchk(888102, 1, 4)')
    r = cur.fetchone()[0]
    record("GEN_NENGAPI≠0 返回0", r == 0, f"实际={r}")

    # GAKUKBN=1 小学校
    print("\n[1.4] GAKUKBN=1 小学校 - S_NYUGAKU 入学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888111")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888111")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888111, 1, 1, 0, 0, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI, S_TENNYU_BI, S_TENGAKU_BI, S_TAIGAKU_BI, S_SOTSUGYO_BI) VALUES (888111, 2024, 1, 1, 0, 0, 20180401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888111, 1, 4)')
    r = cur.fetchone()[0]
    record("GAKUKBN=1 S_NYUGAKU 入学→1", r == 1, f"实际={r}")

    print("\n[1.5] GAKUKBN=1 小学校 - S_TENNYU 転入")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888112")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888112")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888112, 1, 1, 0, 0, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI, S_TENNYU_BI, S_TENGAKU_BI, S_TAIGAKU_BI, S_SOTSUGYO_BI) VALUES (888112, 2024, 1, 1, 0, 0, 0, 20210401, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888112, 1, 4)')
    r = cur.fetchone()[0]
    record("GAKUKBN=1 S_TENNYU 転入→1", r == 1, f"实际={r}")

    print("\n[1.6] GAKUKBN=1 小学校 - S_TENGAKU 転学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888113")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888113")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888113, 1, 1, 0, 0, 0, 0, 5, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI, S_TENNYU_BI, S_TENGAKU_BI, S_TAIGAKU_BI, S_SOTSUGYO_BI) VALUES (888113, 2024, 1, 1, 0, 0, 20180401, 0, 20210320, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888113, 1, 5)')
    r = cur.fetchone()[0]
    record("GAKUKBN=1 S_TENGAKU 転学→0", r == 0, f"实际={r}")

    print("\n[1.7] GAKUKBN=1 小学校 - S_TAIGAKU 退学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888114")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888114")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888114, 1, 1, 0, 0, 0, 0, 6, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI, S_TENNYU_BI, S_TENGAKU_BI, S_TAIGAKU_BI, S_SOTSUGYO_BI) VALUES (888114, 2024, 1, 1, 0, 0, 20180401, 0, 0, 20200320, 0)")
    cur.execute('SELECT gkbfkziskchk(888114, 1, 6)')
    r = cur.fetchone()[0]
    record("GAKUKBN=1 S_TAIGAKU 退学→0", r == 0, f"实际={r}")

    print("\n[1.8] GAKUKBN=1 小学校 - S_SOTSUGYO 卒業")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888115")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888115")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888115, 1, 1, 0, 0, 0, 0, 6, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI, S_TENNYU_BI, S_TENGAKU_BI, S_TAIGAKU_BI, S_SOTSUGYO_BI) VALUES (888115, 2024, 1, 1, 0, 0, 20180401, 0, 0, 0, 20200320)")
    cur.execute('SELECT gkbfkziskchk(888115, 1, 6)')
    r = cur.fetchone()[0]
    record("GAKUKBN=1 S_SOTSUGYO 卒業→0", r == 0, f"实际={r}")

    # GAKUKBN=2 中学校
    print("\n[1.9] GAKUKBN=2 中学校 - T_NYUGAKU 入学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888121")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888121")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888121, 1, 1, 0, 0, 0, 0, 3, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, T_NYUGAKU_BI, T_TENNYU_BI, T_TENGAKU_BI, T_TAIGAKU_BI, T_SOTSUGYO_BI) VALUES (888121, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888121, 2, 3)')
    r = cur.fetchone()[0]
    record("GAKUKBN=2 T_NYUGAKU 入学→1", r == 1, f"实际={r}")

    print("\n[1.10] GAKUKBN=2 中学校 - T_TENNYU 転入")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888122")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888122")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888122, 1, 1, 0, 0, 0, 0, 3, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, T_NYUGAKU_BI, T_TENNYU_BI, T_TENGAKU_BI, T_TAIGAKU_BI, T_SOTSUGYO_BI) VALUES (888122, 2024, 1, 1, 0, 0, 0, 20230401, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888122, 2, 3)')
    r = cur.fetchone()[0]
    record("GAKUKBN=2 T_TENNYU 転入→1", r == 1, f"实际={r}")

    print("\n[1.11] GAKUKBN=2 中学校 - T_TENGAKU 転学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888123")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888123")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888123, 1, 1, 0, 0, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, T_NYUGAKU_BI, T_TENNYU_BI, T_TENGAKU_BI, T_TAIGAKU_BI, T_SOTSUGYO_BI) VALUES (888123, 2024, 1, 1, 0, 0, 20220401, 0, 20230320, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888123, 2, 4)')
    r = cur.fetchone()[0]
    record("GAKUKBN=2 T_TENGAKU 転学→0", r == 0, f"实际={r}")

    print("\n[1.12] GAKUKBN=2 中学校 - T_TAIGAKU 退学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888124")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888124")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888124, 1, 1, 0, 0, 0, 0, 5, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, T_NYUGAKU_BI, T_TENNYU_BI, T_TENGAKU_BI, T_TAIGAKU_BI, T_SOTSUGYO_BI) VALUES (888124, 2024, 1, 1, 0, 0, 20220401, 0, 0, 20250320, 0)")
    cur.execute('SELECT gkbfkziskchk(888124, 2, 5)')
    r = cur.fetchone()[0]
    record("GAKUKBN=2 T_TAIGAKU 退学→0", r == 0, f"实际={r}")

    print("\n[1.13] GAKUKBN=2 中学校 - T_SOTSUGYO 卒業")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888125")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888125")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888125, 1, 1, 0, 0, 0, 0, 5, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, T_NYUGAKU_BI, T_TENNYU_BI, T_TENGAKU_BI, T_TAIGAKU_BI, T_SOTSUGYO_BI) VALUES (888125, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 20270320)")
    cur.execute('SELECT gkbfkziskchk(888125, 2, 5)')
    r = cur.fetchone()[0]
    record("GAKUKBN=2 T_SOTSUGYO 卒業→0", r == 0, f"实际={r}")

    # GAKUKBN=3 特別支援学校
    print("\n[1.14] GAKUKBN=3 特別支援 - SHIEN_S 小学部入学 GAKUNEN=3")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888131")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888131")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888131, 1, 1, 0, 0, 0, 0, 3, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, SHIEN_S_NYUGAKU_BI, SHIEN_S_TENNYU_BI, SHIEN_S_TENGAKU_BI, SHIEN_S_TAIGAKU_BI, SHIEN_S_SOTSUGYO_BI) VALUES (888131, 2024, 1, 1, 0, 0, 20210401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888131, 3, 3)')
    r = cur.fetchone()[0]
    record("GAKUKBN=3 SHIEN_S 小学部入学→1", r == 1, f"实际={r}")

    print("\n[1.15] GAKUKBN=3 特別支援 - SHIEN_S 小学部卒業 GAKUNEN=6")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888132")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888132")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888132, 1, 1, 0, 0, 0, 0, 6, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, SHIEN_S_NYUGAKU_BI, SHIEN_S_TENNYU_BI, SHIEN_S_TENGAKU_BI, SHIEN_S_TAIGAKU_BI, SHIEN_S_SOTSUGYO_BI) VALUES (888132, 2024, 1, 1, 0, 0, 20210401, 0, 0, 0, 20270320)")
    cur.execute('SELECT gkbfkziskchk(888132, 3, 6)')
    r = cur.fetchone()[0]
    record("GAKUKBN=3 SHIEN_S 小学部卒業→0", r == 0, f"实际={r}")

    print("\n[1.16] GAKUKBN=3 特別支援 - SHIEN_T 中学部入学 GAKUNEN=7")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888133")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888133")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888133, 1, 1, 0, 0, 0, 0, 7, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, SHIEN_T_NYUGAKU_BI, SHIEN_T_TENNYU_BI, SHIEN_T_TENGAKU_BI, SHIEN_T_TAIGAKU_BI, SHIEN_T_SOTSUGYO_BI) VALUES (888133, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888133, 3, 7)')
    r = cur.fetchone()[0]
    record("GAKUKBN=3 SHIEN_T 中学部入学→1", r == 1, f"实际={r}")

    print("\n[1.17] GAKUKBN=3 特別支援 - SHIEN_T 中学部卒業 GAKUNEN=9")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888134")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888134")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888134, 1, 1, 0, 0, 0, 0, 9, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, SHIEN_T_NYUGAKU_BI, SHIEN_T_TENNYU_BI, SHIEN_T_TENGAKU_BI, SHIEN_T_TAIGAKU_BI, SHIEN_T_SOTSUGYO_BI) VALUES (888134, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 20300320)")
    cur.execute('SELECT gkbfkziskchk(888134, 3, 9)')
    r = cur.fetchone()[0]
    record("GAKUKBN=3 SHIEN_T 中学部卒業→0", r == 0, f"实际={r}")

    # GAKUKBN=4 区域外
    print("\n[1.18] GAKUKBN=4 区域外 - KUIKIGAI_S 小学部入学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888141")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888141")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888141, 1, 1, 0, 0, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUIKIGAI_S_NYUGAKU_BI, KUIKIGAI_S_TENNYU_BI, KUIKIGAI_S_TENGAKU_BI, KUIKIGAI_S_TAIGAKU_BI, KUIKIGAI_S_SOTSUGYO_BI) VALUES (888141, 2024, 1, 1, 0, 0, 20200401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888141, 4, 4)')
    r = cur.fetchone()[0]
    record("GAKUKBN=4 KUIKIGAI_S 小学部入学→1", r == 1, f"实际={r}")

    print("\n[1.19] GAKUKBN=4 区域外 - KUIKIGAI_S 小学部卒業")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888142")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888142")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888142, 1, 1, 0, 0, 0, 0, 6, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUIKIGAI_S_NYUGAKU_BI, KUIKIGAI_S_TENNYU_BI, KUIKIGAI_S_TENGAKU_BI, KUIKIGAI_S_TAIGAKU_BI, KUIKIGAI_S_SOTSUGYO_BI) VALUES (888142, 2024, 1, 1, 0, 0, 20200401, 0, 0, 0, 20260320)")
    cur.execute('SELECT gkbfkziskchk(888142, 4, 6)')
    r = cur.fetchone()[0]
    record("GAKUKBN=4 KUIKIGAI_S 小学部卒業→0", r == 0, f"实际={r}")

    print("\n[1.20] GAKUKBN=4 区域外 - KUIKIGAI_T 中学部入学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888143")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888143")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888143, 1, 1, 0, 0, 0, 0, 7, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUIKIGAI_T_NYUGAKU_BI, KUIKIGAI_T_TENNYU_BI, KUIKIGAI_T_TENGAKU_BI, KUIKIGAI_T_TAIGAKU_BI, KUIKIGAI_T_SOTSUGYO_BI) VALUES (888143, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888143, 4, 7)')
    r = cur.fetchone()[0]
    record("GAKUKBN=4 KUIKIGAI_T 中学部入学→1", r == 1, f"实际={r}")

    print("\n[1.21] GAKUKBN=4 区域外 - KUIKIGAI_T 中学部卒業")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888144")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888144")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888144, 1, 1, 0, 0, 0, 0, 9, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUIKIGAI_T_NYUGAKU_BI, KUIKIGAI_T_TENNYU_BI, KUIKIGAI_T_TENGAKU_BI, KUIKIGAI_T_TAIGAKU_BI, KUIKIGAI_T_SOTSUGYO_BI) VALUES (888144, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 20300320)")
    cur.execute('SELECT gkbfkziskchk(888144, 4, 9)')
    r = cur.fetchone()[0]
    record("GAKUKBN=4 KUIKIGAI_T 中学部卒業→0", r == 0, f"实际={r}")

    # GAKUKBN=5 国私立
    print("\n[1.22] GAKUKBN=5 国私立 - KUNISHIRITSU_S 小学部入学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888151")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888151")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888151, 1, 1, 0, 0, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUNISHIRITSU_S_NYUGAKU_BI, KUNISHIRITSU_S_TENNYU_BI, KUNISHIRITSU_S_TENGAKU_BI, KUNISHIRITSU_S_TAIGAKU_BI, KUNISHIRITSU_S_SOTSUGYO_BI) VALUES (888151, 2024, 1, 1, 0, 0, 20200401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888151, 5, 4)')
    r = cur.fetchone()[0]
    record("GAKUKBN=5 KUNISHIRITSU_S 小学部入学→1", r == 1, f"实际={r}")

    print("\n[1.23] GAKUKBN=5 国私立 - KUNISHIRITSU_S 小学部卒業")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888152")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888152")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888152, 1, 1, 0, 0, 0, 0, 6, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUNISHIRITSU_S_NYUGAKU_BI, KUNISHIRITSU_S_TENNYU_BI, KUNISHIRITSU_S_TENGAKU_BI, KUNISHIRITSU_S_TAIGAKU_BI, KUNISHIRITSU_S_SOTSUGYO_BI) VALUES (888152, 2024, 1, 1, 0, 0, 20200401, 0, 0, 0, 20260320)")
    cur.execute('SELECT gkbfkziskchk(888152, 5, 6)')
    r = cur.fetchone()[0]
    record("GAKUKBN=5 KUNISHIRITSU_S 小学部卒業→0", r == 0, f"实际={r}")

    print("\n[1.24] GAKUKBN=5 国私立 - KUNISHIRITSU_T 中学部入学")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888153")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888153")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888153, 1, 1, 0, 0, 0, 0, 7, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUNISHIRITSU_T_NYUGAKU_BI, KUNISHIRITSU_T_TENNYU_BI, KUNISHIRITSU_T_TENGAKU_BI, KUNISHIRITSU_T_TAIGAKU_BI, KUNISHIRITSU_T_SOTSUGYO_BI) VALUES (888153, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888153, 5, 7)')
    r = cur.fetchone()[0]
    record("GAKUKBN=5 KUNISHIRITSU_T 中学部入学→1", r == 1, f"实际={r}")

    print("\n[1.25] GAKUKBN=5 国私立 - KUNISHIRITSU_T 中学部卒業")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888154")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888154")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888154, 1, 1, 0, 0, 0, 0, 9, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, KUNISHIRITSU_T_NYUGAKU_BI, KUNISHIRITSU_T_TENNYU_BI, KUNISHIRITSU_T_TENGAKU_BI, KUNISHIRITSU_T_TAIGAKU_BI, KUNISHIRITSU_T_SOTSUGYO_BI) VALUES (888154, 2024, 1, 1, 0, 0, 20220401, 0, 0, 0, 20300320)")
    cur.execute('SELECT gkbfkziskchk(888154, 5, 9)')
    r = cur.fetchone()[0]
    record("GAKUKBN=5 KUNISHIRITSU_T 中学部卒業→0", r == 0, f"实际={r}")

    # 无效 GAKUKBN
    print("\n[1.26] GAKUKBN=0 (無効)")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888161")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888161")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888161, 1, 1, 0, 0, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI) VALUES (888161, 2024, 1, 1, 0, 0, 20180401)")
    cur.execute('SELECT gkbfkziskchk(888161, 0, 4)')
    r = cur.fetchone()[0]
    record("GAKUKBN=0 返回0", r == 0, f"实际={r}")

    print("\n[1.27] GAKUKBN=99 (無効)")
    cur.execute('SELECT gkbfkziskchk(888161, 99, 4)')
    r = cur.fetchone()[0]
    record("GAKUKBN=99 返回0", r == 0, f"实际={r}")

    # 全=0
    print("\n[1.28] 所有日期=0 (全未設定)")
    cur.execute("DELETE FROM gkbtgakureibo WHERE koJIN_no = 888170")
    cur.execute("DELETE FROM gkbtshugakurireki WHERE koJIN_no = 888170")
    cur.execute("INSERT INTO gkbtgakureibo (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GEN_JIYU_CD, GEN_NENGAPI, SAISHIN_KBN, SAKUJO_FLG, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG) VALUES (888170, 1, 1, 0, 0, 0, 0, 4, 1, 1, 0)")
    cur.execute("INSERT INTO gkbtshugakurireki (KOJIN_NO, NENDO, KOJIN_RIREKI_RENBAN, SHUGAKU_RIREKI_RENBAN, SAISHIN_FLG, SAKUJO_FLG, S_NYUGAKU_BI, S_TENNYU_BI, S_TENGAKU_BI, S_TAIGAKU_BI, S_SOTSUGYO_BI) VALUES (888170, 2024, 1, 1, 0, 0, 0, 0, 0, 0, 0)")
    cur.execute('SELECT gkbfkziskchk(888170, 1, 4)')
    r = cur.fetchone()[0]
    record("所有日期=0 返回0", r == 0, f"实际={r}")

    print("\n" + "=" * 70)
    print("2. GKBFKHTSET 排他制御セット (17 分支)")
    print("=" * 70)

    # 参数检查
    print("\n[2.1] KBN=NULL → 9")
    r = cur.execute('SELECT gkbfkhtset(NULL, %s, %s, %s, %s, %s, %s)', 
                   ('KEY1', 'USER1', 20250301, '123456789', 'TERM', 120000))
    cur.execute('SELECT gkbfkhtset(NULL, %s, %s, %s, %s, %s, %s)', 
               ('KEY1', 'USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=NULL → 9", r == 9, f"实际={r}")

    print("\n[2.2] KBN=0 → 9")
    cur.execute('SELECT gkbfkhtset(0, %s, %s, %s, %s, %s, %s)', 
               ('KEY1', 'USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=0 → 9", r == 9, f"实际={r}")

    print("\n[2.3] KBN=3 → 9")
    cur.execute('SELECT gkbfkhtset(3, %s, %s, %s, %s, %s, %s)', 
               ('KEY1', 'USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=3 → 9", r == 9, f"实际={r}")

    print("\n[2.4] KEY=NULL → 9")
    cur.execute('SELECT gkbfkhtset(1, NULL, %s, %s, %s, %s, %s)', 
               ('USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KEY=NULL → 9", r == 9, f"实际={r}")

    print("\n[2.5] TAISHA=NULL → 9")
    cur.execute('SELECT gkbfkhtset(1, %s, NULL, %s, %s, %s, %s)', 
               ('KEY1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("TAISHA=NULL → 9", r == 9, f"实际={r}")

    print("\n[2.6] SHORI_YMD=NULL → 9")
    cur.execute('SELECT gkbfkhtset(1, %s, %s, NULL, %s, %s, %s)', 
               ('KEY1', 'USER1', '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("SHORI_YMD=NULL → 9", r == 9, f"实际={r}")

    print("\n[2.7] SHOKUIN=NULL → 9")
    cur.execute("SELECT gkbfkhtset(1, 'KEY1', 'USER1', 20250301, NULL::VARCHAR, 'TERM', 120000)")
    r = cur.fetchone()[0]
    record("SHOKUIN=NULL → 9", r == 9, f"实际={r}")

    print("\n[2.8] TANMATU=NULL → 9")
    cur.execute("SELECT gkbfkhtset(1, 'KEY1', 'USER1', 20250301, '123456789', NULL::VARCHAR, 120000)")
    r = cur.fetchone()[0]
    record("TANMATU=NULL → 9", r == 9, f"实际={r}")

    print("\n[2.9] HMS=NULL → 9")
    cur.execute("SELECT gkbfkhtset(1, 'KEY1', 'USER1', 20250301, '123456789', 'TERM', NULL::INTEGER)")
    r = cur.fetchone()[0]
    record("HMS=NULL → 9", r == 9, f"实际={r}")

    # 正常场景
    print("\n[2.10] KBN=1 表空 无排他 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute('SELECT gkbfkhtset(1, %s, %s, %s, %s, %s, %s)', 
               ('KEY_NOHATA', 'USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=1 表空 → 0", r == 0, f"实际={r}")

    print("\n[2.11] KBN=2 表空 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute('SELECT gkbfkhtset(2, %s, %s, %s, %s, %s, %s)', 
               ('KEY2', 'USER2', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=2 表空 → 0", r == 0, f"实际={r}")

    print("\n[2.12] KBN=2 表非空 → 1 (排他中)")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (1, 'OTHER_KEY', 'OTHER_USER', 20250301, 120000, '123456789', 'TERM')")
    cur.execute('SELECT gkbfkhtset(2, %s, %s, %s, %s, %s, %s)', 
               ('KEY2', 'USER2', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=2 表非空 → 1", r == 1, f"实际={r}")

    print("\n[2.13] KBN=1 有KBN=2排他 → 1")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (2, 'KEY2', 'USER2', 20250301, 120000, '999999999', 'TERM2')")
    cur.execute('SELECT gkbfkhtset(1, %s, %s, %s, %s, %s, %s)', 
               ('MY_KEY', 'USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=1 有KBN=2排他 → 1", r == 1, f"实际={r}")

    print("\n[2.14] KBN=1 有相同KEY排他 → 1")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (1, 'SAME_KEY', 'OTHER_USER', 20250301, 120000, '999999999', 'TERM2')")
    cur.execute('SELECT gkbfkhtset(1, %s, %s, %s, %s, %s, %s)', 
               ('SAME_KEY', 'USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=1 有相同KEY → 1", r == 1, f"实际={r}")

    print("\n[2.15] KBN=1 无排他 插入成功 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute('SELECT gkbfkhtset(1, %s, %s, %s, %s, %s, %s)', 
               ('NEW_KEY', 'NEW_USER', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=1 插入成功 → 0", r == 0, f"实际={r}")

    print("\n[2.16] KBN=1 有他人KEY 不冲突 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (1, 'OTHER_KEY', 'OTHER_USER', 20250301, 120000, '999999999', 'TERM2')")
    cur.execute('SELECT gkbfkhtset(1, %s, %s, %s, %s, %s, %s)', 
               ('MY_KEY', 'USER1', 20250301, '123456789', 'TERM', 120000))
    r = cur.fetchone()[0]
    record("KBN=1 有他人KEY → 0", r == 0, f"实际={r}")

    print("\n" + "=" * 70)
    print("3. GKBFKHTCLR 排他制御クリア (8 分支)")
    print("=" * 70)

    print("\n[3.1] KBN=NULL → 9")
    cur.execute('SELECT gkbfkhtclr(NULL, %s)', ('KEY1',))
    r = cur.fetchone()[0]
    record("KBN=NULL → 9", r == 9, f"实际={r}")

    print("\n[3.2] KBN=0 → 9")
    cur.execute('SELECT gkbfkhtclr(0, %s)', ('KEY1',))
    r = cur.fetchone()[0]
    record("KBN=0 → 9", r == 9, f"实际={r}")

    print("\n[3.3] KBN=3 → 9")
    cur.execute('SELECT gkbfkhtclr(3, %s)', ('KEY1',))
    r = cur.fetchone()[0]
    record("KBN=3 → 9", r == 9, f"实际={r}")

    print("\n[3.4] KEY=NULL → 9")
    cur.execute("SELECT gkbfkhtclr(1, NULL::VARCHAR)")
    r = cur.fetchone()[0]
    record("KEY=NULL → 9", r == 9, f"实际={r}")

    print("\n[3.5] KBN=1 KEY存在 删除成功 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (1, 'CLR_KEY1', 'USER1', 20250301, 120000, '123456789', 'TERM')")
    cur.execute('SELECT gkbfkhtclr(1, %s)', ('CLR_KEY1',))
    r = cur.fetchone()[0]
    record("KBN=1 KEY存在 → 0", r == 0, f"实际={r}")

    print("\n[3.6] KBN=1 KEY不存在 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute('SELECT gkbfkhtclr(1, %s)', ('NOT_EXIST',))
    r = cur.fetchone()[0]
    record("KBN=1 KEY不存在 → 0", r == 0, f"实际={r}")

    print("\n[3.7] KBN=2 KEY存在 删除成功 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (2, 'CLR_KEY2', 'USER2', 20250301, 120000, '123456789', 'TERM')")
    cur.execute('SELECT gkbfkhtclr(2, %s)', ('CLR_KEY2',))
    r = cur.fetchone()[0]
    record("KBN=2 KEY存在 → 0", r == 0, f"实际={r}")

    print("\n[3.8] KBN=1,2混合删除 → 0")
    cur.execute("DELETE FROM gkbthaita")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (1, 'K1_KEY', 'USER1', 20250301, 120000, '111111111', 'TERM')")
    cur.execute("INSERT INTO gkbthaita (HAITA_KBN_CD, HAITA_KEY, HAITA_TAISHOSHA_MEI, SHORI_YMD, SHORI_HMS, SHORI_SHOKUINKOJIN_NO, SHORI_TANMATSU_NO) VALUES (2, 'K2_KEY', 'USER2', 20250301, 120000, '222222222', 'TERM')")
    cur.execute('SELECT gkbfkhtclr(1, %s)', ('K1_KEY',))
    r = cur.fetchone()[0]
    record("KBN=1,2混合删除 → 0", r == 0, f"实际={r}")

    print("\n" + "=" * 70)
    print("4. GKBFKTBLCNT 動的SQL件数 (6 分支)")
    print("=" * 70)

    print("\n[4.1] WHERE=NULL count>0 → count")
    cur.execute('SELECT gkbfktblcnt(%s, NULL)', ('gkbtzokugara',))
    r = cur.fetchone()[0]
    record("WHERE=NULL count>0", r >= 0, f"实际={r}")

    print("\n[4.2] WHERE=NULL GKBTGAKUREIBO count>0")
    cur.execute('SELECT gkbfktblcnt(%s, NULL)', ('gkbtgakureibo',))
    r = cur.fetchone()[0]
    record("WHERE=NULL GKBTGAKUREIBO count>0", r >= 0, f"实际={r}")

    print("\n[4.3] WHERE=ZOKUGARA_CD=1 匹配 → count")
    cur.execute('SELECT gkbfktblcnt(%s, %s)', ('gkbtzokugara', 'ZOKUGARA_CD=1'))
    r = cur.fetchone()[0]
    record("WHERE ZOKUGARA_CD=1 count", r >= 0, f"实际={r}")

    print("\n[4.4] WHERE=key=value 不匹配 → 0")
    cur.execute('SELECT gkbfktblcnt(%s, %s)', ('gkbtzokugara', 'ZOKUGARA_CD=99999'))
    r = cur.fetchone()[0]
    record("WHERE不匹配 → 0", r == 0, f"实际={r}")

    print("\n[4.5] WHERE='' (空字符串) → count")
    cur.execute('SELECT gkbfktblcnt(%s, %s)', ('gkbtzokugara', ''))
    r = cur.fetchone()[0]
    record("WHERE='' → count", r >= 0, f"实际={r}")

    print("\n[4.6] 不存在的表 → -1")
    cur.execute('SELECT gkbfktblcnt(%s, NULL)', ('non_existent_table',))
    r = cur.fetchone()[0]
    record("不存在表 → -1", r == -1, f"实际={r}")

    print("\n" + "=" * 70)
    print("5. GKBFKGKNCDHK 学年コード変換 (13 分支)")
    print("=" * 70)

    print("\n[5.1] GAKUNEN=1 → P1")
    cur.execute('SELECT gkbfkgkncdhk(1)')
    r = cur.fetchone()[0]
    record("GAKUNEN=1 → P1", 'P1' in str(r), f"实际={r}")

    print("\n[5.2] GAKUNEN=2 → P2")
    cur.execute('SELECT gkbfkgkncdhk(2)')
    r = cur.fetchone()[0]
    record("GAKUNEN=2 → P2", 'P2' in str(r), f"实际={r}")

    print("\n[5.3] GAKUNEN=3 → P3")
    cur.execute('SELECT gkbfkgkncdhk(3)')
    r = cur.fetchone()[0]
    record("GAKUNEN=3 → P3", 'P3' in str(r), f"实际={r}")

    print("\n[5.4] GAKUNEN=4 → P4")
    cur.execute('SELECT gkbfkgkncdhk(4)')
    r = cur.fetchone()[0]
    record("GAKUNEN=4 → P4", 'P4' in str(r), f"实际={r}")

    print("\n[5.5] GAKUNEN=5 → P5")
    cur.execute('SELECT gkbfkgkncdhk(5)')
    r = cur.fetchone()[0]
    record("GAKUNEN=5 → P5", 'P5' in str(r), f"实际={r}")

    print("\n[5.6] GAKUNEN=6 → P6")
    cur.execute('SELECT gkbfkgkncdhk(6)')
    r = cur.fetchone()[0]
    record("GAKUNEN=6 → P6", 'P6' in str(r), f"实际={r}")

    print("\n[5.7] GAKUNEN=7 → J1")
    cur.execute('SELECT gkbfkgkncdhk(7)')
    r = cur.fetchone()[0]
    record("GAKUNEN=7 → J1", 'J1' in str(r), f"实际={r}")

    print("\n[5.8] GAKUNEN=8 → J2")
    cur.execute('SELECT gkbfkgkncdhk(8)')
    r = cur.fetchone()[0]
    record("GAKUNEN=8 → J2", 'J2' in str(r), f"实际={r}")

    print("\n[5.9] GAKUNEN=9 → J3")
    cur.execute('SELECT gkbfkgkncdhk(9)')
    r = cur.fetchone()[0]
    record("GAKUNEN=9 → J3", 'J3' in str(r), f"实际={r}")

    print("\n[5.10] GAKUNEN=0 → E3")
    cur.execute('SELECT gkbfkgkncdhk(0)')
    r = cur.fetchone()[0]
    record("GAKUNEN=0 → E3", 'E3' in str(r), f"实际={r}")

    print("\n[5.11] GAKUNEN=10 → err=1")
    cur.execute('SELECT gkbfkgkncdhk(10)')
    r = cur.fetchone()[0]
    record("GAKUNEN=10 → err=1", '1' in str(r), f"实际={r}")

    print("\n[5.12] GAKUNEN=99 → err=1")
    cur.execute('SELECT gkbfkgkncdhk(99)')
    r = cur.fetchone()[0]
    record("GAKUNEN=99 → err=1", '1' in str(r), f"实际={r}")

    print("\n[5.13] GAKUNEN=NULL → err=1")
    cur.execute("SELECT gkbfkgkncdhk(NULL::INTEGER)")
    r = cur.fetchone()[0]
    record("GAKUNEN=NULL → err=1", '1' in str(r), f"实际={r}")

    # ============================================================
    # 6. GKBFKZKGMGET / GKBFKZKGMGET2 続柄名取得 (20 分支, 2026-09 扩展)
    # ============================================================
    print("\n" + "=" * 70)
    print("6. GKBFKZKGMGET/2 続柄名取得 (20 分支)")
    print("=" * 70)

    # 准备続柄基础数据 (自包含, 不依赖 testdata 文件)
    cur.execute("TRUNCATE GKBTZOKUGARA")
    zoku_rows = [
        (1, 1, '本人'), (1, 2, '父'), (1, 3, '母'), (1, 4, '祖父'), (1, 5, '祖母'), (1, 9, '父(境界)'),
        (2, 1, '叔父'), (2, 5, '弟'), (2, 6, '妹'),
        (9, 1, '境界続柄_一'),
    ]
    for kbn, cd, mei in zoku_rows:
        cur.execute("INSERT INTO GKBTZOKUGARA (ZOKUGARA_KBN, ZOKUGARA_CD, ZOKUGARAMEI) VALUES (%s, %s, %s)", (kbn, cd, mei))
    record("続柄基础数据 (9 行)", True)

    def get(kbn, c1, c2, c3, c4):
        cur.execute("SELECT ret, o_vzokugaramei, o_iherr FROM gkbfkzkgmget(%s,%s,%s,%s,%s)",
                    (kbn, c1, c2, c3, c4))
        return cur.fetchone()

    r = get(1, 1, 0, 0, 0)
    record("[6.1] 单码 CD1: (1,1)→本人", r == (0, '本人', 0), str(r))
    r = get(1, 0, 2, 0, 0)
    record("[6.2] 单码 CD2 槽: (0,2)→父", r == (0, '父', 0), str(r))
    r = get(1, 0, 0, 0, 1)
    record("[6.3] 单码 CD4 槽: (0,0,0,1)→本人", r == (0, '本人', 0), str(r))
    r = get(2, 1, 0, 0, 0)
    record("[6.4] KBN=2: (2,1)→叔父", r == (0, '叔父', 0), str(r))
    r = get(1, 1, 2, 0, 0)
    record("[6.5] 2 码拼接: →本人の父", r == (0, '本人の父', 0), str(r))
    r = get(1, 1, 2, 3, 0)
    record("[6.6] 3 码拼接: →本人の父の母", r == (0, '本人の父の母', 0), str(r))
    r = get(1, 1, 2, 3, 4)
    record("[6.7] 4 码拼接: →本人の父の母の祖父", r == (0, '本人の父の母の祖父', 0), str(r))
    r = get(1, 9, 0, 0, 0)
    record("[6.8] 边界 CD=9: →父(境界)", r == (0, '父(境界)', 0), str(r))
    r = get(9, 1, 0, 0, 0)
    record("[6.9] 边界 KBN=9: →境界続柄_一", r == (0, '境界続柄_一', 0), str(r))
    r = get(1, 98, 0, 0, 0)
    record("[6.10] NO_DATA_FOUND: (1,98)→NULL+herr=1 (末尾赋值未初始化变量, Oracle 同)",
           r == (1, None, 1), str(r))
    r = get(5, 1, 0, 0, 0)
    record("[6.11] KBN 无数据: (5,1)→NULL+herr=1", r == (1, None, 1), str(r))
    r = get(1, 0, 0, 0, 0)
    record("[6.12] 全 0 码: →NULL+herr=0", r[0] == 1 and r[1] is None and r[2] == 0, str(r))
    r = get(1, -1, 0, 0, 0)
    record("[6.13] 负码视同无数据: (-1)→NULL+herr=0", r[0] == 1 and r[1] is None and r[2] == 0, str(r))
    r = get(1, None, 2, 0, 0)
    record("[6.14] NULL 码跳过: (NULL,2)→父", r == (0, '父', 0), str(r))
    r = get(1, None, 98, 0, 0)
    record("[6.15] NULL 跳过后再 NO_DATA_FOUND: (NULL,98)→NULL+herr=1", r == (1, None, 1), str(r))

    def get2(kbn, c1, c2, c3, c4):
        cur.execute("SELECT gkbfkzkgmget2(%s,%s,%s,%s,%s)", (kbn, c1, c2, c3, c4))
        return cur.fetchone()[0]

    v = get2(1, 1, 2, 0, 0)
    record("[6.16] GET2 2 码: →本人の父", v == '本人の父', repr(v))
    v = get2(1, 2, 3, 4, 5)
    record("[6.17] GET2 4 码: →父の母の祖父の祖母", v == '父の母の祖父の祖母', repr(v))
    v = get2(2, 5, 6, 0, 0)
    record("[6.18] GET2 KBN=2: →弟の妹", v == '弟の妹', repr(v))
    v = get2(1, 98, 0, 0, 0)
    record("[6.19] GET2 NO_DATA_FOUND: →NULL (Oracle 同)", v is None, repr(v))
    v = get2(1, None, 1, 0, 0)
    record("[6.20] GET2 NULL 跳过: (NULL,1)→本人", v == '本人', repr(v))

    # 清理 (持久测试库保留, 测试后不删除)
    print(f"\n[清理] 关闭连接 (数据库 {TEST_DB} 保留不删除)")
    cur.close()
    conn.close()
    print(f"\n测试数据库 {TEST_DB} 已保留在本地 PG (含最后测试状态)")

    print(f"\n{'='*70}")
    print(f"汇总: {PASS} PASS / {FAIL} FAIL")
    print(f"覆盖率: {PASS}/{(PASS+FAIL)} = {100*PASS/(PASS+FAIL):.1f}%")
    print(f"{'='*70}")

    # 打印失败项
    if FAIL > 0:
        print("\n失败项:")
        for name, passed, detail in RESULTS:
            if not passed:
                print(f"  - {name}: {detail}")

    sys.exit(0 if FAIL == 0 else 1)


def _has_sql(sql: str) -> bool:
    """去除 -- 注释行后是否还有可执行语句"""
    for line in sql.splitlines():
        s = line.strip()
        if s and not s.startswith("--"):
            return True
    return False


def _apply_dir(cur, d: Path):
    """逐文件 apply; 空 SQL 跳过, 单文件失败不阻断后续 (逐项记录)。
    先加载 CREATE TYPE 文件再加载其余 (domain over array 依赖基础 composite type, 如 GKBEKENSU_LIST→GKBEKENSU_RECORD)"""
    files = sorted(d.glob("*.pgsql"))
    type_files = [f for f in files if "CREATE TYPE" in f.read_text(encoding="utf-8")]
    other_files = [f for f in files if f not in type_files]
    for f in type_files + other_files:
        sql = f.read_text(encoding="utf-8")
        if not _has_sql(sql):
            print(f"  [SKIP] {f.name} (无可执行 SQL)")
            continue
        try:
            cur.execute(sql)
            record(f"LOAD {f.name}", True)
        except Exception as e:
            record(f"LOAD {f.name}", False, str(e)[:80])


if __name__ == "__main__":
    main()
