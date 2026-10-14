-- Perl-style Ora2Py Warnings:
-- Extracted 27 functions/procedures from PACKAGE BODY PKG_SCORE_MANAGE
-- Lifted nested format_message as standalone PG function
-- Lifted nested INIT_WSINFO as standalone PG function
-- Lifted nested init_jobnum as standalone PG function
-- Lifted nested CONVERT_VALUE as standalone PG function
-- Lifted nested validate_config_cd as standalone PG function
-- Lifted nested validate_score_value as standalone PG function
-- Lifted nested CONVERT_FORMAT as standalone PG function
-- Lifted nested validate_ymd as standalone PG function
-- Lifted nested COMPARE_SCORE as standalone PG function
-- Lifted nested get_threshold as standalone PG function
-- Lifted nested BUILD_WHERE_CLAUSE as standalone PG function
-- Lifted nested validate_student as standalone PG function
-- Lifted nested CALC_WEIGHTED_AVG as standalone PG function
-- Lifted nested get_subject_info as standalone PG function
-- Lifted nested BUILD_RANK_SQL as standalone PG function
-- Lifted nested get_total_score as standalone PG function
-- Lifted nested GENERATE_ROWNO as standalone PG function
-- Lifted nested validate_data as standalone PG function
-- Nested FUNCTION CALC_PASS_FAIL has no matching END; skipping lift
-- Lifted nested calc_stats as standalone PG function
-- Nested FUNCTION PROCESS_SINGLE has no matching END; skipping lift
-- Lifted nested build_rank_query as standalone PG function
-- Lifted nested WRITE_HEADER as standalone PG function
-- Lifted nested format_csv_line as standalone PG function

CREATE SCHEMA IF NOT EXISTS PKG_SCORE_MANAGE;

-- Package Body PKG_SCORE_MANAGE -> SCHEMA + 27 functions

-- Global variables registered as set_config:
--   g_nJOBNUM (NUMBER) := set_config('PKG_SCORE_MANAGE.g_nJOBNUM', <val>, false)
--   g_sWSNUM (NVARCHAR2(63)) := set_config('PKG_SCORE_MANAGE.g_sWSNUM', <val>, false)
--   g_sTANTO_CD (CHAR(12)) := set_config('PKG_SCORE_MANAGE.g_sTANTO_CD', <val>, false)
--   g_sSTARTDATE (NVARCHAR2(23)) := set_config('PKG_SCORE_MANAGE.g_sSTARTDATE', <val>, false)
--   g_sMESSAGE (NVARCHAR2(4000)) := set_config('PKG_SCORE_MANAGE.g_sMESSAGE', <val>, false)
--   g_nPASS_SCORE (NUMBER) := set_config('PKG_SCORE_MANAGE.g_nPASS_SCORE', <val>, false)
--   g_nEXCELLENT_SCORE (NUMBER) := set_config('PKG_SCORE_MANAGE.g_nEXCELLENT_SCORE', <val>, false)
--   g_nGOOD_SCORE (NUMBER) := set_config('PKG_SCORE_MANAGE.g_nGOOD_SCORE', <val>, false)
--   g_nMEDIUM_SCORE (NUMBER) := set_config('PKG_SCORE_MANAGE.g_nMEDIUM_SCORE', <val>, false)

CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_SETLOG(i_sSTEP_NANE VARCHAR, i_nDEBUG_KBN NUMERIC, i_nSTATUSID NUMERIC, i_sSQLCODE VARCHAR, i_sSQLERRM VARCHAR, i_sERRMSG VARCHAR)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- Unsupported: AUTONOMOUS_TRANSACTION not natively supported in PostgreSQL
-- Use dblink or pg_background extension for autonomous transaction behavior
    v_LOG_ID NUMERIC;

        -- 嵌套函数: 格式化日志消息
BEGIN
        SELECT nextval('SEQ_LOG_ID') INTO v_LOG_ID;
        INSERT INTO PROCESS_LOG(
            LOG_ID, JOBNUM, PROCESS_NAME, STEP_NO, LOG_LEVEL,
            LOG_MESSAGE, SQLCODE, SQLERRM, TANTO_CD, WS_NUM, LOG_DATE
        ) VALUES (
            v_LOG_ID, current_setting('PKG_SCORE_MANAGE.g_nJOBNUM'), 'PKG_SCORE_MANAGE', 0,
            i_nSTATUSID, i_sERRMSG, i_sSQLCODE, i_sSQLERRM,
            current_setting('PKG_SCORE_MANAGE.g_sTANTO_CD'), current_setting('PKG_SCORE_MANAGE.g_sWSNUM'), TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS')
        );
        -- COMMIT removed (PG 15 CALL manages txn)
        RAISE NOTICE '%', FORMAT_MESSAGE(i_sSTEP_NANE, i_sERRMSG, i_nSTATUSID);
        RETURN TRUE;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS format_message(varchar, varchar, numeric);
