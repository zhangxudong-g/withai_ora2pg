-- Perl-style Ora2Py Warnings:
-- OUT params converted: 1 -> RETURNS TABLE
-- OUT params converted: 1 -> RETURNS TABLE
-- OUT params converted: 1 -> RETURNS TABLE
-- OUT params converted: 1 -> RETURNS TABLE
-- OUT params converted: 1 -> RETURNS TABLE

CREATE OR REPLACE PROCEDURE SP_PROCESS_SCORE(i_nSTUDENT_NO NUMERIC, i_nSUBJECT_CD NUMERIC, i_nEXAM_DATE NUMERIC, i_nEXAM_TYPE NUMERIC, i_nSCORE NUMERIC, i_vTANTO_CD VARCHAR, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
c_OK               CONSTANT INTEGER := 0;
    c_ERR              CONSTANT INTEGER := -1;
    VCOUNT             NUMERIC;
    V_PASS_SCORE       NUMERIC := 60;
    V_GRADE_LEVEL      NUMERIC;
    V_EXISTS           NUMERIC;
BEGIN
    o_nRTN := c_OK;

    -- 获取及格分数线
    BEGIN
        SELECT COALESCE((CONFIG_VALUE)::NUMERIC, 60)
        INTO V_PASS_SCORE
        FROM SYSTEM_CONFIG
        WHERE CONFIG_CD = 'PASS_SCORE_DEFAULT' AND DELETE_FLG = 0;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;


    -- 计算等级
    IF i_nSCORE >= 90 THEN V_GRADE_LEVEL := 1;
    ELSIF i_nSCORE >= 80 THEN V_GRADE_LEVEL := 2;
    ELSIF i_nSCORE >= 70 THEN V_GRADE_LEVEL := 3;
    ELSIF i_nSCORE >= 60 THEN V_GRADE_LEVEL := 4;
    ELSE V_GRADE_LEVEL := 5;
    END IF;

    -- 检查记录是否存在
    SELECT COUNT(*)
    INTO V_EXISTS
    FROM SCORE_REC
    WHERE STUDENT_NO = i_nSTUDENT_NO
    AND SUBJECT_CD = i_nSUBJECT_CD
    AND EXAM_DATE = i_nEXAM_DATE
    AND EXAM_TYPE = i_nEXAM_TYPE;

    IF V_EXISTS > 0 THEN
        UPDATE SCORE_REC
        SET SCORE = i_nSCORE, GRADE_LEVEL = V_GRADE_LEVEL,
            IS_PASS = CASE WHEN i_nSCORE >= V_PASS_SCORE THEN 1 ELSE 0 END,
            REC_UPDATE_TANTO_CD = i_vTANTO_CD,
            REC_UPDATE_DATE = TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'),
            REC_UPDATE_KBN = '02'
        WHERE STUDENT_NO = i_nSTUDENT_NO
        AND SUBJECT_CD = i_nSUBJECT_CD
        AND EXAM_DATE = i_nEXAM_DATE
        AND EXAM_TYPE = i_nEXAM_TYPE;
    ELSE
        INSERT INTO SCORE_REC(
            STUDENT_NO, SUBJECT_CD, EXAM_DATE, EXAM_TYPE,
            SCORE, GRADE_LEVEL, IS_PASS,
            REC_CREATE_TANTO_CD, REC_CREATE_DATE, REC_CREATE_WS,
            REC_UPDATE_TANTO_CD, REC_UPDATE_DATE, REC_UPDATE_KBN
        ) VALUES (
            i_nSTUDENT_NO, i_nSUBJECT_CD, i_nEXAM_DATE, i_nEXAM_TYPE,
            i_nSCORE, V_GRADE_LEVEL, CASE WHEN i_nSCORE >= V_PASS_SCORE THEN 1 ELSE 0 END,
            i_vTANTO_CD, TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'), 'BATCH',
            i_vTANTO_CD, TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'), '01'
        );
    END IF;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 2. 生成成绩报表存储过程
-- ============================================================================
$$;


CREATE OR REPLACE PROCEDURE SP_GEN_SCORE_REPORT(i_nNENDO NUMERIC, i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC, i_nEXAM_TYPE NUMERIC, i_vWSNUM VARCHAR, i_vTANTO_CD VARCHAR, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
c_OK               CONSTANT INTEGER := 0;
    c_ERR              CONSTANT INTEGER := -1;
    nJOBNUM            NUMERIC;
    nROWNO             NUMERIC := 0;
    nTOTAL             NUMERIC;
    nAVG               NUMERIC;
    nPASS              NUMERIC;
    nFAIL              NUMERIC;

    CUR_STUDENT CURSOR FOR
        SELECT STUDENT_NO, STUDENT_NAME, GRADE, CLASS_NO
        FROM STUDENT_MST
        WHERE DELETE_FLG = 0 AND STATUS = 0
        AND GRADE = i_nGRADE AND CLASS_NO = i_nCLASS_NO
        ORDER BY STUDENT_NO;

    R_STUDENT RECORD;
BEGIN
    o_nRTN := c_OK;

    SELECT COALESCE(MAX(JOBNUM), 0) + 1 INTO nJOBNUM FROM WK_SCORE_REPORT;
    DELETE FROM WK_SCORE_REPORT WHERE WSNUM = i_vWSNUM;

    OPEN CUR_STUDENT;
    LOOP
        FETCH CUR_STUDENT INTO R_STUDENT;
        EXIT WHEN NOT FOUND;
        nROWNO := nROWNO + 1;

        -- 计算总分
        SELECT COALESCE(SUM(SCORE), 0) INTO nTOTAL
        FROM SCORE_REC WHERE STUDENT_NO = R_STUDENT.STUDENT_NO
        AND (i_nEXAM_TYPE IS NULL OR EXAM_TYPE = i_nEXAM_TYPE);

        -- 计算平均分
        SELECT ROUND(COALESCE(AVG(SCORE), 0)::NUMERIC, 2) INTO nAVG
        FROM SCORE_REC WHERE STUDENT_NO = R_STUDENT.STUDENT_NO;

        -- 计算及格/不及格数
        SELECT COALESCE(SUM(CASE WHEN IS_PASS = 1 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN IS_PASS = 0 THEN 1 ELSE 0 END), 0)
        INTO nPASS, nFAIL
        FROM SCORE_REC WHERE STUDENT_NO = R_STUDENT.STUDENT_NO;

        INSERT INTO WK_SCORE_REPORT(
            ROW_NO, NO, NENDO, STUDENT_NO, STUDENT_NAME, GRADE, CLASS_NO,
            TOTAL_SCORE, AVERAGE_SCORE, PASS_COUNT, FAIL_COUNT,
            WSNUM, BATCHKBN, JOBNUM
        ) VALUES (
            nROWNO, nROWNO, (i_nNENDO)::varchar, R_STUDENT.STUDENT_NO,
            R_STUDENT.STUDENT_NAME, R_STUDENT.GRADE, R_STUDENT.CLASS_NO,
            nTOTAL, nAVG, nPASS, nFAIL, i_vWSNUM, 1, nJOBNUM
        );
    END LOOP;
    CLOSE CUR_STUDENT;
    COMMIT;

    RAISE NOTICE '%', '处理件数: ' || nROWNO;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 3. 批量导入成绩存储过程
-- ============================================================================
$$;


CREATE OR REPLACE PROCEDURE SP_BATCH_IMPORT_SCORE(i_vWSNUM VARCHAR, i_vTANTO_CD VARCHAR, i_nEXAM_DATE NUMERIC, i_nEXAM_TYPE NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
c_OK               CONSTANT INTEGER := 0;
    c_ERR              CONSTANT INTEGER := -1;
    VCOUNT             NUMERIC := 0;
    V_PASS_SCORE       NUMERIC := 60;
    V_STUDENT_NO       NUMERIC;
    V_SUBJECT_CD       NUMERIC;
    V_SCORE            NUMERIC;
    V_GRADE_LEVEL      NUMERIC;

    v_student_list     NUMERIC[];
    v_subject_list     NUMERIC[];
    v_score_list       NUMERIC[];
BEGIN
    o_nRTN := c_OK;

    -- 获取及格分数线
    BEGIN
        SELECT COALESCE((CONFIG_VALUE)::NUMERIC, 60)
        INTO V_PASS_SCORE
        FROM SYSTEM_CONFIG
        WHERE CONFIG_CD = 'PASS_SCORE_DEFAULT' AND DELETE_FLG = 0;
    EXCEPTION
        WHEN OTHERS THEN
            GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
            RAISE EXCEPTION 'ORA: %', _p25_msg;
    END;


    -- BULK COLLECT: 获取工作表数据
    -- BULK COLLECT into 3 variables: PG 不支持
    RAISE EXCEPTION 'not implemented: BULK COLLECT into 3 variables, please use composite type array';

    -- FORALL: 批量MERGE
    FOR i IN 1..array_length(v_student_list, 1) LOOP
        MERGE INTO SCORE_REC SC
        USING(SELECT 1 AS DUMMY) D
        ON (SC.STUDENT_NO = v_student_list[i]
            AND SC.SUBJECT_CD = v_subject_list[i]
            AND SC.EXAM_DATE = i_nEXAM_DATE
            AND SC.EXAM_TYPE = i_nEXAM_TYPE)
        WHEN MATCHED THEN
            UPDATE SET
                SC.SCORE = v_score_list[i],
                SC.GRADE_LEVEL = CASE
                    WHEN v_score_list[i] >= 90 THEN 1
                    WHEN v_score_list[i] >= 80 THEN 2
                    WHEN v_score_list[i] >= 70 THEN 3
                    WHEN v_score_list[i] >= 60 THEN 4
                    ELSE 5 END,
                SC.IS_PASS = CASE WHEN v_score_list[i] >= V_PASS_SCORE THEN 1 ELSE 0 END,
                SC.REC_UPDATE_TANTO_CD = i_vTANTO_CD,
                SC.REC_UPDATE_DATE = TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'),
                SC.REC_UPDATE_KBN = '02'
        WHEN NOT MATCHED THEN
            INSERT(
                STUDENT_NO, SUBJECT_CD, EXAM_DATE, EXAM_TYPE,
                SCORE, GRADE_LEVEL, IS_PASS,
                REC_CREATE_TANTO_CD, REC_CREATE_DATE, REC_CREATE_WS,
                REC_UPDATE_TANTO_CD, REC_UPDATE_DATE, REC_UPDATE_KBN
            ) VALUES (
                v_student_list[i], v_subject_list[i], i_nEXAM_DATE, i_nEXAM_TYPE,
                v_score_list[i],
                CASE
                    WHEN v_score_list[i] >= 90 THEN 1
                    WHEN v_score_list[i] >= 80 THEN 2
                    WHEN v_score_list[i] >= 70 THEN 3
                    WHEN v_score_list[i] >= 60 THEN 4
                    ELSE 5 END,
                CASE WHEN v_score_list[i] >= V_PASS_SCORE THEN 1 ELSE 0 END,
                i_vTANTO_CD, TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'), i_vWSNUM,
                i_vTANTO_CD, TO_CHAR(CURRENT_TIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.MS'), '01'
            );
    END LOOP;

    GET DIAGNOSTICS VCOUNT := ROW_COUNT;
    COMMIT;
    RAISE NOTICE '%', '处理件数: ' || VCOUNT;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_sqlstate = RETURNED_SQLSTATE,
                            _p25_msg = MESSAGE_TEXT;
o_nRTN := c_ERR;
        ROLLBACK;
        RAISE NOTICE '%', 'ERROR: ' || _p25_msg;
END;
-- ============================================================================
-- 4. 计算排名存储过程
-- ============================================================================
$$;


CREATE OR REPLACE PROCEDURE SP_CALC_RANK(i_nGRADE NUMERIC, i_nCLASS_NO NUMERIC, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
c_OK               CONSTANT INTEGER := 0;
    c_ERR              CONSTANT INTEGER := -1;
    nTOTAL             NUMERIC;
    nRANK              NUMERIC;
    nCNT               NUMERIC := 0;

    CUR_STUDENT CURSOR FOR
        SELECT STUDENT_NO FROM STUDENT_MST
        WHERE DELETE_FLG = 0 AND STATUS = 0
        AND GRADE = i_nGRADE
        AND (i_nCLASS_NO IS NULL OR CLASS_NO = i_nCLASS_NO)
        ORDER BY STUDENT_NO;
BEGIN
    o_nRTN := c_OK;

    FOR R IN CUR_STUDENT LOOP
        nCNT := nCNT + 1;

        SELECT COALESCE(SUM(SCORE), 0) INTO nTOTAL
        FROM SCORE_REC WHERE STUDENT_NO = R.STUDENT_NO;

        IF i_nCLASS_NO IS NOT NULL THEN
            SELECT COUNT(*) + 1 INTO nRANK
            FROM (SELECT S.STUDENT_NO, SUM(NVL(SC.SCORE, 0)) AS T
                FROM STUDENT_MST S LEFT JOIN SCORE_REC SC ON S.STUDENT_NO = SC.STUDENT_NO WHERE S.GRADE = i_nGRADE AND S.CLASS_NO = i_nCLASS_NO AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO
                HAVING SUM(NVL(SC.SCORE, 0)) > nTOTAL) alias0;
        ELSE
            SELECT COUNT(*) + 1 INTO nRANK
            FROM (SELECT S.STUDENT_NO, SUM(NVL(SC.SCORE, 0)) AS T
                FROM STUDENT_MST S LEFT JOIN SCORE_REC SC ON S.STUDENT_NO = SC.STUDENT_NO WHERE S.GRADE = i_nGRADE AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO
                HAVING SUM(NVL(SC.SCORE, 0)) > nTOTAL) alias1;
        END IF;

        UPDATE SCORE_REC SET SCORE_RANK = nRANK WHERE STUDENT_NO = R.STUDENT_NO;
    END LOOP;

    COMMIT;
    RAISE NOTICE '%', '处理件数: ' || nCNT;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

-- ============================================================================
-- 5. 批量删除成绩存储过程
-- ============================================================================
$$;


CREATE OR REPLACE PROCEDURE SP_BATCH_DELETE_SCORE(i_vWSNUM VARCHAR, i_vTANTO_CD VARCHAR, INOUT o_nRTN NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    _p25_msg TEXT;
    _p25_sqlstate TEXT;
c_OK               CONSTANT INTEGER := 0;
    c_ERR              CONSTANT INTEGER := -1;
    nCNT               NUMERIC := 0;

    v_student_list     NUMERIC[];
BEGIN
    o_nRTN := c_OK;

    -- BULK COLLECT
    SELECT ARRAY_AGG((STUDENT_NO)::NUMERIC) INTO v_student_list
    FROM WK_SCORE_REPORT
    WHERE WSNUM = i_vWSNUM AND STUDENT_NO IS NOT NULL;

    -- FORALL DELETE
    FOR i IN 1..array_length(v_student_list, 1) LOOP
        DELETE FROM SCORE_REC WHERE STUDENT_NO = v_student_list[i];
    END LOOP;

    GET DIAGNOSTICS nCNT := ROW_COUNT;
    COMMIT;
    RAISE NOTICE '%', '删除件数: ' || nCNT;

EXCEPTION
    WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS _p25_msg = MESSAGE_TEXT;
        RAISE EXCEPTION 'ORA: %', _p25_msg;
END;

$$;

