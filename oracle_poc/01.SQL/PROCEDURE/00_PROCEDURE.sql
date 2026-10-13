-- ============================================================================
-- 独立存储过程
-- ============================================================================

-- ============================================================================
-- 1. 成绩处理存储过程
-- ============================================================================
CREATE OR REPLACE PROCEDURE SP_PROCESS_SCORE(
    i_nSTUDENT_NO      IN  NUMBER,
    i_nSUBJECT_CD      IN  NUMBER,
    i_nEXAM_DATE       IN  NUMBER,
    i_nEXAM_TYPE       IN  NUMBER,
    i_nSCORE           IN  NUMBER,
    i_vTANTO_CD        IN  VARCHAR2,
    o_nRTN             OUT NUMBER
)
IS
    c_OK               CONSTANT PLS_INTEGER := 0;
    c_ERR              CONSTANT PLS_INTEGER := -1;
    VCOUNT             NUMBER;
    V_PASS_SCORE       NUMBER := 60;
    V_GRADE_LEVEL      NUMBER;
    V_EXISTS           NUMBER;
BEGIN
    o_nRTN := c_OK;

    -- 获取及格分数线
    BEGIN
        SELECT NVL(TO_NUMBER(CONFIG_VALUE), 60)
        INTO V_PASS_SCORE
        FROM SYSTEM_CONFIG
        WHERE CONFIG_CD = 'PASS_SCORE_DEFAULT' AND DELETE_FLG = 0;
    EXCEPTION
        WHEN OTHERS THEN
            V_PASS_SCORE := 60;
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
            REC_UPDATE_DATE = TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'),
            REC_UPDATE_KBN = '02'
        WHERE STUDENT_NO = i_nSTUDENT_NO
        AND SUBJECT_CD = i_nSUBJECT_CD
        AND EXAM_DATE = i_nEXAM_DATE
        AND EXAM_TYPE = i_nEXAM_TYPE;
    ELSE
        INSERT INTO SCORE_REC (
            STUDENT_NO, SUBJECT_CD, EXAM_DATE, EXAM_TYPE,
            SCORE, GRADE_LEVEL, IS_PASS,
            REC_CREATE_TANTO_CD, REC_CREATE_DATE, REC_CREATE_WS,
            REC_UPDATE_TANTO_CD, REC_UPDATE_DATE, REC_UPDATE_KBN
        ) VALUES (
            i_nSTUDENT_NO, i_nSUBJECT_CD, i_nEXAM_DATE, i_nEXAM_TYPE,
            i_nSCORE, V_GRADE_LEVEL, CASE WHEN i_nSCORE >= V_PASS_SCORE THEN 1 ELSE 0 END,
            i_vTANTO_CD, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'), 'BATCH',
            i_vTANTO_CD, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'), '01'
        );
    END IF;

EXCEPTION
    WHEN OTHERS THEN
        o_nRTN := c_ERR;
        ROLLBACK;
END SP_PROCESS_SCORE;
/

-- ============================================================================
-- 2. 生成成绩报表存储过程
-- ============================================================================
CREATE OR REPLACE PROCEDURE SP_GEN_SCORE_REPORT(
    i_nNENDO           IN  NUMBER,
    i_nGRADE           IN  NUMBER,
    i_nCLASS_NO        IN  NUMBER,
    i_nEXAM_TYPE       IN  NUMBER,
    i_vWSNUM           IN  VARCHAR2,
    i_vTANTO_CD        IN  VARCHAR2,
    o_nRTN             OUT NUMBER
)
IS
    c_OK               CONSTANT PLS_INTEGER := 0;
    c_ERR              CONSTANT PLS_INTEGER := -1;
    nJOBNUM            NUMBER;
    nROWNO             NUMBER := 0;
    nTOTAL             NUMBER;
    nAVG               NUMBER;
    nPASS              NUMBER;
    nFAIL              NUMBER;

    CURSOR CUR_STUDENT IS
        SELECT STUDENT_NO, STUDENT_NAME, GRADE, CLASS_NO
        FROM STUDENT_MST
        WHERE DELETE_FLG = 0 AND STATUS = 0
        AND GRADE = i_nGRADE AND CLASS_NO = i_nCLASS_NO
        ORDER BY STUDENT_NO;

    R_STUDENT          CUR_STUDENT%ROWTYPE;