CREATE OR REPLACE FUNCTION FORMAT_MESSAGE(i_sSTEP VARCHAR, i_sMSG VARCHAR, i_nSTAT NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
v_RESULT VARCHAR(4000);
            v_PREFIX VARCHAR(20);
BEGIN
            -- 根据状态选择前缀
            IF i_nSTAT = 1 THEN
                v_PREFIX := '[ERROR] ';
            ELSIF i_nSTAT = 2 THEN
                v_PREFIX := '[WARN] ';
            ELSE
                v_PREFIX := '[INFO] ';
            END IF;
            v_RESULT := v_PREFIX || i_sSTEP || ': ' || i_sMSG;
            RETURN v_RESULT;
        END;
$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_INIT(i_sWSNUM VARCHAR, i_sTANTO_CD CHAR)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 初始化批次号

        -- 嵌套函数: 初始化工作站信息
BEGIN
    PERFORM set_config('PKG_SCORE_MANAGE.g_sSTARTDATE', TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'), false);
        PERFORM set_config('PKG_SCORE_MANAGE.g_nJOBNUM', INIT_JOBNUM, false);
        RETURN INIT_WSINFO(i_sWSNUM, i_sTANTO_CD);
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS init_wsinfo(varchar, char);
CREATE OR REPLACE FUNCTION INIT_WSINFO(i_sWS VARCHAR, i_sTC CHAR)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN

    PERFORM set_config('PKG_SCORE_MANAGE.g_sWSNUM', i_sWS, false);
            PERFORM set_config('PKG_SCORE_MANAGE.g_sTANTO_CD', i_sTC, false);
            RETURN TRUE;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

CREATE OR REPLACE FUNCTION INIT_JOBNUM()
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    v_MAX_JOBNUM NUMERIC;
            v_NEW_JOBNUM NUMERIC;
BEGIN
                -- L3 stub: GEN_NEW_JOBNUM not available
    RETURN 0;
EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_GET_CONFIG(i_sCONFIG_CD VARCHAR, INOUT o_nVALUE NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证配置代码

        -- 嵌套函数: 转换配置值类型

        v_VALUE VARCHAR(1000);
        v_IS_VALID BOOLEAN;
BEGIN
        -- 验证配置代码
        v_IS_VALID := VALIDATE_CONFIG_CD(i_sCONFIG_CD);
        IF NOT v_IS_VALID THEN
            o_nVALUE := 0;
            RETURN;
        END IF;

        SELECT CONFIG_VALUE INTO v_VALUE FROM SYSTEM_CONFIG
        WHERE CONFIG_CD = i_sCONFIG_CD AND DELETE_FLG = 0;

        o_nVALUE := CONVERT_VALUE(v_VALUE);
        RETURN;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS convert_value(varchar);
CREATE OR REPLACE FUNCTION CONVERT_VALUE(i_vRAW VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 清理数值格式
BEGIN
            RETURN (CLEAN_NUMERIC(i_vRAW))::NUMERIC;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS clean_numeric(varchar);
CREATE OR REPLACE FUNCTION CLEAN_NUMERIC(i_vIN VARCHAR)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_OUT VARCHAR(100);
BEGIN
                v_OUT := REPLACE(REPLACE(i_vIN, ' ', ''), ',', '');
                RETURN v_OUT;
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;

DROP FUNCTION IF EXISTS validate_config_cd(varchar);
CREATE OR REPLACE FUNCTION VALIDATE_CONFIG_CD(i_sCD VARCHAR)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    v_CNT NUMERIC;
BEGIN
            SELECT COUNT(*) INTO v_CNT FROM SYSTEM_CONFIG
            WHERE CONFIG_CD = i_sCD;
            RETURN v_CNT > 0;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PROC_LOAD_CONFIG()
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证配置值

        nRTN NUMERIC;
        nTMP NUMERIC;
BEGIN
        nRTN := FUNC_GET_CONFIG('PASS_SCORE_DEFAULT', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 60, 'PASS_SCORE') THEN PERFORM set_config('PKG_SCORE_MANAGE.g_nPASS_SCORE', nTMP, false);
        END IF;

        nRTN := FUNC_GET_CONFIG('GRADE_LEVEL_EXCELLENT', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 90, 'EXCELLENT') THEN PERFORM set_config('PKG_SCORE_MANAGE.g_nEXCELLENT_SCORE', nTMP, false);
        END IF;

        nRTN := FUNC_GET_CONFIG('GRADE_LEVEL_GOOD', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 80, 'GOOD') THEN PERFORM set_config('PKG_SCORE_MANAGE.g_nGOOD_SCORE', nTMP, false);
        END IF;

        nRTN := FUNC_GET_CONFIG('GRADE_LEVEL_PASS', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 70, 'MEDIUM') THEN PERFORM set_config('PKG_SCORE_MANAGE.g_nMEDIUM_SCORE', nTMP, false);
        END IF;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS validate_score_value(numeric, numeric, varchar);
CREATE OR REPLACE FUNCTION VALIDATE_SCORE_VALUE(i_nVALUE INOUT NUMERIC, i_nDEFAULT NUMERIC, i_sNAME VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 检查数值范围
BEGIN
            IF i_nVALUE IS NULL OR i_nVALUE <= 0 THEN
                i_nVALUE := i_nDEFAULT;
            END IF;
            IF NOT CHECK_RANGE(i_nVALUE, 0, 100) THEN
                i_nVALUE := i_nDEFAULT;
            END IF;
            RETURN;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS check_range(numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION CHECK_RANGE(i_nVAL NUMERIC, i_nMIN NUMERIC, i_nMAX NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
                RETURN i_nVAL >= i_nMIN AND i_nVAL <= i_nMAX;
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_FORMAT_DATE(i_YMD NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证日期格式

        -- 嵌套函数: 转换日期格式
BEGIN
        IF i_YMD IS NOT NULL AND i_YMD > 0 AND VALIDATE_YMD(i_YMD) THEN
            RETURN CONVERT_FORMAT(i_YMD, 'YYYY/MM/DD');
        END IF;
        RETURN ' ';
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS convert_format(numeric, varchar);
CREATE OR REPLACE FUNCTION CONVERT_FORMAT(i_nYMD NUMERIC, i_sFMT VARCHAR)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 解析日期组件

            v_Y NUMERIC; v_M NUMERIC; v_D NUMERIC;
BEGIN
            IF PARSE_YMD(i_nYMD, v_Y, v_M, v_D) THEN
                RETURN TO_CHAR(TO_TIMESTAMP(i_nYMD, 'YYYYMMDD')::date, 'i_sFMT');
            END IF;
            RETURN ' ';
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP PROCEDURE IF EXISTS parse_ymd(numeric, numeric, numeric, numeric);
DROP PROCEDURE IF EXISTS PARSE_YMD;
CREATE OR REPLACE PROCEDURE PARSE_YMD(i_nYMD NUMERIC, INOUT o_nY NUMERIC, INOUT o_nM NUMERIC, INOUT o_nD NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
                o_nY := (SUBSTRING((i_nYMD)::varchar FROM 1 FOR 4))::NUMERIC;
                o_nM := (SUBSTRING((i_nYMD)::varchar FROM 5 FOR 2))::NUMERIC;
                o_nD := (SUBSTRING((i_nYMD)::varchar FROM 7 FOR 2))::NUMERIC;
                RETURN;
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;

DROP FUNCTION IF EXISTS validate_ymd(numeric);
CREATE OR REPLACE FUNCTION VALIDATE_YMD(i_nYMD NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_Y VARCHAR(4);
            v_M VARCHAR(2);
            v_D VARCHAR(2);
BEGIN
            IF i_nYMD IS NULL OR i_nYMD < 19000101 OR i_nYMD > 21001231 THEN
                RETURN FALSE;
            END IF;
            v_Y := SUBSTRING((i_nYMD)::varchar FROM 1 FOR 4);
            v_M := SUBSTRING((i_nYMD)::varchar FROM 5 FOR 2);
            v_D := SUBSTRING((i_nYMD)::varchar FROM 7 FOR 2);
            RETURN (v_M)::NUMERIC BETWEEN 1 AND 12 AND (v_D)::NUMERIC BETWEEN 1 AND 31;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_GET_GRADE_LEVEL(i_nSCORE NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 获取分数线

        -- 嵌套函数: 比较分数与阈值

        v_EXCELLENT NUMERIC := 90;
        v_GOOD      NUMERIC := 80;
        v_PASS      NUMERIC := 70;
BEGIN
        -- 获取所有阈值
        v_EXCELLENT := GET_THRESHOLD('EXCELLENT');
        v_GOOD := GET_THRESHOLD('GOOD');
        v_PASS := GET_THRESHOLD('PASS');

        -- 判定等级
        IF COMPARE_SCORE(i_nSCORE, v_EXCELLENT) THEN RETURN 1;  -- 优秀
        ELSIF COMPARE_SCORE(i_nSCORE, v_GOOD) THEN RETURN 2;     -- 良好
        ELSIF COMPARE_SCORE(i_nSCORE, v_PASS) THEN RETURN 3;    -- 及格
        ELSE RETURN 4;                                             -- 不及格
        END IF;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

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

DROP FUNCTION IF EXISTS get_threshold(varchar);
CREATE OR REPLACE FUNCTION GET_THRESHOLD(i_sKBN VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 从缓存获取配置

            -- 再嵌套: 从数据库获取
BEGIN
            -- 优先从缓存获取
            RETURN GET_FROM_CACHE(i_sKBN);
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS get_from_cache(varchar);
CREATE OR REPLACE FUNCTION GET_FROM_CACHE(i_sKEY VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
                IF i_sKEY = 'EXCELLENT' THEN RETURN current_setting('PKG_SCORE_MANAGE.g_nEXCELLENT_SCORE');
                ELSIF i_sKEY = 'GOOD' THEN RETURN current_setting('PKG_SCORE_MANAGE.g_nGOOD_SCORE');
                ELSIF i_sKEY = 'PASS' THEN RETURN current_setting('PKG_SCORE_MANAGE.g_nMEDIUM_SCORE');
                ELSE RETURN 60;
                END IF;
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;

DROP FUNCTION IF EXISTS get_from_db(varchar);
CREATE OR REPLACE FUNCTION GET_FROM_DB(i_sCD VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    v_VALUE NUMERIC;
BEGIN
                SELECT COALESCE((CONFIG_VALUE)::NUMERIC, 0)
                INTO v_VALUE
                FROM SYSTEM_CONFIG
                WHERE CONFIG_CD = 'GRADE_LEVEL_' || i_sCD
                AND DELETE_FLG = 0;
                RETURN v_VALUE;
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_CALC_TOTAL(i_nSTUDENT_NO NUMERIC, i_nEXAM_TYPE NUMERIC DEFAULT NULL)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证学生编号

        -- 嵌套函数: 构建查询条件

        nTOTAL  NUMERIC := 0;
        v_SQL   VARCHAR(1000);
BEGIN
        IF NOT VALIDATE_STUDENT(i_nSTUDENT_NO) THEN
            RETURN 0;
        END IF;

        v_SQL := 'SELECT COALESCE(SUM(SCORE), 0) FROM SCORE_REC' || BUILD_WHERE_CLAUSE(i_nSTUDENT_NO, i_nEXAM_TYPE);
        EXECUTE v_SQL INTO nTOTAL;

        RETURN nTOTAL;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS build_where_clause(numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_WHERE_CLAUSE(i_nSTU NUMERIC, i_nEXM NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_CLAUSE VARCHAR(500) := ' WHERE 1=1 ';
BEGIN
            IF i_nSTU IS NOT NULL AND i_nSTU > 0 THEN
                v_CLAUSE := v_CLAUSE || ' AND STUDENT_NO = ' || i_nSTU;
            END IF;
            IF i_nEXM IS NOT NULL THEN
                v_CLAUSE := v_CLAUSE || ' AND EXAM_TYPE = ' || i_nEXM;
            END IF;
            RETURN v_CLAUSE;
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


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_CALC_AVG(i_nSTUDENT_NO NUMERIC, i_nSUBJECT_CD NUMERIC DEFAULT NULL)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 获取科目信息

        -- 嵌套函数: 计算加权平均

        nAVG  NUMERIC := 0;
        v_SUBJECT_NAME VARCHAR(100);
        n_CREDIT NUMERIC;
BEGIN
        -- 如果指定了科目，使用简单平均
        IF i_nSUBJECT_CD IS NOT NULL THEN
            IF GET_SUBJECT_INFO(i_nSUBJECT_CD, v_SUBJECT_NAME, n_CREDIT) THEN
                SELECT ROUND(COALESCE(AVG(SCORE), 0)::NUMERIC, 2)
                INTO nAVG
                FROM SCORE_REC
                WHERE STUDENT_NO = i_nSTUDENT_NO
                AND SUBJECT_CD = i_nSUBJECT_CD;
            END IF;
        ELSE
            -- 未指定科目，使用加权平均
            nAVG := CALC_WEIGHTED_AVG(i_nSTUDENT_NO);
        END IF;

        RETURN nAVG;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

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
            rec record;
BEGIN
            FOR rec IN (SELECT SC.SCORE, SB.CREDIT
                FROM SCORE_REC SC, SUBJECT_MST SB
                WHERE SC.STUDENT_NO = i_nSTU
                AND SC.SUBJECT_CD = SB.SUBJECT_CD
                AND SB.DELETE_FLG = 0
            ) LOOP
                v_WEIGHTED_SUM := v_WEIGHTED_SUM + (rec.SCORE * rec.CREDIT);
                v_TOTAL_WEIGHT := v_TOTAL_WEIGHT + rec.CREDIT;
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

DROP PROCEDURE IF EXISTS get_subject_info(numeric, varchar, numeric);
DROP PROCEDURE IF EXISTS GET_SUBJECT_INFO;
CREATE OR REPLACE PROCEDURE GET_SUBJECT_INFO(i_nCD NUMERIC, INOUT o_sNAME VARCHAR, INOUT o_nCREDIT NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            SELECT SUBJECT_NAME, CREDIT
            INTO o_sNAME, o_nCREDIT
            FROM SUBJECT_MST
            WHERE SUBJECT_CD = i_nCD AND DELETE_FLG = 0;
            RETURN;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.FUNC_CALC_RANK(i_nSTUDENT_NO NUMERIC, i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC, i_nRANK_TYPE NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 获取学生总分

        -- 嵌套函数: 构建排名SQL

        nTOTAL_SCORE   NUMERIC;
        nRANK          NUMERIC := 1;
        v_SQL          VARCHAR(4000);
BEGIN
        nTOTAL_SCORE := GET_TOTAL_SCORE(i_nSTUDENT_NO);
        v_SQL := BUILD_RANK_SQL(i_nSTUDENT_NO, i_nGRADE, i_nCLASS_NO, i_nRANK_TYPE, nTOTAL_SCORE);

        IF v_SQL IS NOT NULL THEN
            EXECUTE v_SQL INTO nRANK;
        END IF;

        RETURN nRANK;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS build_rank_sql(numeric, numeric, numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION BUILD_RANK_SQL(i_nSTU NUMERIC, i_nGRD NUMERIC, i_nCLS NUMERIC, i_nTYP NUMERIC, i_nTOT NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 获取分组字段

            v_SQL VARCHAR(4000);
BEGIN
            IF i_nTYP = 1 THEN
                v_SQL := 'SELECT COUNT(*) + 1 FROM ( ' ||
                         'SELECT S.STUDENT_NO, SUM(COALESCE(SC.SCORE, 0)) AS TOTAL ' ||
                         'FROM STUDENT_MST S, SCORE_REC SC ' ||
                         'WHERE S.STUDENT_NO = SC.STUDENT_NO%OUTERJOIN0% ' ||
                         'AND S.GRADE = ' || i_nGRD || ' ' ||
                         'AND ' || GET_GROUP_BY(1) || ' ' ||
                         'AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO ' ||
                         'HAVING SUM(COALESCE(SC.SCORE, 0)) > ' || i_nTOT || ') ';
            ELSE
                v_SQL := 'SELECT COUNT(*) + 1 FROM ( ' ||
                         'SELECT S.STUDENT_NO, SUM(COALESCE(SC.SCORE, 0)) AS TOTAL ' ||
                         'FROM STUDENT_MST S, SCORE_REC SC ' ||
                         'WHERE S.STUDENT_NO = SC.STUDENT_NO(+) ' ||
                         'AND S.GRADE = ' || i_nGRD || ' ' ||
                         'AND ' || GET_GROUP_BY(2) || ' ' ||
                         'AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO ' ||
                         'HAVING SUM(COALESCE(SC.SCORE, 0)) > ' || i_nTOT || ') ';
            END IF;
            RETURN v_SQL;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS get_group_by(numeric);
CREATE OR REPLACE FUNCTION GET_GROUP_BY(p_nTYP NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
                IF p_nTYP = 1 THEN
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

DROP FUNCTION IF EXISTS get_total_score(numeric);
CREATE OR REPLACE FUNCTION GET_TOTAL_SCORE(i_nSTU NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN FUNC_CALC_TOTAL(i_nSTU, NULL);
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PROC_CLEAR_WORKTABLE(i_sWSNUM VARCHAR)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
        DELETE FROM WK_SCORE_REPORT WHERE WSNUM = i_sWSNUM;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PROC_INSERT_WORK(i_sWSNUM VARCHAR, i_nSTUDENT_NO NUMERIC, i_vSTUDENT_NAME VARCHAR, i_nTOTAL_SCORE NUMERIC, i_nAVG_SCORE NUMERIC, i_nPASS_COUNT NUMERIC, i_nFAIL_COUNT NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 嵌套函数: 验证数据

        -- 嵌套函数: 生成行号

        nROWNO  NUMERIC;
BEGIN
        IF NOT VALIDATE_DATA(i_nSTUDENT_NO, i_nTOTAL_SCORE, i_nAVG_SCORE) THEN
            RETURN;
        END IF;

        nROWNO := GENERATE_ROWNO(i_sWSNUM);

        INSERT INTO WK_SCORE_REPORT(
            ROW_NO, NO, STUDENT_NO, STUDENT_NAME,
            TOTAL_SCORE, AVERAGE_SCORE, PASS_COUNT, FAIL_COUNT,
            WSNUM, BATCHKBN, JOBNUM
        ) VALUES (
            nROWNO, nROWNO, i_nSTUDENT_NO, i_vSTUDENT_NAME,
            i_nTOTAL_SCORE, i_nAVG_SCORE, i_nPASS_COUNT, i_nFAIL_COUNT,
            i_sWSNUM, c_ONLINE, current_setting('PKG_SCORE_MANAGE.g_nJOBNUM')
        );
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS generate_rowno(varchar);
CREATE OR REPLACE FUNCTION GENERATE_ROWNO(i_sWS VARCHAR)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    v_MAX BIGINT;
BEGIN
            SELECT COALESCE(MAX(ROW_NO), 0) + 1 INTO v_MAX
            FROM WK_SCORE_REPORT WHERE WSNUM = i_sWS;
            RETURN v_MAX;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS validate_data(numeric, numeric, numeric);
CREATE OR REPLACE FUNCTION VALIDATE_DATA(i_nNO NUMERIC, i_nTOT NUMERIC, i_nAVG NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN i_nNO IS NOT NULL AND i_nNO > 0;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PROC_PROCESS_STUDENT(i_nSTUDENT_NO NUMERIC, i_vSTUDENT_NAME VARCHAR, i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC, i_sWSNUM VARCHAR, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
-- 嵌套函数: 计算统计数据
BEGIN
        o_nRTN := CALC_STATS;
    END;
$$;

CREATE OR REPLACE FUNCTION CALC_STATS()
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
-- 再嵌套: 计算总分

            -- 再嵌套: 计算平均分

            -- 再嵌套: 计算及格/不及格数

            nTOTAL NUMERIC;
            nAVG   NUMERIC;
            nPASS  NUMERIC;
            nFAIL  NUMERIC;
BEGIN
                -- L3 stub: refs parent proc params (i_nSTUDENT_NO etc.)
    RETURN 0;
EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS calc_total_score(numeric);
CREATE OR REPLACE FUNCTION CALC_TOTAL_SCORE(i_nSTU NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
                RETURN FUNC_CALC_TOTAL(i_nSTU, NULL);
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;

DROP FUNCTION IF EXISTS calc_avg_score(numeric);
CREATE OR REPLACE FUNCTION CALC_AVG_SCORE(i_nSTU NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
                RETURN FUNC_CALC_AVG(i_nSTU, NULL);
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;

DROP PROCEDURE IF EXISTS calc_pass_fail(numeric, numeric, numeric);
DROP PROCEDURE IF EXISTS CALC_PASS_FAIL;
CREATE OR REPLACE PROCEDURE CALC_PASS_FAIL(i_nSTU NUMERIC, INOUT o_nPASS NUMERIC, INOUT o_nFAIL NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
                SELECT
                    COALESCE(SUM(CASE WHEN IS_PASS = 1 THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN IS_PASS = 0 THEN 1 ELSE 0 END), 0)
                INTO o_nPASS, o_nFAIL
                FROM SCORE_REC WHERE STUDENT_NO = i_nSTU;
                RETURN;
            EXCEPTION
                WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                    RAISE EXCEPTION 'ORA: %', _p25_msg;
            END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PROC_BATCH_SCORE_PROCESS(i_vWSNUM VARCHAR, i_vTANTO_CD VARCHAR, i_nEXAM_DATE NUMERIC, i_nEXAM_TYPE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_scores         TEXT[];
        v_exists         NUMERIC;
        v_grade_level    NUMERIC;
        v_cnt            NUMERIC := 0;

        -- 嵌套函数: 批量获取数据

    BEGIN END;
$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PROC_BATCH_RANK_UPDATE(i_nGRADE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
BEGIN
    -- L3 复杂 stub (Oracle 集合/UTL_FILE 强依赖, 详见 HANDOFF P4-3 续)
    o_nRTN := 0;
END;
$$;




DROP FUNCTION IF EXISTS build_rank_query(numeric);
CREATE OR REPLACE FUNCTION BUILD_RANK_QUERY(i_nGRD NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
v_sql VARCHAR(4000);
BEGIN
            v_sql := ' SELECT STUDENT_NO, TOTAL_SCORE, ' ||
                     ' RANK() OVER (PARTITION BY CLASS_NO ORDER BY TOTAL_SCORE DESC) AS CLASS_RANK, ' ||
                     ' RANK() OVER (PARTITION BY GRADE ORDER BY TOTAL_SCORE DESC) AS GRADE_RANK ' ||
                     ' FROM (SELECT S.STUDENT_NO, S.GRADE, S.CLASS_NO, ' ||
                     ' (SELECT SUM(NVL(SC.SCORE, 0)) FROM SCORE_REC SC WHERE SC.STUDENT_NO = S.STUDENT_NO) AS TOTAL_SCORE ' ||
                     ' FROM STUDENT_MST S WHERE S.DELETE_FLG = 0 AND S.GRADE = ' || i_nGRD || ') alias0 ';
            RETURN v_sql;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PROC_EXPORT_CSV(i_vWSNUM VARCHAR, i_vFILENAME VARCHAR, INOUT o_nRTN NUMERIC, INOUT o_vFILE_PATH VARCHAR)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    v_FILE RECORD;
        v_LINE     VARCHAR(4000);
        v_CNT      NUMERIC := 0;

        -- 嵌套函数: 格式化CSV行

        -- 嵌套函数: 写入文件头

        CUR_DATA CURSOR FOR
            SELECT ROW_NO, STUDENT_NO, STUDENT_NAME, TOTAL_SCORE, AVERAGE_SCORE
            FROM WK_SCORE_REPORT
            WHERE WSNUM = i_vWSNUM
            ORDER BY ROW_NO;
BEGIN
        RAISE NOTICE 'UTL_FILE.FOPEN stub (PG 不支持文件写入)';
        v_FILE := NULL;
PERFORM WRITE_HEADER(v_FILE);

        FOR rec IN CUR_DATA LOOP
            v_LINE := FORMAT_CSV_LINE(rec.ROW_NO, rec.STUDENT_NO, rec.STUDENT_NAME,
                                      rec.TOTAL_SCORE, rec.AVERAGE_SCORE);
            RAISE LOG 'UTL_FILE.PUT_LINE stub: %', v_LINE;
            v_CNT := v_CNT + 1;
        END LOOP;
-- stub: -- UTL_FILE.FCLOSE stub;
        o_nRTN := c_OK;
        o_vFILE_PATH := 'DATA_DIR/' || i_vFILENAME;
PERFORM FUNC_SETLOG('PROC_EXPORT_CSV', 0, 0, '', '', '导出: ' || v_CNT || '件');

    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS write_header(record);
CREATE OR REPLACE FUNCTION WRITE_HEADER(i_f INOUT RECORD)
RETURNS RECORD
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RAISE LOG 'UTL_FILE.PUT_LINE stub: %', 'NO,STUDENT_NO,STUDENT_NAME,TOTAL_SCORE,AVG_SCORE';
            RETURN;
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;

DROP FUNCTION IF EXISTS format_csv_line(numeric, numeric, varchar, numeric, numeric);
CREATE OR REPLACE FUNCTION FORMAT_CSV_LINE(i_nNO NUMERIC, i_nSTU NUMERIC, i_vNAME VARCHAR, i_nTOT NUMERIC, i_nAVG NUMERIC)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;

BEGIN
            RETURN i_nNO || ',' || i_nSTU || ',' ||
                   REPLACE(i_vNAME, ',', ';') || ',' ||
                   COALESCE((i_nTOT)::varchar, '') || ',' ||
                   COALESCE((i_nAVG)::varchar, '');
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
                RAISE EXCEPTION 'ORA: %', _p25_msg;
        END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PONLINE(i_sWSNUM VARCHAR, i_sTANTO_CD CHAR, i_sPARAM VARCHAR, INOUT o_nRESULT NUMERIC, INOUT o_sMESSAGE VARCHAR)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    bRTN BOOLEAN;
BEGIN
        bRTN := FUNC_INIT(i_sWSNUM, i_sTANTO_CD);
PERFORM PROC_LOAD_CONFIG();
PERFORM PROC_CLEAR_WORKTABLE(current_setting('PKG_SCORE_MANAGE.g_sWSNUM'));

        o_nRESULT := c_OK;
        o_sMESSAGE := '处理完成,批次号:' || current_setting('PKG_SCORE_MANAGE.g_nJOBNUM');
        bRTN := FUNC_SETLOG('PONLINE', 0, 0, '', '', o_sMESSAGE);

    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.PBATCH(i_nJOBNUM NUMERIC, i_sWSNUM VARCHAR, i_sTANTO_CD CHAR, i_nNENDO NUMERIC, i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC, i_nEXAM_TYPE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    bRTN BOOLEAN;
        nRTN        NUMERIC;
        nROWNO      NUMERIC := 0;
        vUSER       VARCHAR(100);
        v_cursor    REFCURSOR;
        v_student   STUDENT_MST;
BEGIN
    PERFORM set_config('PKG_SCORE_MANAGE.g_nJOBNUM', i_nJOBNUM, false);
        PERFORM set_config('PKG_SCORE_MANAGE.g_sWSNUM', i_sWSNUM, false);
        PERFORM set_config('PKG_SCORE_MANAGE.g_sTANTO_CD', i_sTANTO_CD, false);
PERFORM PROC_LOAD_CONFIG();
PERFORM PROC_CLEAR_WORKTABLE(current_setting('PKG_SCORE_MANAGE.g_sWSNUM'));

        v_cursor := F_GET_STUDENT_CURSOR(i_nGRADE, i_nCLASS_NO);

        LOOP
            FETCH v_cursor INTO v_student;
            EXIT WHEN NOT FOUND;
            nROWNO := nROWNO + 1;
PERFORM PROC_PROCESS_STUDENT(v_student.STUDENT_NO, v_student.STUDENT_NAME,
                                v_student.GRADE, v_student.CLASS_NO, current_setting('PKG_SCORE_MANAGE.g_sWSNUM'), nRTN);
        END LOOP;

        CLOSE v_cursor;
        -- COMMIT removed (PG 15 CALL manages txn)
        PERFORM set_config('PKG_SCORE_MANAGE.g_sMESSAGE', '批次处理完成:' || nROWNO || '件', false);
        bRTN := FUNC_SETLOG('PBATCH', 0, 0, '', '', current_setting('PKG_SCORE_MANAGE.g_sMESSAGE'));
        o_nRTN := c_OK;

    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.P_PROCESS_SCORE(i_nSTUDENT_NO NUMERIC, i_nSUBJECT_CD NUMERIC, i_nEXAM_DATE NUMERIC, i_nEXAM_TYPE NUMERIC, i_nSCORE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    nEXISTS NUMERIC;
        nGRADE     NUMERIC;
BEGIN
        o_nRTN := c_OK;

        SELECT COUNT(*) INTO nEXISTS
        FROM SCORE_REC
        WHERE STUDENT_NO = i_nSTUDENT_NO
        AND SUBJECT_CD = i_nSUBJECT_CD
        AND EXAM_DATE = i_nEXAM_DATE
        AND EXAM_TYPE = i_nEXAM_TYPE;

        nGRADE := FUNC_GET_GRADE_LEVEL(i_nSCORE);

        IF nEXISTS > 0 THEN
            UPDATE SCORE_REC
            SET SCORE = i_nSCORE, GRADE_LEVEL = nGRADE,
                IS_PASS = CASE WHEN i_nSCORE >= current_setting('PKG_SCORE_MANAGE.g_nPASS_SCORE') THEN 1 ELSE 0 END,
                REC_UPDATE_DATE = TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS')
            WHERE STUDENT_NO = i_nSTUDENT_NO
            AND SUBJECT_CD = i_nSUBJECT_CD
            AND EXAM_DATE = i_nEXAM_DATE
            AND EXAM_TYPE = i_nEXAM_TYPE;
        ELSE
            INSERT INTO SCORE_REC(
                STUDENT_NO, SUBJECT_CD, EXAM_DATE, EXAM_TYPE,
                SCORE, GRADE_LEVEL, IS_PASS,
                REC_CREATE_DATE, REC_UPDATE_DATE
            ) VALUES (
                i_nSTUDENT_NO, i_nSUBJECT_CD, i_nEXAM_DATE, i_nEXAM_TYPE,
                i_nSCORE, nGRADE, CASE WHEN i_nSCORE >= current_setting('PKG_SCORE_MANAGE.g_nPASS_SCORE') THEN 1 ELSE 0 END,
                TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'),
                TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS')
            );
        END IF;

    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.P_BATCH_PROCESS_SCORE(i_vWSNUM VARCHAR, i_vTANTO_CD VARCHAR, i_nEXAM_DATE NUMERIC, i_nEXAM_TYPE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
BEGIN
PERFORM PROC_BATCH_SCORE_PROCESS(i_vWSNUM, i_vTANTO_CD, i_nEXAM_DATE, i_nEXAM_TYPE, o_nRTN);
    END;
$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.P_GEN_REPORT(i_nNENDO NUMERIC, i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC, i_vWSNUM VARCHAR, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    v_student RECORD;
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    bRTN BOOLEAN;
        nRTN        NUMERIC;
        nROWNO      NUMERIC := 0;
        vUSER       VARCHAR(100);
BEGIN
    PERFORM set_config('PKG_SCORE_MANAGE.g_sWSNUM', i_vWSNUM, false);
PERFORM set_config('PKG_SCORE_MANAGE.g_nJOBNUM', (SELECT COALESCE(MAX(JOBNUM), 0) + 1 FROM WK_SCORE_REPORT)::text, false);
PERFORM PROC_LOAD_CONFIG();
PERFORM PROC_CLEAR_WORKTABLE(current_setting('PKG_SCORE_MANAGE.g_sWSNUM'));

        FOR v_student IN SELECT * FROM CUR_ALL_STUDENT(i_nGRADE, i_nCLASS_NO) LOOP
PERFORM PROC_PROCESS_STUDENT(v_student.STUDENT_NO, v_student.STUDENT_NAME,
                                v_student.GRADE, v_student.CLASS_NO, current_setting('PKG_SCORE_MANAGE.g_sWSNUM'), nRTN);
            nROWNO := nROWNO + 1;
        END LOOP;

        PERFORM set_config('PKG_SCORE_MANAGE.g_sMESSAGE', '报表生成:' || nROWNO || '件', false);
        bRTN := FUNC_SETLOG('P_GEN_REPORT', 0, 0, '', '', current_setting('PKG_SCORE_MANAGE.g_sMESSAGE'));
        o_nRTN := c_OK;

    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.P_UPDATE_RANK(i_nGRADE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    REC RECORD;
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    nTOTAL NUMERIC;
BEGIN
        o_nRTN := c_OK;
        FOR REC IN SELECT * FROM CUR_ALL_STUDENT(i_nGRADE, NULL) LOOP
            nTOTAL := FUNC_CALC_TOTAL(REC.STUDENT_NO, NULL);
        END LOOP;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.P_BATCH_UPDATE_RANK(i_nGRADE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
BEGIN
PERFORM PROC_BATCH_RANK_UPDATE(i_nGRADE, o_nRTN);
    END;
$$;


CREATE OR REPLACE PROCEDURE PKG_SCORE_MANAGE.P_EXPORT_CSV(i_vWSNUM VARCHAR, i_vFILENAME VARCHAR, INOUT o_nRTN NUMERIC, INOUT o_vFILE_PATH VARCHAR)
LANGUAGE plpgsql
AS $$
BEGIN
PERFORM PROC_EXPORT_CSV(i_vWSNUM, i_vFILENAME, o_nRTN, o_vFILE_PATH);
    END;
$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.F_GET_SCORE_LIST(i_nSTUDENT_NO NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
BEGIN
    -- L3 复杂 stub (Oracle 集合/UTL_FILE 强依赖, 详见 HANDOFF P4-3 续)
    RETURN 0;
END;
$$;





CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.F_GET_TOTAL(i_nSTUDENT_NO NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
BEGIN
        RETURN FUNC_CALC_TOTAL(i_nSTUDENT_NO, NULL);
    END;
$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.F_GET_AVG(i_nSTUDENT_NO NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
BEGIN
        RETURN FUNC_CALC_AVG(i_nSTUDENT_NO, NULL);
    END;
$$;


CREATE OR REPLACE FUNCTION PKG_SCORE_MANAGE.F_GET_RANK(i_nSTUDENT_NO NUMERIC, i_nRANK_TYPE NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
    vGRADE BIGINT;
        vCLASS     BIGINT;
BEGIN
        SELECT GRADE, CLASS_NO INTO vGRADE, vCLASS
        FROM STUDENT_MST WHERE STUDENT_NO = i_nSTUDENT_NO;
        RETURN FUNC_CALC_RANK(i_nSTUDENT_NO, vGRADE, vCLASS, i_nRANK_TYPE);
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;

$$;

DROP FUNCTION IF EXISTS cur_all_student(numeric, numeric);
CREATE OR REPLACE FUNCTION CUR_ALL_STUDENT(i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC)
RETURNS TABLE(ADDRESS varchar(500), BIRTH_DATE bigint, CLASS_NO bigint, DELETE_FLG bigint, ENROLL_DATE bigint, GENDER bigint, GRADE bigint, GRADUATE_DATE bigint, PHONE varchar(20), REC_CREATE_WS varchar(63), REC_UPDATE_WS varchar(63), STATUS bigint, STUDENT_KANA varchar(200), STUDENT_NAME varchar(100), STUDENT_NO bigint)
LANGUAGE sql STABLE
AS $$
    SELECT ADDRESS::varchar(500), BIRTH_DATE::bigint, CLASS_NO::bigint, DELETE_FLG::bigint, ENROLL_DATE::bigint, GENDER::bigint, GRADE::bigint, GRADUATE_DATE::bigint, PHONE::varchar(20), REC_CREATE_WS::varchar(63), REC_UPDATE_WS::varchar(63), STATUS::bigint, STUDENT_KANA::varchar(200), STUDENT_NAME::varchar(100), STUDENT_NO::bigint
    FROM STUDENT_MST
    WHERE DELETE_FLG = 0 AND STATUS = 0
      AND (i_nGRADE IS NULL OR GRADE = i_nGRADE)
      AND (i_nCLASS_NO IS NULL OR CLASS_NO = i_nCLASS_NO)
    ORDER BY STUDENT_NO;
$$;

CREATE OR REPLACE FUNCTION CUR_ALL_STUDENT(i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC)
RETURNS TABLE(ADDRESS varchar(500), BIRTH_DATE bigint, CLASS_NO bigint, DELETE_FLG bigint, ENROLL_DATE bigint, GENDER bigint, GRADE bigint, GRADUATE_DATE bigint, PHONE varchar(20), REC_CREATE_WS varchar(63), REC_UPDATE_WS varchar(63), STATUS bigint, STUDENT_KANA varchar(200), STUDENT_NAME varchar(100), STUDENT_NO bigint)
LANGUAGE sql STABLE
AS $$
    SELECT ADDRESS::varchar(500), BIRTH_DATE::bigint, CLASS_NO::bigint, DELETE_FLG::bigint, ENROLL_DATE::bigint, GENDER::bigint, GRADE::bigint, GRADUATE_DATE::bigint, PHONE::varchar(20), REC_CREATE_WS::varchar(63), REC_UPDATE_WS::varchar(63), STATUS::bigint, STUDENT_KANA::varchar(200), STUDENT_NAME::varchar(100), STUDENT_NO::bigint
    FROM STUDENT_MST
    WHERE DELETE_FLG = 0 AND STATUS = 0
      AND (i_nGRADE IS NULL OR GRADE = i_nGRADE)
      AND (i_nCLASS_NO IS NULL OR CLASS_NO = i_nCLASS_NO)
    ORDER BY STUDENT_NO;
$$;

