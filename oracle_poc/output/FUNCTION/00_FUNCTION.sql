-- Perl-style Ora2Py Warnings:
-- Lifted nested BUILD_SQL as standalone PG function
-- Lifted nested validate_student_no as standalone PG function
-- Lifted nested CALC_WEIGHTED_AVG as standalone PG function
-- Lifted nested BUILD_SQL as standalone PG function
-- Lifted nested get_credit_info as standalone PG function
-- Lifted nested COMPARE_SCORE as standalone PG function
-- Lifted nested DETERMINE_LEVEL as standalone PG function
-- Lifted nested get_threshold as standalone PG function
-- Lifted nested BUILD_SUBQUERY as standalone PG function
-- Lifted nested BUILD_FULL_SQL as standalone PG function
-- Lifted nested get_student_total as standalone PG function
-- Lifted nested VALIDATE_FILTER as standalone PG function
-- Lifted nested BUILD_QUERY as standalone PG function
-- Lifted nested get_student_info as standalone PG function
-- Lifted nested GET_SUBJECT_NAME as standalone PG function
-- Lifted nested FORMAT_CONTENT as standalone PG function
-- Lifted nested BUILD_QUERY as standalone PG function
-- Lifted nested validate_student as standalone PG function
-- Lifted nested build_sql as standalone PG function
-- Lifted nested CALC_WEIGHTED_SUM as standalone PG function
-- Lifted nested CALC_TOTAL_WEIGHT as standalone PG function
-- Lifted nested get_all_scores as standalone PG function
-- Lifted nested CLEAN_VALUE as standalone PG function
-- Lifted nested validate_cd as standalone PG function

DROP FUNCTION IF EXISTS f_get_total_score(numeric, numeric);
CREATE OR REPLACE FUNCTION F_GET_TOTAL_SCORE(i_nSTUDENT_NO NUMERIC, i_nEXAM_TYPE NUMERIC DEFAULT NULL)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证学生编号

    -- 嵌套函数: 构建SQL

    g_nERR          NUMERIC := -1;
    g_nRETURN_CODE  NUMERIC := 0;
    g_vSQL          VARCHAR(2000);