BEGIN
    o_nRTN := c_OK;

    SELECT NVL(MAX(JOBNUM), 0) + 1 INTO nJOBNUM FROM WK_SCORE_REPORT;
    DELETE FROM WK_SCORE_REPORT WHERE WSNUM = i_vWSNUM;

    OPEN CUR_STUDENT;
    LOOP
        FETCH CUR_STUDENT INTO R_STUDENT;
        EXIT WHEN CUR_STUDENT%NOTFOUND;
        nROWNO := nROWNO + 1;

        -- 计算总分
        SELECT NVL(SUM(SCORE), 0) INTO nTOTAL
        FROM SCORE_REC WHERE STUDENT_NO = R_STUDENT.STUDENT_NO
        AND (i_nEXAM_TYPE IS NULL OR EXAM_TYPE = i_nEXAM_TYPE);

        -- 计算平均分
        SELECT ROUND(NVL(AVG(SCORE), 0), 2) INTO nAVG
        FROM SCORE_REC WHERE STUDENT_NO = R_STUDENT.STUDENT_NO;

        -- 计算及格/不及格数
        SELECT NVL(SUM(CASE WHEN IS_PASS = 1 THEN 1 ELSE 0 END), 0),
               NVL(SUM(CASE WHEN IS_PASS = 0 THEN 1 ELSE 0 END), 0)
        INTO nPASS, nFAIL
        FROM SCORE_REC WHERE STUDENT_NO = R_STUDENT.STUDENT_NO;

        INSERT INTO WK_SCORE_REPORT (
            ROW_NO, NO, NENDO, STUDENT_NO, STUDENT_NAME, GRADE, CLASS_NO,
            TOTAL_SCORE, AVERAGE_SCORE, PASS_COUNT, FAIL_COUNT,
            WSNUM, BATCHKBN, JOBNUM
        ) VALUES (
            nROWNO, nROWNO, TO_CHAR(i_nNENDO), R_STUDENT.STUDENT_NO,
            R_STUDENT.STUDENT_NAME, R_STUDENT.GRADE, R_STUDENT.CLASS_NO,
            nTOTAL, nAVG, nPASS, nFAIL, i_vWSNUM, 1, nJOBNUM
        );
    END LOOP;
    CLOSE CUR_STUDENT;
    COMMIT;

    DBMS_OUTPUT.PUT_LINE('处理件数: ' || nROWNO);

EXCEPTION
    WHEN OTHERS THEN
        o_nRTN := c_ERR;
        ROLLBACK;
        IF CUR_STUDENT%ISOPEN THEN CLOSE CUR_STUDENT; END IF;
END SP_GEN_SCORE_REPORT;
/

-- ============================================================================
-- 3. 批量导入成绩存储过程
-- ============================================================================
CREATE OR REPLACE PROCEDURE SP_BATCH_IMPORT_SCORE(
    i_vWSNUM           IN  VARCHAR2,
    i_vTANTO_CD        IN  VARCHAR2,
    i_nEXAM_DATE       IN  NUMBER,
    i_nEXAM_TYPE       IN  NUMBER,
    o_nRTN             OUT NUMBER
)
IS
    c_OK               CONSTANT PLS_INTEGER := 0;
    c_ERR              CONSTANT PLS_INTEGER := -1;
    VCOUNT             NUMBER := 0;
    V_PASS_SCORE       NUMBER := 60;
    V_STUDENT_NO       NUMBER;
    V_SUBJECT_CD       NUMBER;
    V_SCORE            NUMBER;
    V_GRADE_LEVEL      NUMBER;

    TYPE t_score_list IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
    v_student_list     t_score_list;
    v_subject_list     t_score_list;
    v_score_list       t_score_list;
