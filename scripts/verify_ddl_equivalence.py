#!/usr/bin/env python3
"""
verify_ddl_equivalence.py - DDL 转换等价性全面验证

验证目标:
    1. PG 中能成功创建所有表
    2. Oracle NUMBER(p,0) → PG 整数类型 正确
    3. Oracle NVARCHAR2(n) → PG VARCHAR(n) 正确
    4. Oracle CLOB → PG TEXT 正确
    5. Oracle CHAR(n) → PG VARCHAR(n) 正确
    6. Oracle GLOBAL TEMPORARY TABLE → PG 普通表 正确
    7. NOT NULL 约束保留
    8. PRIMARY KEY 约束保留
    9. DEFAULT 值保留
   10. COMMENT 保留
   11. INDEX 保留
"""
import re
import sys
from pathlib import Path
import psycopg2


def read_source_file(path: Path) -> str:
    """读取源文件, 尝试多种编码。"""
    for enc in ('cp932', 'shift_jis', 'utf-8', 'gbk'):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, Exception):
            continue
    return path.read_bytes().decode('utf-8', errors='replace')


POC = Path(__file__).parent.parent
DDL_DIR = POC / "out" / "ddl"
SRC_DIR = POC / "source" / "table"   # 2026-09 修正: 原指向已删除的 source-shiftjis/
TEST_DB = "gkb_ddl_verify"

# 表名映射: Oracle 表名 -> PG 表名 (PG 默认小写, 但保持一致)
# 由于 PG 将未加引号的标识符转小写, PG 中实际表名是全小写
TABLE_MAP = {
    'GKBTGAKUREIBO': 'gkbtgakureibo',
    'GKBTZOKUGARA': 'gkbtzokugara',
    'GKBTHAITA': 'gkbthaita',
    'GKBTSHUGAKURIREKI': 'gkbtshugakurireki',
    'GKBWKYOIKUIDO': 'gkbwkyoikuido',
    # 2026-09 扩展
    'GKBTSHOGAKKO': 'gkbtshogakko',
    'GKBTCHUGAKKO': 'gkbtchugakko',
    'GKBTMSGAKKU': 'gkbtmsgakku',
    # 2026-09 扩展第 2 轮
    'GKBTSSJ': 'gkbtssj',
    'GKBWDB002001': 'gkbwdb002001',
    'GKBTBKLOG': 'gkbtbklog',
}

RESULTS = []
PASS_COUNT = 0
FAIL_COUNT = 0