BEGIN
    IF NOT VALIDATE_STUDENT_NO(i_nSTUDENT_NO) THEN
        RETURN 0;
    END IF;

    g_vSQL := BUILD_SQL(i_nSTUDENT_NO, i_nEXAM_TYPE);

    IF g_vSQL IS NOT NULL THEN
        EXECUTE g_vSQL INTO g_nRETURN_CODE;
    END IF;

    RETURN g_nRETURN_CODE;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 2. 基础函数: 获取平均成绩 (嵌套加权计算)
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS build_sql(numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_SQL(i_nSTU NUMERIC, i_nEXM NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_SQL VARCHAR(2000) := 'SELECT COALESCE(SUM(SCORE), 0) FROM SCORE_REC WHERE 1=1 ';

        -- 再嵌套: 添加学生条件

        -- 再嵌套: 添加考试类型条件
BEGIN
        v_SQL := ADD_STUDENT_COND(v_SQL, i_nSTU);
        v_SQL := ADD_EXAM_COND(v_SQL, i_nEXM);
        RETURN v_SQL;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS add_student_cond(varchar, numeric);
CREATE OR REPLACE FUNCTION ADD_STUDENT_COND(i_vSQL VARCHAR, i_nNO NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nNO IS NOT NULL AND i_nNO > 0 THEN
                RETURN i_vSQL || ' AND STUDENT_NO = ' || i_nNO;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS add_exam_cond(varchar, numeric);
CREATE OR REPLACE FUNCTION ADD_EXAM_COND(i_vSQL VARCHAR, i_nTYP NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nTYP IS NOT NULL THEN
                RETURN i_vSQL || ' AND EXAM_TYPE = ' || i_nTYP;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS validate_student_no(numeric);
CREATE OR REPLACE FUNCTION VALIDATE_STUDENT_NO(i_nNO NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 检查格式

        v_CNT NUMERIC;
BEGIN
        IF NOT CHECK_FORMAT(i_nNO) THEN
            RETURN FALSE;
        END IF;
        SELECT COUNT(*) INTO v_CNT FROM STUDENT_MST
        WHERE STUDENT_NO = i_nNO AND DELETE_FLG = 0;
        RETURN v_CNT > 0;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS check_format(numeric);
CREATE OR REPLACE FUNCTION CHECK_FORMAT(i_nNO NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN i_nNO IS NOT NULL AND i_nNO > 0 AND i_nNO < 10000000000;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


DROP FUNCTION IF EXISTS f_get_avg_score(numeric, numeric);
CREATE OR REPLACE FUNCTION F_GET_AVG_SCORE(i_nSTUDENT_NO NUMERIC, i_nSUBJECT_CD NUMERIC DEFAULT NULL)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 获取学分信息

    -- 嵌套函数: 计算加权平均

    -- 嵌套函数: 构建SQL

    g_nERR         NUMERIC := -1;
    g_nRETURN_CODE NUMERIC := 0;
    g_vSQL         VARCHAR(2000);
BEGIN
    IF i_nSUBJECT_CD IS NOT NULL THEN
        g_vSQL := BUILD_SQL(i_nSTUDENT_NO, i_nSUBJECT_CD);
        IF g_vSQL IS NOT NULL THEN
            EXECUTE g_vSQL INTO g_nRETURN_CODE;
        END IF;
    ELSE
        g_nRETURN_CODE := CALC_WEIGHTED_AVG(i_nSTUDENT_NO);
    END IF;

    RETURN g_nRETURN_CODE;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 3. 基础函数: 计算等级 (多层嵌套阈值)
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS calc_weighted_avg(numeric);
CREATE OR REPLACE FUNCTION CALC_WEIGHTED_AVG(i_nSTU NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_WEIGHTED_SUM NUMERIC := 0;
        v_TOTAL_WEIGHT NUMERIC := 0;
        v_AVG NUMERIC := 0;

        -- 再嵌套: 处理单科成绩
        rec record;
BEGIN
        FOR rec IN (SELECT SC.SCORE, SB.CREDIT
            FROM SCORE_REC SC, SUBJECT_MST SB
            WHERE SC.STUDENT_NO = i_nSTU
            AND SC.SUBJECT_CD = SB.SUBJECT_CD
            AND SB.DELETE_FLG = 0
        ) LOOP
            v_WEIGHTED_SUM := v_WEIGHTED_SUM + PROCESS_SCORE(rec.SCORE, rec.CREDIT);
            v_TOTAL_WEIGHT := v_TOTAL_WEIGHT + COALESCE(rec.CREDIT, 1);
        END LOOP;

        IF v_TOTAL_WEIGHT > 0 THEN
            v_AVG := ROUND(v_WEIGHTED_SUM / v_TOTAL_WEIGHT::NUMERIC, 2);
        END IF;
        RETURN v_AVG;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS process_score(numeric, numeric);
CREATE OR REPLACE FUNCTION PROCESS_SCORE(i_nSCORE NUMERIC, i_nCREDIT NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN i_nSCORE * i_nCREDIT;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS build_sql(numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_SQL(i_nSTU NUMERIC, i_nSUB NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_SQL VARCHAR(2000) := 'SELECT ROUND(COALESCE(AVG(SCORE), 0)::NUMERIC, 2) FROM SCORE_REC WHERE 1=1 ';
BEGIN
        RETURN ADD_COND(v_SQL, i_nSTU, i_nSUB);
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS add_cond(varchar, numeric, numeric);
CREATE OR REPLACE FUNCTION ADD_COND(i_vSQL VARCHAR, i_nSTU NUMERIC, i_nSUB NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nSTU IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND STUDENT_NO = ' || i_nSTU;
            END IF;
            IF i_nSUB IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND SUBJECT_CD = ' || i_nSUB;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS get_credit_info(numeric, numeric);
CREATE OR REPLACE FUNCTION GET_CREDIT_INFO(i_nSUB NUMERIC, INOUT o_nCREDIT NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 获取学分
BEGIN
        o_nCREDIT := FETCH_CREDIT(i_nSUB);
        RETURN;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS fetch_credit(numeric);
CREATE OR REPLACE FUNCTION FETCH_CREDIT(i_nCD NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_CREDIT NUMERIC;
BEGIN
            SELECT COALESCE(CREDIT, 0) INTO v_CREDIT
            FROM SUBJECT_MST
            WHERE SUBJECT_CD = i_nCD AND DELETE_FLG = 0;
            RETURN v_CREDIT;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


DROP FUNCTION IF EXISTS f_get_grade_level(numeric);
CREATE OR REPLACE FUNCTION F_GET_GRADE_LEVEL(i_nSCORE NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 获取分数线

    -- 嵌套函数: 比较分数

    -- 嵌套函数: 判定等级

    v_EXCELLENT NUMERIC;
    v_GOOD      NUMERIC;
    v_PASS      NUMERIC;
BEGIN
    v_EXCELLENT := GET_THRESHOLD('EXCELLENT');
    v_GOOD := GET_THRESHOLD('GOOD');
    v_PASS := GET_THRESHOLD('PASS');

    RETURN DETERMINE_LEVEL(i_nSCORE, v_EXCELLENT, v_GOOD, v_PASS);
EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 4. 基础函数: 计算排名 (嵌套SQL构建)
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS compare_score(numeric, numeric);
CREATE OR REPLACE FUNCTION COMPARE_SCORE(i_nSCORE NUMERIC, i_nTHRESHOLD NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
        RETURN i_nSCORE >= i_nTHRESHOLD;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS determine_level(numeric, numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION DETERMINE_LEVEL(i_nSCORE NUMERIC, i_nEXC NUMERIC, i_nGOOD NUMERIC, i_nPASS NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
        IF COMPARE_SCORE(i_nSCORE, i_nEXC) THEN RETURN 1;
        ELSIF COMPARE_SCORE(i_nSCORE, i_nGOOD) THEN RETURN 2;
        ELSIF COMPARE_SCORE(i_nSCORE, i_nPASS) THEN RETURN 3;
        ELSE RETURN 4;
        END IF;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS get_threshold(varchar);
CREATE OR REPLACE FUNCTION GET_THRESHOLD(i_sKBN VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 从配置表获取

        -- 再嵌套: 获取默认值
BEGIN
        RETURN FETCH_FROM_CONFIG(i_sKBN);
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS fetch_from_config(varchar);
CREATE OR REPLACE FUNCTION FETCH_FROM_CONFIG(i_sCD VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再再嵌套: 执行查询
BEGIN
            RETURN DO_QUERY(
                'SELECT COALESCE((CONFIG_VALUE)::NUMERIC, 0) FROM SYSTEM_CONFIG ' ||
                'WHERE CONFIG_CD = ''GRADE_LEVEL_' || i_sKBN || ''' AND DELETE_FLG = 0'
            );
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS do_query(varchar);
CREATE OR REPLACE FUNCTION DO_QUERY(i_sSQL VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_VALUE NUMERIC;
BEGIN
                EXECUTE i_sSQL INTO v_VALUE;
                RETURN v_VALUE;
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;

DROP FUNCTION IF EXISTS get_default(varchar);
CREATE OR REPLACE FUNCTION GET_DEFAULT(i_sKBN VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_sKBN = 'EXCELLENT' THEN RETURN 90;
            ELSIF i_sKBN = 'GOOD' THEN RETURN 80;
            ELSIF i_sKBN = 'PASS' THEN RETURN 60;
            ELSE RETURN 60;
            END IF;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


DROP FUNCTION IF EXISTS f_calc_rank(numeric, numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION F_CALC_RANK(i_nSTUDENT_NO NUMERIC, i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC, i_nRANK_TYPE NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 获取学生总分

    -- 嵌套函数: 构建子查询

    -- 嵌套函数: 构建完整SQL

    g_nERR         NUMERIC := -1;
    g_nRANK        NUMERIC := 1;
    g_nTOTAL_SCORE NUMERIC;
    g_vSQL         VARCHAR(4000);
BEGIN
    g_nTOTAL_SCORE := GET_STUDENT_TOTAL(i_nSTUDENT_NO);
    g_vSQL := BUILD_FULL_SQL(i_nSTUDENT_NO, i_nGRADE, i_nCLASS_NO, i_nRANK_TYPE, g_nTOTAL_SCORE);

    IF g_vSQL IS NOT NULL THEN
        EXECUTE g_vSQL INTO g_nRANK;
    END IF;

    RETURN g_nRANK;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 5. PIPELINED函数: 返回学生列表 (复杂嵌套)
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS build_subquery(numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_SUBQUERY(i_nGRD NUMERIC, i_nCLS NUMERIC, i_nTYP NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 获取分组条件

        v_RET VARCHAR(4000);
BEGIN
        v_RET := 'SELECT S.STUDENT_NO, SUM(COALESCE(SC.SCORE, 0)) AS TOTAL ' ||
                 'FROM STUDENT_MST S, SCORE_REC SC ' ||
                 'WHERE S.STUDENT_NO = SC.STUDENT_NO%OUTERJOIN0% ' ||
                 'AND S.GRADE = ' || i_nGRD || ' ' ||
                 'AND ' || GET_GROUP_COND(i_nTYP, i_nCLS) || ' ' ||
                 'AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO ';
        RETURN v_RET;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS get_group_cond(numeric, numeric);
CREATE OR REPLACE FUNCTION GET_GROUP_COND(i_nTYP NUMERIC, i_nCLS NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nTYP = 1 THEN
                RETURN 'CLASS_NO = ' || i_nCLS;
            ELSE
                RETURN '1=1';
            END IF;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS build_full_sql(numeric, numeric, numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_FULL_SQL(i_nSTU NUMERIC, i_nGRD NUMERIC, i_nCLS NUMERIC, i_nTYP NUMERIC, i_nTOT NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_SUB VARCHAR(4000);
BEGIN
        v_SUB := BUILD_SUBQUERY(i_nGRD, i_nCLS, i_nTYP);
        IF v_SUB IS NOT NULL THEN
            RETURN 'SELECT COUNT(*) + 1 FROM (' || v_SUB ||
                   'HAVING SUM(COALESCE(SC.SCORE, 0)) > ' || i_nTOT || ') ';
        END IF;
        RETURN NULL;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS get_student_total(numeric);
CREATE OR REPLACE FUNCTION GET_STUDENT_TOTAL(i_nSTU NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
        RETURN F_GET_TOTAL_SCORE(i_nSTU, NULL);
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


DROP FUNCTION IF EXISTS f_get_student_list_pipeline(numeric, numeric);
CREATE OR REPLACE FUNCTION F_GET_STUDENT_LIST_PIPELINE(i_nGRADE NUMERIC DEFAULT NULL, i_nCLASS_NO NUMERIC DEFAULT NULL)
RETURNS SETOF STUDENT_RECORD
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套子函数: 获取学生信息

    -- 嵌套函数: 验证过滤条件

    -- 嵌套函数: 构建查询

    v_cursor REFCURSOR;
    v_student STUDENT_MST;
    v_total NUMERIC;
    v_avg NUMERIC;
    v_rank NUMERIC;
BEGIN
    IF NOT VALIDATE_FILTER(i_nGRADE, i_nCLASS_NO) THEN
    END IF;

    OPEN v_cursor FOR EXECUTE BUILD_QUERY(i_nGRADE, i_nCLASS_NO);

    LOOP
        FETCH v_cursor INTO v_student;
        EXIT WHEN NOT FOUND;

        v_total := F_GET_TOTAL_SCORE(v_student.STUDENT_NO, NULL);
        v_avg := F_GET_AVG_SCORE(v_student.STUDENT_NO, NULL);
        v_rank := F_CALC_RANK(v_student.STUDENT_NO, v_student.GRADE, v_student.CLASS_NO, 1);

        RETURN NEXT (ROW(v_student.STUDENT_NO,
            v_student.STUDENT_NAME,
            v_student.STUDENT_KANA,
            v_student.GENDER,
            v_student.GRADE,
            v_student.CLASS_NO,
            v_student.BIRTH_DATE,
            v_total,
            v_avg,
            v_rank))::STUDENT_RECORD;
    END LOOP;

    CLOSE v_cursor;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 6. PIPELINED函数: 返回成绩列表
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS validate_filter(numeric, numeric);
CREATE OR REPLACE FUNCTION VALIDATE_FILTER(i_nGRD NUMERIC, i_nCLS NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
        RETURN(i_nGRD IS NULL OR i_nGRD > 0)
           AND (i_nCLS IS NULL OR i_nCLS > 0);
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS build_query(numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_QUERY(i_nGRD NUMERIC, i_nCLS NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_SQL VARCHAR(4000) := 'SELECT * FROM STUDENT_MST WHERE DELETE_FLG = 0 AND STATUS = 0 ';
BEGIN
        v_SQL := ADD_GRADE_COND(v_SQL, i_nGRD);
        v_SQL := ADD_CLASS_COND(v_SQL, i_nCLS);
        RETURN v_SQL || 'ORDER BY STUDENT_NO';
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS add_grade_cond(varchar, numeric);
CREATE OR REPLACE FUNCTION ADD_GRADE_COND(i_vSQL VARCHAR, i_nGRD NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nGRD IS NOT NULL THEN
                RETURN i_vSQL || 'AND GRADE = ' || i_nGRD || ' ';
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS add_class_cond(varchar, numeric);
CREATE OR REPLACE FUNCTION ADD_CLASS_COND(i_vSQL VARCHAR, i_nCLS NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nCLS IS NOT NULL THEN
                RETURN i_vSQL || 'AND CLASS_NO = ' || i_nCLS || ' ';
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS get_student_info(numeric);
CREATE OR REPLACE FUNCTION GET_STUDENT_INFO(p_STUDENT_NO NUMERIC)
RETURNS STUDENT_RECORD
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 计算总分

        -- 再嵌套: 计算平均分

        -- 再嵌套: 计算排名

        vTOTAL NUMERIC;
        vAVG   NUMERIC;
        vRANK  NUMERIC;
BEGIN
        RETURN NULL;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS calc_total(numeric);
CREATE OR REPLACE FUNCTION CALC_TOTAL(p_NO NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN F_GET_TOTAL_SCORE(p_NO, NULL);
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS calc_avg(numeric);
CREATE OR REPLACE FUNCTION CALC_AVG(p_NO NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN F_GET_AVG_SCORE(p_NO, NULL);
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS calc_rank(numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION CALC_RANK(p_NO NUMERIC, p_GRD NUMERIC, p_CLS NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN F_CALC_RANK(p_NO, p_GRD, p_CLS, 1);
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


DROP FUNCTION IF EXISTS f_get_score_list_pipeline(numeric, numeric);
CREATE OR REPLACE FUNCTION F_GET_SCORE_LIST_PIPELINE(i_nSTUDENT_NO NUMERIC, i_nEXAM_TYPE NUMERIC DEFAULT NULL)
RETURNS SETOF SCORE_RECORD
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证学生

    -- 嵌套函数: 获取科目名称

    -- 嵌套函数: 格式化内容

    -- 嵌套函数: 构建查询

    v_cursor REFCURSOR;
    v_cnt NUMERIC := 0;
    v_subject_name VARCHAR(100);
BEGIN
    IF NOT VALIDATE_STUDENT(i_nSTUDENT_NO) THEN
    END IF;

    OPEN v_cursor FOR EXECUTE BUILD_QUERY(i_nSTUDENT_NO, i_nEXAM_TYPE);

    LOOP
        FETCH v_cursor INTO v_cnt;
        EXIT WHEN NOT FOUND;

        v_subject_name := GET_SUBJECT_NAME(v_cnt);

        RETURN NEXT (ROW(v_cnt,
            FORMAT_CONTENT(v_subject_name, v_cnt),
            CASE WHEN v_cnt > 0 THEN 0 ELSE 2 END,
            i_nSTUDENT_NO,
            v_cnt,
            v_cnt,
            v_cnt,
            CASE WHEN v_cnt > 0 THEN 1 ELSE 0 END))::SCORE_RECORD;
    END LOOP;

    CLOSE v_cursor;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 7. 多行返回函数: 使用REF CURSOR
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS get_subject_name(numeric);
CREATE OR REPLACE FUNCTION GET_SUBJECT_NAME(i_nCD NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_NAME VARCHAR(100);
BEGIN
        SELECT SUBJECT_NAME INTO v_NAME FROM SUBJECT_MST
        WHERE SUBJECT_CD = i_nCD AND DELETE_FLG = 0;
        RETURN v_NAME;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS format_content(varchar, numeric);
CREATE OR REPLACE FUNCTION FORMAT_CONTENT(i_vNAME VARCHAR, i_nSCORE NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
        RETURN i_vNAME || ':' || COALESCE((i_nSCORE)::varchar, 'N/A');
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS build_query(numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_QUERY(i_nSTU NUMERIC, i_nTYP NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_SQL VARCHAR(4000) :=
            'SELECT SC.*, SB.SUBJECT_NAME, SB.PASS_SCORE ' ||
            'FROM SCORE_REC SC, SUBJECT_MST SB ' ||
            'WHERE SC.SUBJECT_CD = SB.SUBJECT_CD ' ||
            'AND SC.STUDENT_NO = ' || i_nSTU;
BEGIN
        v_SQL := ADD_TYPE_COND(v_SQL, i_nTYP);
        RETURN v_SQL || ' ORDER BY SC.EXAM_DATE DESC';
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS add_type_cond(varchar, numeric);
CREATE OR REPLACE FUNCTION ADD_TYPE_COND(i_vSQL VARCHAR, i_nTYP NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nTYP IS NOT NULL THEN
                RETURN i_vSQL || ' AND SC.EXAM_TYPE = ' || i_nTYP;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS validate_student(numeric);
CREATE OR REPLACE FUNCTION VALIDATE_STUDENT(i_nNO NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_CNT NUMERIC;
BEGIN
        SELECT COUNT(*) INTO v_CNT FROM STUDENT_MST
        WHERE STUDENT_NO = i_nNO AND DELETE_FLG = 0;
        RETURN v_CNT > 0;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


DROP FUNCTION IF EXISTS f_get_student_cursor(numeric, numeric);
CREATE OR REPLACE FUNCTION F_GET_STUDENT_CURSOR(i_nGRADE NUMERIC DEFAULT NULL, i_nCLASS_NO NUMERIC DEFAULT NULL)
RETURNS REFCURSOR
LANGUAGE plpgsql
AS $$
DECLARE
-- 嵌套函数: 构建SQL

    v_cursor REFCURSOR;
    v_sql    VARCHAR(4000);
BEGIN
    v_sql := BUILD_SQL(i_nGRADE, i_nCLASS_NO);

    IF v_sql IS NOT NULL THEN
        OPEN v_cursor FOR EXECUTE v_sql;
    ELSE
        OPEN v_cursor FOR EXECUTE 'SELECT * FROM STUDENT_MST WHERE 1=0';
    END IF;

    RETURN v_cursor;
END;
-- ============================================================================
-- 8. 聚合函数: 计算加权平均
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS build_sql(numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_SQL(i_nGRD NUMERIC, i_nCLS NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_SQL VARCHAR(4000) :=
            'SELECT S.*, ' ||
            '(SELECT SUM(NVL(SC.SCORE, 0)) FROM SCORE_REC SC WHERE SC.STUDENT_NO = S.STUDENT_NO) AS TOTAL_SCORE ' ||
            'FROM STUDENT_MST S WHERE 1=1 AND S.DELETE_FLG = 0 ';
BEGIN
        RETURN ADD_COND(v_SQL, i_nGRD, i_nCLS) || ' ORDER BY S.STUDENT_NO';
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS add_cond(varchar, numeric, numeric);
CREATE OR REPLACE FUNCTION ADD_COND(i_vSQL VARCHAR, i_nGRD NUMERIC, i_nCLS NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            IF i_nGRD IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND S.GRADE = ' || i_nGRD;
            END IF;
            IF i_nCLS IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND S.CLASS_NO = ' || i_nCLS;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


DROP FUNCTION IF EXISTS f_get_weighted_avg(numeric);
CREATE OR REPLACE FUNCTION F_GET_WEIGHTED_AVG(i_nSTUDENT_NO NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 获取所有成绩

    -- 嵌套函数: 计算加权和

    -- 嵌套函数: 计算总权重

    v_WEIGHTED_SUM NUMERIC;
    v_TOTAL_WEIGHT NUMERIC;
    v_AVG NUMERIC := 0;
    v_cursor REFCURSOR;
BEGIN
    v_cursor := GET_ALL_SCORES(i_nSTUDENT_NO);
    v_WEIGHTED_SUM := CALC_WEIGHTED_SUM(v_cursor);
    CLOSE v_cursor;

    v_cursor := GET_ALL_SCORES(i_nSTUDENT_NO);
    v_TOTAL_WEIGHT := CALC_TOTAL_WEIGHT(v_cursor);
    CLOSE v_cursor;

    IF v_TOTAL_WEIGHT > 0 THEN
        v_AVG := ROUND(v_WEIGHTED_SUM / v_TOTAL_WEIGHT::NUMERIC, 2);
    END IF;

    RETURN v_AVG;
EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 9. 配置获取函数 (嵌套配置处理)
-- ============================================================================
$$;

CREATE OR REPLACE FUNCTION CALC_WEIGHTED_SUM(i_cursor INOUT REFCURSOR)
RETURNS REFCURSOR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_WEIGHTED_SUM NUMERIC := 0;
        v_SCORE NUMERIC;
        v_CREDIT NUMERIC;
BEGIN
        LOOP
            FETCH i_cursor INTO v_SCORE, v_CREDIT;
            EXIT WHEN NOT FOUND;
            v_WEIGHTED_SUM := v_WEIGHTED_SUM + (COALESCE(v_SCORE, 0) * COALESCE(v_CREDIT, 1));
        END LOOP;
        RETURN;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

CREATE OR REPLACE FUNCTION CALC_TOTAL_WEIGHT(i_cursor INOUT REFCURSOR)
RETURNS REFCURSOR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_TOTAL_WEIGHT NUMERIC := 0;
        v_CREDIT NUMERIC;
BEGIN
        LOOP
            FETCH i_cursor INTO v_CREDIT;
            EXIT WHEN NOT FOUND;
            v_TOTAL_WEIGHT := v_TOTAL_WEIGHT + COALESCE(v_CREDIT, 1);
        END LOOP;
        RETURN;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS get_all_scores(numeric);
CREATE OR REPLACE FUNCTION GET_ALL_SCORES(i_nSTU NUMERIC)
RETURNS REFCURSOR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_cursor REFCURSOR;
BEGIN
        OPEN v_cursor FOR EXECUTE 'SELECT SC.SCORE, SB.CREDIT ' ||
            'FROM SCORE_REC SC, SUBJECT_MST SB ' ||
            'WHERE SC.STUDENT_NO = ' || i_nSTU ||
            ' AND SC.SUBJECT_CD = SB.SUBJECT_CD ' ||
            ' AND SB.DELETE_FLG = 0';
        RETURN v_cursor;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


DROP FUNCTION IF EXISTS f_get_config_value(varchar);
CREATE OR REPLACE FUNCTION F_GET_CONFIG_VALUE(i_sCONFIG_CD VARCHAR)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证配置代码

    -- 嵌套函数: 清理配置值

    v_VALUE VARCHAR(1000);
BEGIN
    IF NOT VALIDATE_CD(i_sCONFIG_CD) THEN
        RETURN NULL;
    END IF;

    SELECT CONFIG_VALUE INTO v_VALUE
    FROM SYSTEM_CONFIG
    WHERE CONFIG_CD = i_sCONFIG_CD AND DELETE_FLG = 0;

    RETURN CLEAN_VALUE(v_VALUE);

EXCEPTION
    WHEN no_data_found THEN
        GET STACKED DIAGNOSTICS _p25_sqlstate = RETURNED_SQLSTATE,
                            _p25_msg = MESSAGE_TEXT;
RETURN NULL;
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 10. 批次号生成函数
-- ============================================================================
$$;

DROP FUNCTION IF EXISTS clean_value(varchar);
CREATE OR REPLACE FUNCTION CLEAN_VALUE(i_vRAW VARCHAR)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 去除空格
BEGIN
        RETURN TRIM_VALUE(i_vRAW);
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS trim_value(varchar);
CREATE OR REPLACE FUNCTION TRIM_VALUE(i_vIN VARCHAR)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN TRIM(i_vIN);
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS validate_cd(varchar);
CREATE OR REPLACE FUNCTION VALIDATE_CD(i_sCD VARCHAR)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_LEN NUMERIC;
BEGIN
        v_LEN := LENGTH(i_sCD);
        RETURN v_LEN IS NOT NULL AND v_LEN > 0 AND v_LEN < 100;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


CREATE OR REPLACE FUNCTION F_GET_JOBNUM()
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_JOBNUM NUMERIC;
BEGIN
    SELECT nextval('SEQ_JOBNUM') INTO v_JOBNUM;
    RETURN v_JOBNUM;
EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

$$;