BEGIN
    o_nRTN := c_OK;

    -- 获取及格分数线
    BEGIN
        SELECT NVL(TO_NUMBER(CONFIG_VALUE), 60)
        INTO V_PASS_SCORE
        FROM SYSTEM_CONFIG
        WHERE CONFIG_CD = 'PASS_SCORE_DEFAULT' AND DELETE_FLG = 0;
    EXCEPTION
        WHEN OTHERS THEN
            V_PASS_SCORE := 60;
    END;

    -- BULK COLLECT: 获取工作表数据
    SELECT TO_NUMBER(STUDENT_NO), TO_NUMBER(SUBJECT_CD), TO_NUMBER(SCORE)
    BULK COLLECT INTO v_student_list, v_subject_list, v_score_list
    FROM WK_SCORE_REPORT
    WHERE WSNUM = i_vWSNUM
    AND STUDENT_NO IS NOT NULL
    AND SUBJECT_CD IS NOT NULL
    AND SCORE IS NOT NULL
    AND TO_NUMBER(SCORE) >= 0;

    -- FORALL: 批量MERGE
    FORALL i IN 1..v_student_list.COUNT
        MERGE INTO SCORE_REC SC
        USING (SELECT 1 AS DUMMY FROM DUAL) D
        ON (SC.STUDENT_NO = v_student_list(i)
            AND SC.SUBJECT_CD = v_subject_list(i)
            AND SC.EXAM_DATE = i_nEXAM_DATE
            AND SC.EXAM_TYPE = i_nEXAM_TYPE)
        WHEN MATCHED THEN
            UPDATE SET
                SC.SCORE = v_score_list(i),
                SC.GRADE_LEVEL = CASE
                    WHEN v_score_list(i) >= 90 THEN 1
                    WHEN v_score_list(i) >= 80 THEN 2
                    WHEN v_score_list(i) >= 70 THEN 3
                    WHEN v_score_list(i) >= 60 THEN 4
                    ELSE 5 END,
                SC.IS_PASS = CASE WHEN v_score_list(i) >= V_PASS_SCORE THEN 1 ELSE 0 END,
                SC.REC_UPDATE_TANTO_CD = i_vTANTO_CD,
                SC.REC_UPDATE_DATE = TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'),
                SC.REC_UPDATE_KBN = '02'
        WHEN NOT MATCHED THEN
            INSERT (
                STUDENT_NO, SUBJECT_CD, EXAM_DATE, EXAM_TYPE,
                SCORE, GRADE_LEVEL, IS_PASS,
                REC_CREATE_TANTO_CD, REC_CREATE_DATE, REC_CREATE_WS,
                REC_UPDATE_TANTO_CD, REC_UPDATE_DATE, REC_UPDATE_KBN
            ) VALUES (
                v_student_list(i), v_subject_list(i), i_nEXAM_DATE, i_nEXAM_TYPE,
                v_score_list(i),
                CASE
                    WHEN v_score_list(i) >= 90 THEN 1
                    WHEN v_score_list(i) >= 80 THEN 2
                    WHEN v_score_list(i) >= 70 THEN 3
                    WHEN v_score_list(i) >= 60 THEN 4
                    ELSE 5 END,
                CASE WHEN v_score_list(i) >= V_PASS_SCORE THEN 1 ELSE 0 END,
                i_vTANTO_CD, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'), i_vWSNUM,
                i_vTANTO_CD, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'), '01'
            );

    VCOUNT := SQL%ROWCOUNT;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('处理件数: ' || VCOUNT);

EXCEPTION
    WHEN OTHERS THEN
        o_nRTN := c_ERR;
        ROLLBACK;
        DBMS_OUTPUT.PUT_LINE('ERROR: ' || SQLERRM);
END SP_BATCH_IMPORT_SCORE;
/

