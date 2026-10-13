#!/usr/bin/env python3
"""
verify_views.py - VIEW 转换结果验证

用法:
    python scripts/verify_views.py

验证:
    1. 语法正确性 (在 PostgreSQL 中加载)
    2. Oracle 特性标记 (OUTER_JOIN_PLUS, DECODE 等)
    3. 依赖表是否存在
"""
import sys
from pathlib import Path
import psycopg2
import re

POC = Path(__file__).parent.parent
VIEW_DIR = POC / "out" / "view"
DDL_DIR = POC / "out" / "ddl"
TEST_DB = "gkb_view_verify"


def get_ddl_tables():
    """获取 DDL 中的所有表名"""
    tables = set()
    for f in DDL_DIR.glob("*.pgsql"):
        content = f.read_text(encoding="utf-8")
        for m in re.finditer(r'CREATE TABLE (\w+)', content, re.IGNORECASE):
            tables.add(m.group(1).upper())
    return tables


def get_view_tables(sql):
    """从 VIEW SQL 中提取引用的表名"""
    from_match = re.search(r'\bFROM\b(.+?)(?:\bWHERE\b|\bORDER\s+BY\b|\bGROUP\s+BY\b)', 
                           sql, re.DOTALL | re.IGNORECASE)
    if not from_match:
        return set()
    # 提取单词形式的表名
    tables = set()
    for m in re.finditer(r'([A-Z][A-Z0-9_]{2,})', from_match.group(1)):
        tables.add(m.group(1).upper())
    return tables


def main():
    print("=" * 70)
    print("Oracle VIEW - PostgreSQL VIEW verification")
    print("=" * 70)

    # Get DDL tables
    ddl_tables = get_ddl_tables()
    print(f"\n[INFO] DDL tables: {len(ddl_tables)}")

    # Create test DB
    conn = psycopg2.connect(host='localhost', port=5432, user='postgres',
                            password='postgres', dbname='postgres')
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    cur.execute(f"CREATE DATABASE {TEST_DB} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
    cur.close()
    conn.close()

    conn = psycopg2.connect(host='localhost', port=5432, user='postgres',
                            password='postgres', dbname=TEST_DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")

    # Load DDL
    print(f"\n[1] Loading DDL ({len(list(DDL_DIR.glob('*.pgsql')))} files)")
    ddl_failed = 0
    for f in sorted(DDL_DIR.glob("*.pgsql")):
        try:
            cur.execute(f.read_text(encoding="utf-8"))
        except:
            ddl_failed += 1
    print(f"    DDL loaded: {len(list(DDL_DIR.glob('*.pgsql'))) - ddl_failed} OK, {ddl_failed} FAIL")

    # Verify VIEW
    print(f"\n[2] Verifying VIEW ({len(list(VIEW_DIR.glob('*.pgsql')))} files)")
    
    pass_count = 0
    fail_syntax = 0
    fail_missing = 0
    needs_manual = []
    
    for f in sorted(VIEW_DIR.glob("*.pgsql")):
        sql = f.read_text(encoding="utf-8")
        name = f.stem
        
        # Check for unconverted Oracle features
        issues = []
        if 'OUTER_JOIN_PLUS' in sql:
            issues.append('Oracle (+) outer join')
        if 'DECODE(' in sql.upper():
            issues.append('DECODE function')
        if 'CONNECT BY' in sql.upper():
            issues.append('CONNECT BY')
        
        # Check for missing tables
        view_tables = get_view_tables(sql)
        missing_tables = view_tables - ddl_tables
        
        # Try to load
        error_msg = None
        try:
            cur.execute(sql)
            status = 'PASS'
            pass_count += 1
        except psycopg2.Error as e:
            error_msg = str(e)
            # Check for table not found errors (in various languages)
            if 'does not exist' in error_msg.lower() or '不存在' in error_msg or 'not exist' in error_msg.lower():
                status = 'MISSING'
                fail_missing += 1
            else:
                status = 'FAIL'
                fail_syntax += 1
        
        info_parts = []
        if issues:
            info_parts.append(', '.join(issues))
        if missing_tables:
            info_parts.append(f'{len(missing_tables)} missing tables')
        if error_msg and status != 'PASS':
            # Extract table name from error
            m = re.search(r'relation "(\w+)"', error_msg)
            if m:
                info_parts.append(f'error: {m.group(1)}')
        
        info_str = f" ({', '.join(info_parts)})" if info_parts else ""
        print(f"    [{status:8}] {name}{info_str}")
        
        if issues:
            needs_manual.append((name, issues))

    print(f"\n[RESULT] {pass_count} PASS / {fail_syntax} SYNTAX_ERR / {fail_missing} MISSING_TABLES")
    
    if needs_manual:
        print(f"\n[NEEDS MANUAL CONVERSION] {len(needs_manual)} VIEWs:")
        for name, issues in needs_manual:
            print(f"    - {name}: {', '.join(issues)}")
    
    cur.close()
    conn.close()
    
    print(f"\n[DONE] Test DB {TEST_DB} preserved")
    
    # Return code: 0 if all syntax OK, 1 if syntax errors
    return 1 if fail_syntax > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
