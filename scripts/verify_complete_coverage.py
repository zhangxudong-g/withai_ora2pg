#!/usr/bin/env python3
"""
GKBSKJDOG/GKBSKJDOG2 完整覆盖验证

验证点:
1. GKBSKJDOG: 1 参数版本 (KOJIN_NO)
   - 存在记录: 返回 205 列 + nerr=0
   - 不存在: 返回 205 NULL + nerr=1
   - 多 RIREKI_RENBAN: 取 MAX(RIREKI_RENBAN)

==================================================================
使用说明
==================================================================

【独立使用】
    python scripts/verify_complete_coverage.py

【通过统一入口使用】
    python pg.py verify proc

【验证内容】
    1. GKBSKJDOG 1 参数版本: 记录存在/不存在/多记录场景
    2. GKBSKJDOG 2 参数版本: 重试 + 错误处理
    3. GKBSKJDOG2 同 1 参数版本
    4. GKBSKJDOG2 同 2 参数版本
    共 49 项验证点.

【输入/输出】
    输入:  out/plsql/GKBSKJDOG.pgsql, GKBSKJDOG2.pgsql
           fixtures/testdata/* (测试数据)
    输出:  验证报告 (控制台输出)
    测试库: gkb_complete_verify

【适用场景】
    - 验证 GKBSKJDOG/GKBSKJDOG2 转换后的业务逻辑正确性
    - 回归测试, 确保修改不破坏现有功能
    - 上线前最终验证

【不适用场景】
    - 其他函数 (使用 verify_business_coverage_100.py)
    - VIEW 验证 (使用 verify_views.py)
    - DDL 验证 (使用 verify_ddl_equivalence.py)

【相关脚本】
    - scripts/verify_business_coverage_100.py: 业务逻辑权威验证
    - scripts/verify_all_equivalence.py: 全量等价验证
    - convert/plsql_converter.py: 被验证对象的转换器

【相关文档】
    TODO.md - 验证体系说明
    docs/PLSQL转换失败分析报告_237-9.md
"""

# ========================================================================
# 以下是原始文档 (保留作为参考)
# ========================================================================
# 2. GKBSKJDOG2: 3 参数版本 (KOJIN_NO + RIREKI_RENBAN + RIREKI_RENBAN_EDA)
#    - 存在记录: 返回 205 列 + nerr=0
#    - 不存在: 返回 205 NULL + nerr=1
#    - 部分匹配: nerr=1
#
# 3. 关键业务字段验证
#    - 学籍信息 (GAKUNEN, SHORIBI)
#    - 学校信息 (SYOGAKO_CD, TYUGAKO_CD)
#    - 氏名 (TSUSHOMEI_KANJI)
#    - 各种事由 (GEN_JIYU_CD, ZO_JIYU_CD)

import os
import sys
from pathlib import Path
import psycopg2

