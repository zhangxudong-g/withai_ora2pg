#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为所有案例添加业务目的和依赖表/字段信息
Phase 1 完全收尾 - 完善覆盖矩阵
"""
import os
from pathlib import Path

# 案例元数据: 业务目的、依赖表、输入条件、预期输出
CASE_METADATA = {
    "01_basic_select": {
        "business_purpose": "查询学齢簿中有效学生基本信息 (成人式対象者抽出)",
        "dependencies": {
            "tables": ["GKBTSEIJINSHA"],
            "fields": ["KOJIN_NO", "NENDO", "SEIREKI", "SEINENGAPI", "SEIBETSU", "KOJIN_CD", "SAKUJO_FLG"]
        },
        "input_conditions": "SAKUJO_FLG = 0 (未削除)",
        "expected_output": "按 NENDO 降序, KOJIN_NO 升序排列的成人式対象者",
        "manual_fix_needed": False
    },
    "02_multi_join": {
        "business_purpose": "查询选择学校申请信息, 包括选择小学校区名称",
        "dependencies": {
            "tables": ["GKBTGGAKKOSHINKOHJS", "GABVATENAKIHON", "GKBTMSGAKKO"],
            "fields": ["KOJIN_NO", "SENTAKUGAKKO_CD", "SHIMEI_KANJI", "SENTAKUGAKKO_NM"]
        },
        "input_conditions": "SAKUJO_FLG = 0",
        "expected_output": "选择学校申请者 + 宛名 + 学校区名称",
        "manual_fix_needed": False
    },
    "03_outer_join": {
        "business_purpose": "查询学龄簿DV受害者, 包括学校信息, 关联表可能不存在",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GACTIDOHAKKOYOKUSHIKANRI", "GKBTMSGAKKO"],
            "fields": ["KOJIN_NO", "SYOGAKO_CD", "TYUGAKO_CD", "GENZON_KBN", "SAKUJO_FLG"]
        },
        "input_conditions": "DV期间包含当前日期, 学年 0-9",
        "expected_output": "可能无DV记录的学龄簿学生 (左外连接)",
        "manual_fix_needed": True,
        "manual_fix_note": "Oracle (+) 语法需转为 ANSI JOIN"
    },
    "04_exists_not_exists": {
        "business_purpose": "检查学生是否在系统中存在或不存在",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABTJUKIIDO"],
            "fields": ["KOJIN_NO", "GENZON_KBN"]
        },
        "input_conditions": "EXISTS 子查询检查",
        "expected_output": "布尔结果 (True/False)",
        "manual_fix_needed": False
    },
    "05_union": {
        "business_purpose": "汇总调查数据, 多个表合并查询",
        "dependencies": {
            "tables": ["GKBTSUIKEICHOSAKNR", "GKBTSUIKEICHOSAKNR2"],
            "fields": ["TOROKU_NO", "KOUMOKU1", "KOUMOKU2", "KOUMOKU3"]
        },
        "input_conditions": "SAKUJO_FLG = 0",
        "expected_output": "去重或保留所有行",
        "manual_fix_needed": False
    },
    "06_group_by": {
        "business_purpose": "统计各学年学生人数",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABTATENARIREKI"],
            "fields": ["KOJIN_NO", "GAKUNEN", "JUMIN_NARU", "JUMIN_NAKUNARU"]
        },
        "input_conditions": "按 KOJIN_NO 分组",
        "expected_output": "每个学年的学生数",
        "manual_fix_needed": False
    },
    "07_analytic_functions": {
        "business_purpose": "对学龄簿进行窗口函数分析 (排名、累计)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO"],
            "fields": ["KOJIN_NO", "GAKUNEN", "RIREKI_RENBAN"]
        },
        "input_conditions": "按 KOJIN_NO 分区, 按某列排序",
        "expected_output": "每行的排名/累计值",
        "manual_fix_needed": False
    },
    "08_rownum_pagination": {
        "business_purpose": "分页查询学龄簿列表",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO"],
            "fields": ["KOJIN_NO", "GAKUNEN"]
        },
        "input_conditions": "ROWNUM 范围 11-20",
        "expected_output": "第2页数据 (10条)",
        "manual_fix_needed": True,
        "manual_fix_note": "ROWNUM 需转为 LIMIT/OFFSET"
    },
    "09_date_handling": {
        "business_purpose": "处理 Oracle 8位数字日期 (YYYYMMDD) 比较",
        "dependencies": {
            "tables": ["GABTJUKIIDO", "GABTATENARIREKI"],
            "fields": ["JUMIN_NARU", "JUMIN_NAKUNARU", "SYSDATE"]
        },
        "input_conditions": "日期数值化存储",
        "expected_output": "正确的日期范围判断",
        "manual_fix_needed": True,
        "manual_fix_note": "SYSDATE 需转为 CURRENT_DATE, 数字日期可能需转型"
    },
    "10_null_handling": {
        "business_purpose": "处理学龄簿 NULL 值, 提供默认值",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABTATENAKIHON"],
            "fields": ["NVL(ZOKUGARA)", "NVL(GENZON_KBN)", "NVL(TSUSHOMEI)"]
        },
        "input_conditions": "NULL 替换为默认值",
        "expected_output": "NVL → COALESCE 转换",
        "manual_fix_needed": False
    },
    "11_decode_nvl": {
        "business_purpose": "代码值转换 (减事由, 学校区分) 到名称",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO"],
            "fields": ["GEN_JIYU_CD", "GAKKO_KBN_CD"]
        },
        "input_conditions": "代码值匹配",
        "expected_output": "DECODE → CASE WHEN 转换",
        "manual_fix_needed": True,
        "manual_fix_note": "DECODE 需转为 CASE WHEN, 注意 NULL 比较"
    },
    "12_case_expression": {
        "business_purpose": "根据学校区分代码动态选择查询字段",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GKBTMSGAKUKYUCD"],
            "fields": ["GAKKO_KBN_CD", "SYOGAKO_CD", "TYUGAKO_CD", "YOGOGAKO_CD", "KUIKIGAI_CD"]
        },
        "input_conditions": "根据 GAKKO_KBN_CD 选择不同的子查询",
        "expected_output": "对应学校的学级名称",
        "manual_fix_needed": False
    },
    "13_merge": {
        "business_purpose": "同步学龄簿数据 (存在则更新, 不存在则插入)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO"],
            "fields": ["KOJIN_NO", "GAKUNEN", "GENZON_KBN", "SYS_KOSHINBI"]
        },
        "input_conditions": "根据主键匹配",
        "expected_output": "INSERT/UPDATE 一体化操作",
        "manual_fix_needed": True,
        "manual_fix_note": "MERGE 需用 INSERT ON CONFLICT 替代"
    },
    "14_insert_select": {
        "business_purpose": "插入排他控制记录",
        "dependencies": {
            "tables": ["GKBTHAITA"],
            "fields": ["HAITA_KBN_CD", "HAITA_KEY", "HAITA_TAISHOSHA_MEI", "SHORI_Y_MD"]
        },
        "input_conditions": "排他控制参数",
        "expected_output": "插入排他记录 (自动提交)",
        "manual_fix_needed": False
    },
    "15_update_delete_subquery": {
        "business_purpose": "根据子查询更新或删除相关数据",
        "dependencies": {
            "tables": ["GKBTSHIMEIJKN", "GKBTGAKUREIBO"],
            "fields": ["CHOHYO_KBN", "SAKUJO_FLG", "KOJIN_NO", "RIREKI_RENBAN"]
        },
        "input_conditions": "EXISTS 子查询条件",
        "expected_output": "UPDATE/DELETE 影响相关行",
        "manual_fix_needed": True,
        "manual_fix_note": "Oracle 关联更新语法需转为 PostgreSQL UPDATE...FROM 语法"
    },
    "16_dynamic_sql": {
        "business_purpose": "动态构建 SQL 查询学龄簿 (根据条件拼接 WHERE)",
        "dependencies": {
            "tables": ["GKBTMSGAKKU", "GABVATENAALL", "KKATCD"],
            "fields": ["GAKKO_KBN_CD", "GAKKOKU_CD", "MEISHO", "KOJIN_NO"]
        },
        "input_conditions": "根据学区管理区分动态拼接 SQL",
        "expected_output": "EXECUTE IMMEDIATE + OPEN FOR",
        "manual_fix_needed": True,
        "manual_fix_note": "EXECUTE IMMEDIATE 需转为 plpgsql EXECUTE, 需注意 SQL 注入防护"
    },
    "17_ref_cursor": {
        "business_purpose": "返回查询结果的游标 (用于应用层遍历)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABVATENAKIHON"],
            "fields": ["*"]
        },
        "input_conditions": "动态 SQL 返回游标",
        "expected_output": "SYS_REFCURSOR → REFCURSOR/TABLE",
        "manual_fix_needed": True,
        "manual_fix_note": "REF CURSOR 需转为 REFCURSOR 或 TABLE 返回"
    },
    "18_function": {
        "business_purpose": "学龄簿学年代码转换 (外部输入 → 内部代码)",
        "dependencies": {
            "tables": ["(无直接表)"],
            "fields": ["学年代码 → 基本データリスト学年コード"]
        },
        "input_conditions": "外部学年代码 (0-9)",
        "expected_output": "E3/P1-P6/J1-J3 内部代码",
        "manual_fix_needed": True,
        "manual_fix_note": "PL/SQL Function 需转为 plpgsql Function, OUT 参数需调整"
    },
    "19_procedure": {
        "business_purpose": "资格信息取得 (判断个人基准日是否在学龄簿)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABTATENAKIHON"],
            "fields": ["KOJIN_NO", "HOGOSYA_KOJIN_NO", "ZO_NENGAPI", "GEN_NENGAPI"]
        },
        "input_conditions": "个人番号 + 基准日",
        "expected_output": "资格状况 (1: 现在无资格, 2: 资格有, 9: 异常)",
        "manual_fix_needed": True,
        "manual_fix_note": "PG 14 不支持 CREATE PROCEDURE, 需用 Function 替代"
    },
    "20_package": {
        "business_purpose": "健康診断通知書/票出力 (业务功能封装)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABTATENAKIHON", "KKATOPRT"],
            "fields": ["全字段 + 各种业务参数"]
        },
        "input_conditions": "个人番号 + 履歴連番",
        "expected_output": "健康診断通知書/票 PDF 文件",
        "manual_fix_needed": True,
        "manual_fix_note": "Package 需转为 Schema + Functions, 全局变量需重构"
    },
    "21_exception_handling": {
        "business_purpose": "异常处理 - NO_DATA_FOUND, OTHERS 等",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABTATENARIREKI", "GABTJUKIJUSHO"],
            "fields": ["KOJIN_NO", "JUMIN_NAKUNARU", "SQLCODE", "SQLERRM"]
        },
        "input_conditions": "查询不存在数据时触发异常",
        "expected_output": "EXCEPTION WHEN NO_DATA_FOUND 等处理",
        "manual_fix_needed": True,
        "manual_fix_note": "SQLCODE/SQLERRM 处理, PG 用 GET DIAGNOSTICS"
    },
    "22_transaction_control": {
        "business_purpose": "排他控制 - 表格锁定 + 排他检查 + 排他设置",
        "dependencies": {
            "tables": ["GKBTHAITA"],
            "fields": ["HAITA_KBN_CD", "HAITA_KEY"]
        },
        "input_conditions": "排他控制区分 + 排他控制键",
        "expected_output": "LOCK TABLE + INSERT + COMMIT/ROLLBACK",
        "manual_fix_needed": True,
        "manual_fix_note": "PRAGMA AUTONOMOUS_TRANSACTION 无 PG 等价, 需 dblink 替代"
    },
    "23_dbms_sql": {
        "business_purpose": "动态列查询 (列数动态变化)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO"],
            "fields": ["60+ 列 (动态)"]
        },
        "input_conditions": "动态列定义 + 提取数据",
        "expected_output": "DBMS_SQL.OPEN_CURSOR + PARSE + DEFINE_COLUMN + FETCH_ROWS",
        "manual_fix_needed": True,
        "manual_fix_note": "DBMS_SQL 无 PG 等价, 必须重构为 plpgsql 动态 SQL 或应用层处理"
    },
    "24_trigger": {
        "business_purpose": "数据变更审计 (自动记录修改时间、操作者)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO", "GABTATENAKIHON"],
            "fields": ["SYS_SAKUSEIBI", "SYS_KOSHINBI", ":NEW", ":OLD"]
        },
        "input_conditions": "BEFORE INSERT / AFTER UPDATE",
        "expected_output": "触发器自动设置系统字段",
        "manual_fix_needed": True,
        "manual_fix_note": "COMPOUND TRIGGER 不支持, 拆分为多个触发器"
    },
    "25_collection_type": {
        "business_purpose": "批量处理 (BULK COLLECT, FORALL)",
        "dependencies": {
            "tables": ["GKBTGAKUREIBO"],
            "fields": ["全字段"]
        },
        "input_conditions": "批量查询 / 批量插入",
        "expected_output": "VARRAY / TABLE OF RECORD",
        "manual_fix_needed": True,
        "manual_fix_note": "VARRAY/TABLE → ARRAY/临时表, FORALL → UNNEST"
    }
}


def update_case_readme(case_dir_name: str, metadata: dict):
    """更新单个案例的 README.md, 添加业务元数据"""
    case_path = Path(f"fixtures/cases/{case_dir_name}/README.md")

    if not case_path.exists():
        print(f"  [SKIP] {case_dir_name} - README not found")
        return False

    content = case_path.read_text(encoding="utf-8")

    # 检查是否已有元数据
    if "## 业务元数据" in content:
        print(f"  [SKIP] {case_dir_name} - already updated")
        return False

    # 构建元数据节
    deps = metadata["dependencies"]
    tables_str = ", ".join(deps.get("tables", []))
    fields_str = ", ".join(deps.get("fields", []))

    manual_fix = "✅ 是" if metadata.get("manual_fix_needed") else "❌ 否"
    manual_note = metadata.get("manual_fix_note", "")

    metadata_section = f"""

---

## 业务元数据

### 业务目的

{metadata['business_purpose']}

### 依赖信息

| 项目 | 内容 |
|------|------|
| **依赖表** | {tables_str} |
| **关键字段** | {fields_str} |
| **输入条件** | {metadata.get('input_conditions', 'N/A')} |
| **预期输出** | {metadata.get('expected_output', 'N/A')} |

### 转换评估

| 项目 | 评估 |
|------|------|
| **需要人工修正** | {manual_fix} |
| **修正说明** | {manual_note or 'N/A'} |
"""

    # 在 "## 转换说明" 之前插入
    if "## 转换说明" in content:
        content = content.replace("## 转换说明", metadata_section + "\n## 转换说明", 1)
    else:
        # 否则添加到末尾
        content = content + metadata_section

    case_path.write_text(content, encoding="utf-8")
    print(f"  [OK] {case_dir_name} - updated")
    return True


def main():
    print("[*] Updating case metadata...\n")

    updated = 0
    skipped = 0
    for case_dir, data in CASE_METADATA.items():
        if update_case_readme(case_dir, data):
            updated += 1
        else:
            skipped += 1

    print(f"\n[*] Done! Updated: {updated}, Skipped: {skipped}")


if __name__ == "__main__":
    main()