-- ============================================================================
-- 4. 计算排名存储过程
-- ============================================================================
CREATE OR REPLACE PROCEDURE SP_CALC_RANK(
    i_nGRADE           IN  NUMBER,
    i_nCLASS_NO        IN  NUMBER DEFAULT NULL,
    o_nRTN             OUT NUMBER
)
IS
    c_OK               CONSTANT PLS_INTEGER := 0;
    c_ERR              CONSTANT PLS_INTEGER := -1;
    nTOTAL             NUMBER;
    nRANK              NUMBER;
    nCNT               NUMBER := 0;

    CURSOR CUR_STUDENT IS
        SELECT STUDENT_NO FROM STUDENT_MST
        WHERE DELETE_FLG = 0 AND STATUS = 0
        AND GRADE = i_nGRADE
        AND (i_nCLASS_NO IS NULL OR CLASS_NO = i_nCLASS_NO)
        ORDER BY STUDENT_NO;
BEGIN
    o_nRTN := c_OK;

    FOR R IN CUR_STUDENT LOOP
        nCNT := nCNT + 1;

        SELECT NVL(SUM(SCORE), 0) INTO nTOTAL
        FROM SCORE_REC WHERE STUDENT_NO = R.STUDENT_NO;

        IF i_nCLASS_NO IS NOT NULL THEN
            SELECT COUNT(*) + 1 INTO nRANK
            FROM (
                SELECT S.STUDENT_NO, SUM(NVL(SC.SCORE, 0)) AS T
                FROM STUDENT_MST S, SCORE_REC SC
                WHERE S.STUDENT_NO = SC.STUDENT_NO(+)
                AND S.GRADE = i_nGRADE AND S.CLASS_NO = i_nCLASS_NO
                AND S.DELETE_FLG = 0
                GROUP BY S.STUDENT_NO
                HAVING SUM(NVL(SC.SCORE, 0)) > nTOTAL
            );
        ELSE
            SELECT COUNT(*) + 1 INTO nRANK
            FROM (
                SELECT S.STUDENT_NO, SUM(NVL(SC.SCORE, 0)) AS T
                FROM STUDENT_MST S, SCORE_REC SC
                WHERE S.STUDENT_NO = SC.STUDENT_NO(+)
                AND S.GRADE = i_nGRADE AND S.DELETE_FLG = 0
                GROUP BY S.STUDENT_NO
                HAVING SUM(NVL(SC.SCORE, 0)) > nTOTAL
            );
        END IF;

        UPDATE SCORE_REC SET SCORE_RANK = nRANK WHERE STUDENT_NO = R.STUDENT_NO;
    END LOOP;

    COMMIT;
    DBMS_OUTPUT.PUT_LINE('处理件数: ' || nCNT);

EXCEPTION
    WHEN OTHERS THEN
        o_nRTN := c_ERR;
        ROLLBACK;
END SP_CALC_RANK;
/

-- ============================================================================
-- 5. 批量删除成绩存储过程
-- ============================================================================
CREATE OR REPLACE PROCEDURE SP_BATCH_DELETE_SCORE(
    i_vWSNUM           IN  VARCHAR2,
    i_vTANTO_CD        IN  VARCHAR2,
    o_nRTN             OUT NUMBER
)
IS
    c_OK               CONSTANT PLS_INTEGER := 0;
    c_ERR              CONSTANT PLS_INTEGER := -1;
    nCNT               NUMBER := 0;

    TYPE t_student_list IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
    v_student_list     t_student_list;
BEGIN
    o_nRTN := c_OK;

    -- BULK COLLECT
    SELECT TO_NUMBER(STUDENT_NO)
    BULK COLLECT INTO v_student_list
    FROM WK_SCORE_REPORT
    WHERE WSNUM = i_vWSNUM AND STUDENT_NO IS NOT NULL;

    -- FORALL DELETE
    FORALL i IN 1..v_student_list.COUNT
        DELETE FROM SCORE_REC WHERE STUDENT_NO = v_student_list(i);

    nCNT := SQL%ROWCOUNT;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('删除件数: ' || nCNT);

EXCEPTION
    WHEN OTHERS THEN
        o_nRTN := c_ERR;
        ROLLBACK;
END SP_BATCH_DELETE_SCORE;
/