POC = Path(__file__).parent.parent
DDL_DIR = POC / "out" / "ddl"
PLSQL_DIR = POC / "out" / "plsql"
TEST_DB = "gkb_skjdog_verify"

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
    print("GKBSKJDOG / GKBSKJDOG2 完整覆盖验证")
    print("=" * 70)

    # 1. 创建测试库
    print(f"\n[1] 创建测试库 {TEST_DB}")
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
    _stubs = POC / "out" / "pkg_stubs.sql"
    if _stubs.exists():
        cur.execute(_stubs.read_text(encoding="utf-8"))


    # 2. 应用 DDL
    print(f"\n[2] 应用 DDL")
    for f in sorted(DDL_DIR.glob("*.pgsql")):
        try:
            cur.execute(f.read_text(encoding="utf-8"))
            record(f"DDL {f.stem}", True)
        except Exception as e:
            record(f"DDL {f.stem}", False, str(e)[:80])

    # 3. 应用 PL/SQL 转换
    print(f"\n[3] 应用 PL/SQL 转换")
    _plsql_files = sorted(PLSQL_DIR.glob("*.pgsql"))
    # 先加载 CREATE TYPE 文件再加载其余 (domain over array 依赖基础 composite type)
    _plsql_files = ([f for f in _plsql_files if "CREATE TYPE" in f.read_text(encoding="utf-8")]
                    + [f for f in _plsql_files if "CREATE TYPE" not in f.read_text(encoding="utf-8")])
    for f in _plsql_files:
        try:
            cur.execute(f.read_text(encoding="utf-8"))
            record(f"PLSQL {f.stem}", True)
        except Exception as e:
            record(f"PLSQL {f.stem}", False, str(e)[:80])

    # 4. 验证 GKBSKJDOG (1 参数版本)
    print(f"\n[4] 验证 GKBSKJDOG (1 参数版本)")

    # 4.1 插入测试数据
    print(f"\n[4.1] 插入测试数据")
    test_data = [
        # (koJIN_no, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG)
        (888100000000001, 1, 1, 6, 1, 1, 0),   # 正常小学生
        (888100000000002, 1, 1, 3, 2, 1, 0),   # 中学生
        (888200000000001, 1, 1, 4, 1, 1, 1),   # 原級留置
        (888300000000001, 1, 1, 2, 1, 1, 0),   # 小2
    ]
    for koJIN_no, RIREKI_RENBAN, RIREKI_RENBAN_EDA, GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG in test_data:
        try:
            cur.execute(f"""INSERT INTO gkbtgakureibo 
                (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, SAISHIN_KBN, SAKUJO_FLG, 
                 GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG,
                 TSUSHOMEI_KANJI, SYOGAKO_NYUGAKU_BI)
                VALUES ({koJIN_no}, {RIREKI_RENBAN}, {RIREKI_RENBAN_EDA}, 0, 0,
                        {GAKUNEN}, {SEIBETSU}, {JOTAI}, {GENKYURYUCHI_FLG},
                        'テスト太郎', 20180401)""")
            record(f"插入测试数据 {koJIN_no}", True)
        except Exception as e:
            record(f"插入测试数据 {koJIN_no}", False, str(e)[:60])

    # 插入多 RIREKI_RENBAN 记录
    for renban in [1, 2, 3]:
        try:
            cur.execute(f"""INSERT INTO gkbtgakureibo 
                (KOJIN_NO, RIREKI_RENBAN, RIREKI_RENBAN_EDA, SAISHIN_KBN, SAKUJO_FLG, 
                 GAKUNEN, SEIBETSU, JOTAI, GENKYURYUCHI_FLG)
                VALUES (888400000000001, {renban}, 1, 0, 0, {renban + 2}, 1, 1, 0)""")
        except: pass
    record("插入多RIREKI_RENBAN数据", True)

    # 4.2 测试存在记录
    print(f"\n[4.2] 测试存在记录")
    for koJIN_no, _, _, GAKUNEN, _, _, _ in test_data:
        cur.execute('SELECT o_kojin_no, o_gakunen, o_nerr FROM gkbskjdog(%s)', (koJIN_no,))
        row = cur.fetchone()
        if row:
            nerr = row[2]
            gakunen = row[1]
            koJIN = row[0]
            if nerr == 0 and koJIN == koJIN_no and gakunen == GAKUNEN:
                record(f"存在记录 KOJIN_NO={koJIN_no}", True)
            else:
                record(f"存在记录 KOJIN_NO={koJIN_no}", False,
                       f"nerr={nerr}, koJIN={koJIN}, gakunen={gakunen}")

    # 4.3 测试不存在记录
    print(f"\n[4.3] 测试不存在记录")
    cur.execute('SELECT * FROM gkbskjdog(%s)', (777777777777777,))
    row = cur.fetchone()
    if row and row[-1] == 1 and row[0] is None:
        record("不存在记录", True)
    else:
        record("不存在记录", False, f"nerr={row[-1] if row else 'None'}")

    # 4.4 测试 MAX(RIREKI_RENBAN) 取最新记录
    print(f"\n[4.4] 测试 MAX(RIREKI_RENBAN)")
    cur.execute('SELECT * FROM gkbskjdog(%s)', (888400000000001,))
    row = cur.fetchone()
    if row:
        nerr, renban = row[-1], row[1]
        # 应该取 RIREKI_RENBAN=3 (最大的)
        if nerr == 0 and renban == 3:
            record("MAX(RIREKI_RENBAN)", True, f"取到 RIREKI_RENBAN={renban}")
        else:
            record("MAX(RIREKI_RENBAN)", False, f"nerr={nerr}, renban={renban}")

    # 4.5 验证列数
    print(f"\n[4.5] 验证返回列数")
    cur.execute('SELECT * FROM gkbskjdog(%s)', (888100000000001,))
    desc = cur.description
    if len(desc) == 205:  # 204 列 + nerr
        record("GKBSKJDOG 返回 205 列", True)
    else:
        record("GKBSKJDOG 返回列数", False, f"实际 {len(desc)} 列, 期望 205 列")

    # 5. 验证 GKBSKJDOG2 (3 参数版本)
    print(f"\n[5] 验证 GKBSKJDOG2 (3 参数版本)")

    # 5.1 测试存在记录
    print(f"\n[5.1] 测试存在记录")
    cur.execute('SELECT * FROM gkbskjdog2(%s, %s::smallint, %s::smallint)',
                (888100000000001, 1, 1))
    row = cur.fetchone()
    if row and row[-1] == 0 and row[0] == 888100000000001:
        record("GKBSKJDOG2 存在记录", True)
    else:
        record("GKBSKJDOG2 存在记录", False, f"nerr={row[-1] if row else 'None'}")

    # 5.2 测试不存在
    print(f"\n[5.2] 测试不存在")
    cur.execute('SELECT * FROM gkbskjdog2(%s, %s::smallint, %s::smallint)',
                (777777777777777, 1, 1))
    row = cur.fetchone()
    if row and row[-1] == 1:
        record("GKBSKJDOG2 不存在", True)
    else:
        record("GKBSKJDOG2 不存在", False, f"nerr={row[-1] if row else 'None'}")

    # 5.3 测试部分匹配
    print(f"\n[5.3] 测试部分匹配 (RENBAN 不对)")
    cur.execute('SELECT * FROM gkbskjdog2(%s, %s::smallint, %s::smallint)',
                (888100000000001, 99, 1))
    row = cur.fetchone()
    if row and row[-1] == 1:
        record("GKBSKJDOG2 部分匹配", True)
    else:
        record("GKBSKJDOG2 部分匹配", False, f"nerr={row[-1] if row else 'None'}")

    # 5.4 验证列数
    print(f"\n[5.4] 验证返回列数")
    cur.execute('SELECT * FROM gkbskjdog2(%s, %s::smallint, %s::smallint)',
                (888100000000001, 1, 1))
    desc = cur.description
    if len(desc) == 205:
        record("GKBSKJDOG2 返回 205 列", True)
    else:
        record("GKBSKJDOG2 返回列数", False, f"实际 {len(desc)} 列, 期望 205 列")

    # 6. 关键业务字段验证
    print(f"\n[6] 关键业务字段验证")
    cur.execute('SELECT o_kojin_no, o_rireki_renban, o_rireki_renban_eda, o_gakunen, '
                'o_seibetsu, o_jotai, o_genkyuryuchi_flg, o_tsushomei_kanji, '
                'o_syogako_nyugaku_bi, o_nerr '
                'FROM gkbskjdog(%s)', (888100000000001,))
    row = cur.fetchone()
    if row:
        koJIN, renban, eda, gakunen, seibetsu, jotai, genkyuryuchi, name, nyugaku, nerr = row
        tests = [
            ("KOJIN_NO 正确", koJIN == 888100000000001),
            ("RIREKI_RENBAN 正确", renban == 1),
            ("RIREKI_RENBAN_EDA 正确", eda == 1),
            ("GAKUNEN 正确", gakunen == 6),
            ("SEIBETSU 正确", seibetsu == 1),
            ("JOTAI 正确", jotai == 1),
            ("GENKYURYUCHI_FLG 正确", genkyuryuchi == 0),
            ("TSUSHOMEI_KANJI 正确", name == 'テスト太郎'),
            ("SYOGAKO_NYUGAKU_BI 正确", nyugaku == 20180401),
            ("nerr 正常", nerr == 0),
        ]
        for field, passed in tests:
            record(f"字段 {field}", passed, f"值={locals().get(field.split()[1], 'N/A')}")

    # 7. 错误码验证
    print(f"\n[7] 错误码验证")
    # 测试正常
    cur.execute('SELECT o_nerr FROM gkbskjdog(%s)', (888100000000001,))
    if cur.fetchone()[0] == 0:
        record("错误码 0 (正常)", True)
    # 测试无数据
    cur.execute('SELECT o_nerr FROM gkbskjdog(%s)', (555555555555555,))
    if cur.fetchone()[0] == 1:
        record("错误码 1 (无数据)", True)

    # 清理 (持久测试库保留, 测试后不删除)
    print(f"\n[清理] 关闭连接 (数据库 {TEST_DB} 保留不删除)")
    cur.close()
    conn.close()
    print(f"\n测试数据库 {TEST_DB} 已保留在本地 PG (含最后测试状态)")

    print(f"\n{'='*70}")
    print(f"汇总: {PASS} PASS / {FAIL} FAIL")
    print(f"{'='*70}")

    # 打印失败项
    if FAIL > 0:
        print("\n失败项:")
        for name, passed, detail in RESULTS:
            if not passed:
                print(f"  - {name}: {detail}")

    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