def record(name: str, passed: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if passed:
        PASS_COUNT += 1
        mark = "[PASS]"
    else:
        FAIL_COUNT += 1
        mark = "[FAIL]"
    RESULTS.append((name, passed, detail))
    print(f"  {mark} {name}" + (f" - {detail}" if detail else ""))


def connect(db: str):
    return psycopg2.connect(
        host="localhost", port=5432, user="postgres", password="postgres",
        dbname=db, connect_timeout=10
    )


def parse_oracle_columns(sql_text: str):
    """从 CREATE TABLE 中提取所有列定义。"""
    # 使用栈式查找来处理嵌套括号
    m = re.search(r'CREATE\s+(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\s+\w+\s*\(',
                  sql_text, re.IGNORECASE)
    if not m:
        return []
    start = m.end()
    # 括号匹配
    depth = 1
    end = start
    for i, ch in enumerate(sql_text[start:], start):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                end = i
                break
    body = sql_text[start:end]
    columns = []
    for line in body.split('\n'):
        line = line.strip().rstrip(',').strip()
        if not line:
            continue
        line_upper = line.upper()
        if line_upper.startswith('CONSTRAINT'):
            continue
        if (line_upper.startswith('PRIMARY KEY')
                or line_upper.startswith('FOREIGN KEY')
                or line_upper.startswith('UNIQUE')
                or line_upper.startswith('CHECK')):
            continue
        if line in ['(', ')']:
            continue
        # 提取列名 (第一个标识符)
        col_m = re.match(r'(\w+)\s+', line)
        if not col_m:
            continue
        col_name = col_m.group(1).upper()
        # 过滤掉不是列名的关键字 (TABLESPACE, ENABLE, USING, LOGGING, NOCACHE 等)
        if col_name in ('TABLESPACE', 'ENABLE', 'USING', 'LOGGING', 'NOCACHE', 'NOLOGGING',
                         'PCTFREE', 'PCTUSED', 'INITRANS', 'MAXTRANS', 'STORAGE', 'BUFFER_POOL'):
            continue
        rest = line[col_m.end():].strip()

        # 提取类型 (在 NOT NULL 或 DEFAULT 之前的部分)
        # 类型格式: NUMBER(p,s), NVARCHAR2(n), CHAR(n), CLOB, DATE 等
        type_m = re.match(r'([A-Z0-9_]+(?:\s*\([^)]*\))?)', rest, re.IGNORECASE)
        if not type_m:
            continue
        col_type = type_m.group(1).strip().upper()
        rest_after_type = rest[type_m.end():].strip()

        not_null = bool(re.search(r'\bNOT\s+NULL\b', rest_after_type, re.IGNORECASE))
        default_match = re.search(r'DEFAULT\s+(\S+)', rest_after_type, re.IGNORECASE)
        default = default_match.group(1) if default_match else None

        columns.append({
            'name': col_name,
            'type': col_type,
            'not_null': not_null,
            'default': default
        })
    return columns


def get_pg_columns(cur, table_name: str):
    """从 information_schema 获取 PG 表的所有列定义。"""
    cur.execute("""
        SELECT column_name, data_type, character_maximum_length,
               is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table_name.lower(),))
    result = []
    for row in cur.fetchall():
        result.append({
            'name': row[0].upper(),
            'data_type': row[1],
            'max_len': row[2],
            'nullable': row[3] == 'YES',
            'default': row[4]
        })
    return result


def get_pg_pk(cur, table_name: str):
    """获取 PG 表的主键列。"""
    cur.execute("""
        SELECT a.attname
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_attribute a ON a.attrelid = con.conrelid
               AND a.attnum = ANY(con.conkey)
        WHERE con.contype = 'p'
          AND c.relname = %s
        ORDER BY array_position(con.conkey, a.attnum)
    """, (table_name.lower(),))
    return [r[0].upper() for r in cur.fetchall()]


def get_pg_indexes(cur, table_name: str):
    """获取 PG 表的索引。"""
    cur.execute("""
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = %s
    """, (table_name.lower(),))
    return [r[0] for r in cur.fetchall()]


def get_pg_table_comment(cur, table_name: str):
    """获取 PG 表的注释。"""
    cur.execute("""
        SELECT d.description FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_description d ON d.objoid = c.oid AND d.objsubid = 0
        WHERE c.relname = %s AND n.nspname = 'public'
    """, (table_name.lower(),))
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def expected_pg_type(oracle_type: str):
    """根据转换规则, 返回期望的 PG 类型。"""
    t = oracle_type.strip().upper()
    # NUMBER(p,0)
    m = re.match(r'NUMBER\s*\(\s*(\d+)\s*,\s*0\s*\)', t)
    if m:
        p = int(m.group(1))
        if 1 <= p <= 4:
            return 'smallint', None
        elif 5 <= p <= 9:
            return 'integer', None
        elif 10 <= p <= 18:
            return 'bigint', None
        else:
            return 'numeric', None
    # NUMBER(p,s)
    m = re.match(r'NUMBER\s*\(\s*(\d+)\s*,\s*(-?\d+)\s*\)', t)
    if m:
        return 'numeric', None
    # NUMBER(p)
    m = re.match(r'NUMBER\s*\(\s*(\d+)\s*\)', t)
    if m:
        p = int(m.group(1))
        if 1 <= p <= 4:
            return 'smallint', None
        elif 5 <= p <= 9:
            return 'integer', None
        elif 10 <= p <= 18:
            return 'bigint', None
        else:
            return 'numeric', None
    # NVARCHAR2(n) / VARCHAR2(n)
    m = re.match(r'(?:N)?VARCHAR2\s*\(\s*(\d+)\s*\)', t)
    if m:
        return 'character varying', int(m.group(1))
    # CHAR(n)
    m = re.match(r'CHAR\s*\(\s*(\d+)\s*\)', t)
    if m:
        return 'character varying', int(m.group(1))
    # CLOB / NCLOB / LONG
    if t in ('CLOB', 'NCLOB', 'LONG'):
        return 'text', None
    # BLOB
    if t == 'BLOB':
        return 'bytea', None
    # DATE
    if t == 'DATE':
        return 'timestamp without time zone', None
    # TIMESTAMP(n): PG 默认精度即 6 (微秒), TIMESTAMP(6)→TIMESTAMP 等价
    if re.match(r'TIMESTAMP\s*(\(\d\))?\s*(WITHOUT\s+TIME\s+ZONE)?$', t):
        return 'timestamp without time zone', None
    return None, None


def main():
    print("\n" + "=" * 70)
    print("DDL 等价性全面验证")
    print("=" * 70)

    # ===== 1. 重建测试库 =====
    print("\n1. 重建测试库")
    try:
        conn = connect("postgres")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if cur.fetchone() is None:
            cur.execute(f"CREATE DATABASE {TEST_DB} TEMPLATE template0 "
                         f"ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
        cur.close()
        conn.close()
        record("创建 UTF8 数据库", True)
    except Exception as e:
        record("创建数据库", False, str(e))
        return

    # ===== 2. 应用所有 DDL =====
    print("\n2. 应用 DDL")
    conn = connect(TEST_DB)
    conn.autocommit = True
    cur = conn.cursor()
    # 持久测试库: 重建 schema 内容 (每次刷新), 数据库本身保留不删除
    cur.execute("DROP SCHEMA public CASCADE;")
    cur.execute("CREATE SCHEMA public;")
    cur.execute("GRANT ALL ON SCHEMA public TO postgres;")


    for f in sorted(DDL_DIR.glob("*.pgsql")):
        try:
            cur.execute(f.read_text(encoding="utf-8"))
            record(f"DDL {f.stem}", True)
        except Exception as e:
            record(f"DDL {f.stem}", False, str(e).splitlines()[0])

    # ===== 3. 类型映射全面验证 =====
    print("\n3. 类型映射验证")
    type_check = {}
    for f in sorted(SRC_DIR.glob("*.SQL")):
        table_oracle = f.stem
        table_pg = TABLE_MAP.get(table_oracle)
        if not table_pg:
            continue

        sql = read_source_file(f)
        oracle_cols = parse_oracle_columns(sql)
        pg_cols = get_pg_columns(cur, table_pg)
        pg_col_dict = {c['name']: c for c in pg_cols}

        type_check[table_oracle] = {'total': 0, 'matched': 0, 'mismatched': []}

        for col in oracle_cols:
            if col['name'] not in pg_col_dict:
                continue
            type_check[table_oracle]['total'] += 1
            exp_type, exp_len = expected_pg_type(col['type'])
            if exp_type is None:
                continue

            pg = pg_col_dict[col['name']]
            ok = pg['data_type'] == exp_type
            if exp_len is not None:
                ok = ok and (pg['max_len'] == exp_len)

            if ok:
                type_check[table_oracle]['matched'] += 1
            else:
                type_check[table_oracle]['mismatched'].append({
                    'col': col['name'],
                    'oracle_type': col['type'],
                    'expected': f"{exp_type}({exp_len})" if exp_len else exp_type,
                    'actual': f"{pg['data_type']}({pg['max_len']})" if pg['max_len'] else pg['data_type']
                })

    for table, result in type_check.items():
        if result['total'] == 0:
            continue
        ratio = result['matched'] / result['total']
        ok = ratio == 1.0
        detail = f"{result['matched']}/{result['total']}"
        if not ok and result['mismatched']:
            for m in result['mismatched'][:3]:
                detail += f" [{m['col']}: {m['oracle_type']} -> exp {m['expected']}, act {m['actual']}]"
        record(f"类型映射 {table}", ok, detail)

    # ===== 4. PRIMARY KEY 验证 =====
    print("\n4. PRIMARY KEY 验证")
    pk_expectations = {
        'GKBTGAKUREIBO': ['KOJIN_NO', 'RIREKI_RENBAN', 'RIREKI_RENBAN_EDA'],
        'GKBTZOKUGARA': ['ZOKUGARA_KBN', 'ZOKUGARA_CD'],
        'GKBTHAITA': ['HAITA_KBN_CD', 'HAITA_KEY'],
        'GKBTSHUGAKURIREKI': ['KOJIN_NO', 'NENDO', 'KOJIN_RIREKI_RENBAN', 'SHUGAKU_RIREKI_RENBAN'],
        # 2026-09 扩展表
        'GKBTSHOGAKKO': ['SYOGAKO_CD'],
        'GKBTCHUGAKKO': ['TYUGAKO_CD'],
        'GKBTMSGAKKU': ['GAKKOSHIKIBETSU', 'GAKKO_KBN_CD', 'RENBAN'],
        'GKBTSSJ': ['SHINSEIKANRISEQ', 'SHINSEISHORIJOKYOSEQ'],
        'GKBWDB002001': ['RIREKISEQ', 'FILESEQ', 'DATAKBN', 'ROWNUMBER'],
        'GKBTBKLOG': [],
    }
    for table_oracle, expected in pk_expectations.items():
        table_pg = TABLE_MAP.get(table_oracle)
        actual = get_pg_pk(cur, table_pg)
        ok = actual == expected
        record(f"PK {table_oracle}", ok,
               f"期望 {expected}, 实际 {actual}" if not ok else f"{len(expected)} 列")

    # ===== 5. NOT NULL 验证 =====
    print("\n5. NOT NULL 验证")
    cur.execute("""
        SELECT table_name, COUNT(*) FILTER (WHERE is_nullable = 'NO'),
               COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public'
        GROUP BY table_name
        ORDER BY table_name
    """)
    not_null_data = {}
    for row in cur.fetchall():
        table_pg = row[0]
        not_null_data[table_pg] = (row[1], row[2])

    expected_not_null = {
        'gkbtgakureibo': 10,    # PK 3 + DEFAULT NOT NULL 7
        'gkbtzokugara': 2,      # PK 2
        'gkbthaita': 7,         # 多列 NOT NULL
        'gkbtshugakurireki': 4, # PK 4
        # 2026-09 扩展表
        'gkbtshogakko': 1,      # PK SYOGAKO_CD
        'gkbtchugakko': 1,      # PK TYUGAKO_CD
        'gkbtmsgakku': 3,       # GAKKOSHIKIBETSU/GAKKO_KBN_CD/RENBAN
        'gkbtssj': 2,           # PK 两列 (均 DEFAULT 0 NOT NULL)
        'gkbwdb002001': 6,      # RIREKISEQ/FILESEQ/FILENAME/CONSERVE_FILENAME/DATAKBN/ROWNUMBER
        'gkbtbklog': 0,         # 纯日志表
    }
    for table_pg, expected_nn in expected_not_null.items():
        if table_pg in not_null_data:
            actual_nn, total = not_null_data[table_pg]
            record(f"NOT NULL {table_pg.upper()}", actual_nn == expected_nn,
                   f"期望 {expected_nn}, 实际 {actual_nn}/{total}")
        else:
            record(f"NOT NULL {table_pg.upper()}", False, "表不存在")

    # GKBWKYOIKUIDO 是 GTT 源文件没有 NOT NULL
    if 'gkbwkyoikuido' in not_null_data:
        actual_nn, total = not_null_data['gkbwkyoikuido']
        record("NOT NULL GKBWKYOIKUIDO (GTT 无 NOT NULL)", actual_nn == 0,
               f"{actual_nn}/{total}")

    # ===== 6. COMMENT 验证 =====
    print("\n6. COMMENT 验证")
    comment_expectations = {
        'gkbtgakureibo': '学齢簿',
        'gkbtzokugara': '続柄',
        'gkbthaita': '排他制御',
        'gkbtshugakurireki': '就学履歴',
        'gkbwkyoikuido': '教育異動者一覧ワーク',
    }
    for table_pg, expected in comment_expectations.items():
        actual = get_pg_table_comment(cur, table_pg)
        ok = actual == expected
        record(f"表注释 {table_pg.upper()}", ok,
               f"期望 '{expected}', 实际 '{actual}'" if not ok else actual)

    # ===== 7. INDEX 验证 =====
    print("\n7. INDEX 验证")
    for table_oracle in ['GKBTGAKUREIBO', 'GKBTZOKUGARA', 'GKBTHAITA', 'GKBTSHUGAKURIREKI']:
        table_pg = TABLE_MAP.get(table_oracle)
        idx = get_pg_indexes(cur, table_pg)
        non_pk = [i for i in idx if '_pkey' not in i]
        record(f"索引 {table_oracle}", len(idx) > 0,
               f"共 {len(idx)} 个 (非PK: {len(non_pk)})")

    # ===== 8. 临时表转换验证 =====
    print("\n8. 临时表转换验证")
    cur.execute("""
        SELECT relname, relpersistence FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND relkind = 'r'
        ORDER BY relname
    """)
    pg_tables = {r[0]: r[1] for r in cur.fetchall()}

    for table_pg in ['gkbtgakureibo', 'gkbtzokugara', 'gkbthaita', 'gkbtshugakurireki', 'gkbwkyoikuido']:
        if table_pg in pg_tables:
            record(f"表存在 {table_pg}", True)
        else:
            record(f"表存在 {table_pg}", False, "PG 中未找到")

    # GKBWKYOIKUIDO 应该是普通表 ('p' = permanent)
    if 'gkbwkyoikuido' in pg_tables:
        pers = pg_tables['gkbwkyoikuido']
        record("GTT->普通表 GKBWKYOIKUIDO", pers == 'p',
               f"relpersistence={pers} (期望 'p' 普通表)")

    # ===== 9. NUMERIC 残留检查 =====
    print("\n9. NUMERIC 残留检查")
    cur.execute("""
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type = 'numeric'
    """)
    numeric_count = cur.fetchone()[0]
    record("无 NUMERIC(p,0) 残留", numeric_count == 0,
           f"{numeric_count} 个 NUMERIC 列" if numeric_count else "全部使用原生整数类型")

    # ===== 10. 列数对比 =====
    print("\n10. 列数对比")
    for f in sorted(SRC_DIR.glob("*.SQL")):
        table_oracle = f.stem
        table_pg = TABLE_MAP.get(table_oracle)
        if not table_pg or table_pg not in pg_tables:
            continue
        sql = read_source_file(f)
        oracle_cols = parse_oracle_columns(sql)
        cur.execute("""
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
        """, (table_pg,))
        pg_count = cur.fetchone()[0]
        ok = len(oracle_cols) == pg_count
        record(f"列数 {table_oracle}", ok,
               f"Oracle {len(oracle_cols)} 列, PG {pg_count} 列" if not ok else f"{len(oracle_cols)} 列")

    # ===== 11. 特殊类型行为 (CLOB/BLOB/TIMESTAMP, 2026-09 扩展并入) =====
    print("\n11. 特殊类型行为")
    # 最小内联数据 (本脚本不依赖 testdata/ 目录)
    cur.execute("""
        INSERT INTO GKBTSSJ (SHINSEIKANRISEQ, SHINSEISHORIJOKYOSEQ, RENRAKUJIKO,
                             SHINSEISHORIINSDATETIME) VALUES
        (1, 1, '連絡事項テスト: 本人確認電話必要、7月20日までに返信すること。',
         TIMESTAMP '2026-07-14 09:30:00.123456')
    """)
    cur.execute("""
        INSERT INTO GKBWDB002001 (RIREKISEQ, FILESEQ, FILENAME, DATAKBN, ROWNUMBER, DATA)
        VALUES (1, 1, 'TEST001.CSV', 1, 1, '\\x4b474b0a74657374'::bytea)
    """)
    cur.execute("INSERT INTO GKBWDB002001 (RIREKISEQ, FILESEQ, FILENAME, DATAKBN, ROWNUMBER) VALUES (1, 1, 'TEST001.CSV', 2, 1)")
    cur.execute("INSERT INTO GKBTBKLOG (TOUROKU_RENBAN, SHORIBI, SHORIJIKAN) VALUES (1, 20260714, 90000)")

    cur.execute("SELECT SHINSEISHORIINSDATETIME FROM GKBTSSJ WHERE SHINSEISHORIJOKYOSEQ=1")
    v = cur.fetchone()[0]
    record("TIMESTAMP(6) 微秒精度 (GKBTSSJ)",
           v is not None and v.microsecond == 123456, str(v))
    cur.execute("SELECT char_length(RENRAKUJIKO), position('確認' in RENRAKUJIKO) FROM GKBTSSJ")
    row = cur.fetchone()
    record("CLOB→TEXT 日文内容 (GKBTSSJ)", row[0] >= 20 and row[1] > 0, str(row))
    try:
        cur.execute("INSERT INTO GKBTSSJ (SHINSEIKANRISEQ, SHINSEISHORIJOKYOSEQ) VALUES (1, 1)")
        record("复合 PK 去重 (GKBTSSJ)", False, "未拒绝")
    except psycopg2.errors.UniqueViolation:
        record("复合 PK 去重 (GKBTSSJ)", True)
    conn.rollback()

    cur.execute("SELECT encode(DATA, 'hex') FROM GKBWDB002001 WHERE DATAKBN=1")
    v = cur.fetchone()[0]
    record("BLOB→BYTEA 二进制往返 (GKBWDB002001)", v == "4b474b0a74657374", str(v))
    cur.execute("SELECT COUNT(*) FROM GKBWDB002001 WHERE RIREKISEQ=1 AND FILESEQ=1 AND DATA IS NULL AND DATAKBN=2")
    n = cur.fetchone()[0]
    record("4列复合 PK + NULL BLOB (GKBWDB002001)", n == 1, f"n={n}")
    cur.execute("SELECT COUNT(*) FROM GKBTBKLOG")
    n = cur.fetchone()[0]
    record("纯日志表无约束插入 (GKBTBKLOG)", n == 1, f"n={n}")

    # 11b. 扩展表数据行为: DEFAULT / 长文本 / PK / NOT NULL (学校/学区表)
    name60 = '最大文字数小学校_maximum_length_test_1234567890123456789012345678901'  # 60 字
    cur.execute("INSERT INTO GKBTSHOGAKKO (SYOGAKO_CD, SYOGAKOMEI, TORIKOMI_KBN) VALUES (1, '第一小学校', 1)")
    cur.execute("INSERT INTO GKBTSHOGAKKO (SYOGAKO_CD, SYOGAKOMEI) VALUES (9, %s)", (name60,))
    cur.execute("INSERT INTO GKBTCHUGAKKO (TYUGAKO_CD, TYUGAKOMEI, TORIKOMI_KBN) VALUES (1, '第一中学校', 1)")
    cur.execute("INSERT INTO GKBTMSGAKKU (OAZA_CD, GAKKOKU_CD) VALUES (200, 'C01')")
    cur.execute("""
        INSERT INTO GKBTMSGAKKU (GAKKOSHIKIBETSU, GAKKO_KBN_CD, RENBAN, SAISHIN_FLG, OAZA_CD, GAKKOKU_CD, GAIKU_FR)
        VALUES ('S', 1, 1, 0, 100, 'S01', '00001')
    """)

    cur.execute("SELECT COUNT(*) FROM GKBTSHOGAKKO WHERE char_length(SYOGAKOMEI) >= 60")
    n = cur.fetchone()[0]
    record("NVARCHAR2(60) 长文本保留 (GKBTSHOGAKKO)", n == 1, f"n={n}")
    try:
        cur.execute("INSERT INTO GKBTSHOGAKKO (SYOGAKO_CD) VALUES (1)")
        record("PK 去重 (GKBTSHOGAKKO)", False, "未拒绝")
    except psycopg2.errors.UniqueViolation:
        record("PK 去重 (GKBTSHOGAKKO)", True)
    conn.rollback()
    try:
        cur.execute("INSERT INTO GKBTSHOGAKKO (SYOGAKO_CD) VALUES (NULL)")
        record("NOT NULL (GKBTSHOGAKKO)", False, "未拒绝")
    except psycopg2.errors.NotNullViolation:
        record("NOT NULL (GKBTSHOGAKKO)", True)
    conn.rollback()

    cur.execute("SELECT GAKKOSHIKIBETSU, GAKKO_KBN_CD, RENBAN FROM GKBTMSGAKKU WHERE OAZA_CD=200")
    row = cur.fetchone()
    record("3 个 DEFAULT 生效 (GKBTMSGAKKU)", row == (' ', 0, 0), str(row))

    # ===== 总结 =====
    print("\n" + "=" * 70)
    print(f"总结: {PASS_COUNT} PASS / {FAIL_COUNT} FAIL")
    print("=" * 70)

    if FAIL_COUNT > 0:
        print("\n失败详情:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  [FAIL] {name} - {detail}")

    # 清理 (持久测试库保留, 测试后不删除)
    print(f"\n[清理] 关闭连接 (数据库 {TEST_DB} 保留不删除)")
    cur.close()
    conn.close()
    print(f"\n测试数据库 {TEST_DB} 已保留在本地 PG (含最后测试状态)")

    sys.exit(0 if FAIL_COUNT == 0 else 1)


if __name__ == "__main__":
    main()
