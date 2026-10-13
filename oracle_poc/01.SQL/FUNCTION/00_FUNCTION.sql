-- ============================================================================
-- 函数定义 - 包含复杂多层嵌套
-- ============================================================================

-- ============================================================================
-- 1. 基础函数: 获取总成绩 (含嵌套验证)
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_TOTAL_SCORE(
    i_nSTUDENT_NO  IN  NUMBER,
    i_nEXAM_TYPE   IN  NUMBER DEFAULT NULL
) RETURN NUMBER
IS
    -- 嵌套函数: 验证学生编号
    FUNCTION VALIDATE_STUDENT_NO(
        i_nNO IN NUMBER
    ) RETURN BOOLEAN
    IS
        -- 再嵌套: 检查格式
        FUNCTION CHECK_FORMAT(
            i_nNO IN NUMBER
        ) RETURN BOOLEAN
        IS
        BEGIN
            RETURN i_nNO IS NOT NULL AND i_nNO > 0 AND i_nNO < 10000000000;
        EXCEPTION
            WHEN OTHERS THEN RETURN FALSE;
        END CHECK_FORMAT;

        v_CNT NUMBER;
    BEGIN
        IF NOT CHECK_FORMAT(i_nNO) THEN
            RETURN FALSE;
        END IF;
        SELECT COUNT(*) INTO v_CNT FROM STUDENT_MST
        WHERE STUDENT_NO = i_nNO AND DELETE_FLG = 0;
        RETURN v_CNT > 0;
    EXCEPTION
        WHEN OTHERS THEN RETURN FALSE;
    END VALIDATE_STUDENT_NO;

    -- 嵌套函数: 构建SQL
    FUNCTION BUILD_SQL(
        i_nSTU IN NUMBER,
        i_nEXM IN NUMBER
    ) RETURN VARCHAR2
    IS
        v_SQL VARCHAR2(2000) := 'SELECT NVL(SUM(SCORE), 0) FROM SCORE_REC WHERE 1=1 ';

        -- 再嵌套: 添加学生条件
        FUNCTION ADD_STUDENT_COND(
            i_vSQL IN VARCHAR2,
            i_nNO  IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nNO IS NOT NULL AND i_nNO > 0 THEN
                RETURN i_vSQL || ' AND STUDENT_NO = ' || i_nNO;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vSQL;
        END ADD_STUDENT_COND;

        -- 再嵌套: 添加考试类型条件
        FUNCTION ADD_EXAM_COND(
            i_vSQL IN VARCHAR2,
            i_nTYP IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nTYP IS NOT NULL THEN
                RETURN i_vSQL || ' AND EXAM_TYPE = ' || i_nTYP;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vSQL;
        END ADD_EXAM_COND;

    BEGIN
        v_SQL := ADD_STUDENT_COND(v_SQL, i_nSTU);
        v_SQL := ADD_EXAM_COND(v_SQL, i_nEXM);
        RETURN v_SQL;
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END BUILD_SQL;

    g_nERR          NUMBER := -1;
    g_nRETURN_CODE  NUMBER := 0;
    g_vSQL          VARCHAR2(2000);
BEGIN
    IF NOT VALIDATE_STUDENT_NO(i_nSTUDENT_NO) THEN
        RETURN 0;
    END IF;

    g_vSQL := BUILD_SQL(i_nSTUDENT_NO, i_nEXAM_TYPE);

    IF g_vSQL IS NOT NULL THEN
        EXECUTE IMMEDIATE g_vSQL INTO g_nRETURN_CODE;
    END IF;

    RETURN g_nRETURN_CODE;

EXCEPTION
    WHEN OTHERS THEN
        RETURN g_nERR;
END F_GET_TOTAL_SCORE;
/

-- ============================================================================
-- 2. 基础函数: 获取平均成绩 (嵌套加权计算)
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_AVG_SCORE(
    i_nSTUDENT_NO  IN  NUMBER,
    i_nSUBJECT_CD  IN  NUMBER DEFAULT NULL
) RETURN NUMBER
IS
    -- 嵌套函数: 获取学分信息
    FUNCTION GET_CREDIT_INFO(
        i_nSUB IN NUMBER,
        o_nCREDIT OUT NUMBER
    ) RETURN BOOLEAN
    IS
        -- 再嵌套: 获取学分
        FUNCTION FETCH_CREDIT(
            i_nCD IN NUMBER
        ) RETURN NUMBER
        IS
            v_CREDIT NUMBER;
        BEGIN
            SELECT NVL(CREDIT, 0) INTO v_CREDIT
            FROM SUBJECT_MST
            WHERE SUBJECT_CD = i_nCD AND DELETE_FLG = 0;
            RETURN v_CREDIT;
        EXCEPTION
            WHEN OTHERS THEN RETURN 1;
        END FETCH_CREDIT;

    BEGIN
        o_nCREDIT := FETCH_CREDIT(i_nSUB);
        RETURN TRUE;
    EXCEPTION
        WHEN OTHERS THEN
            o_nCREDIT := 1;
            RETURN FALSE;
    END GET_CREDIT_INFO;

    -- 嵌套函数: 计算加权平均
    FUNCTION CALC_WEIGHTED_AVG(
        i_nSTU IN NUMBER
    ) RETURN NUMBER
    IS
        v_WEIGHTED_SUM NUMBER := 0;
        v_TOTAL_WEIGHT NUMBER := 0;
        v_AVG NUMBER := 0;

        -- 再嵌套: 处理单科成绩
        FUNCTION PROCESS_SCORE(
            i_nSCORE IN NUMBER,
            i_nCREDIT IN NUMBER
        ) RETURN NUMBER
        IS
        BEGIN
            RETURN i_nSCORE * i_nCREDIT;
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END PROCESS_SCORE;

    BEGIN
        FOR rec IN (
            SELECT SC.SCORE, SB.CREDIT
            FROM SCORE_REC SC, SUBJECT_MST SB
            WHERE SC.STUDENT_NO = i_nSTU
            AND SC.SUBJECT_CD = SB.SUBJECT_CD
            AND SB.DELETE_FLG = 0
        ) LOOP
            v_WEIGHTED_SUM := v_WEIGHTED_SUM + PROCESS_SCORE(rec.SCORE, rec.CREDIT);
            v_TOTAL_WEIGHT := v_TOTAL_WEIGHT + NVL(rec.CREDIT, 1);
        END LOOP;

        IF v_TOTAL_WEIGHT > 0 THEN
            v_AVG := ROUND(v_WEIGHTED_SUM / v_TOTAL_WEIGHT, 2);
        END IF;
        RETURN v_AVG;
    EXCEPTION
        WHEN OTHERS THEN RETURN 0;
    END CALC_WEIGHTED_AVG;

    -- 嵌套函数: 构建SQL
    FUNCTION BUILD_SQL(
        i_nSTU IN NUMBER,
        i_nSUB IN NUMBER
    ) RETURN VARCHAR2
    IS
        v_SQL VARCHAR2(2000) := 'SELECT ROUND(NVL(AVG(SCORE), 0), 2) FROM SCORE_REC WHERE 1=1 ';

        FUNCTION ADD_COND(
            i_vSQL IN VARCHAR2,
            i_nSTU IN NUMBER,
            i_nSUB IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nSTU IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND STUDENT_NO = ' || i_nSTU;
            END IF;
            IF i_nSUB IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND SUBJECT_CD = ' || i_nSUB;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vSQL;
        END ADD_COND;

    BEGIN
        RETURN ADD_COND(v_SQL, i_nSTU, i_nSUB);
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END BUILD_SQL;

    g_nERR         NUMBER := -1;
    g_nRETURN_CODE NUMBER := 0;
    g_vSQL         VARCHAR2(2000);
BEGIN
    IF i_nSUBJECT_CD IS NOT NULL THEN
        g_vSQL := BUILD_SQL(i_nSTUDENT_NO, i_nSUBJECT_CD);
        IF g_vSQL IS NOT NULL THEN
            EXECUTE IMMEDIATE g_vSQL INTO g_nRETURN_CODE;
        END IF;
    ELSE
        g_nRETURN_CODE := CALC_WEIGHTED_AVG(i_nSTUDENT_NO);
    END IF;

    RETURN g_nRETURN_CODE;

EXCEPTION
    WHEN OTHERS THEN
        RETURN g_nERR;
END F_GET_AVG_SCORE;
/

-- ============================================================================
-- 3. 基础函数: 计算等级 (多层嵌套阈值)
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_GRADE_LEVEL(
    i_nSCORE    IN  NUMBER
) RETURN NUMBER
IS
    -- 嵌套函数: 获取分数线
    FUNCTION GET_THRESHOLD(
        i_sKBN IN VARCHAR2
    ) RETURN NUMBER
    IS
        -- 再嵌套: 从配置表获取
        FUNCTION FETCH_FROM_CONFIG(
            i_sCD IN VARCHAR2
        ) RETURN NUMBER
        IS
            -- 再再嵌套: 执行查询
            FUNCTION DO_QUERY(
                i_sSQL IN VARCHAR2
            ) RETURN NUMBER
            IS
                v_VALUE NUMBER;
            BEGIN
                EXECUTE IMMEDIATE i_sSQL INTO v_VALUE;
                RETURN v_VALUE;
            EXCEPTION
                WHEN OTHERS THEN RETURN 0;
            END DO_QUERY;

        BEGIN
            RETURN DO_QUERY(
                'SELECT NVL(TO_NUMBER(CONFIG_VALUE), 0) FROM SYSTEM_CONFIG ' ||
                'WHERE CONFIG_CD = ''GRADE_LEVEL_' || i_sKBN || ''' AND DELETE_FLG = 0'
            );
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END FETCH_FROM_CONFIG;

        -- 再嵌套: 获取默认值
        FUNCTION GET_DEFAULT(
            i_sKBN IN VARCHAR2
        ) RETURN NUMBER
        IS
        BEGIN
            IF i_sKBN = 'EXCELLENT' THEN RETURN 90;
            ELSIF i_sKBN = 'GOOD' THEN RETURN 80;
            ELSIF i_sKBN = 'PASS' THEN RETURN 60;
            ELSE RETURN 60;
            END IF;
        EXCEPTION
            WHEN OTHERS THEN RETURN 60;
        END GET_DEFAULT;

    BEGIN
        RETURN FETCH_FROM_CONFIG(i_sKBN);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN GET_DEFAULT(i_sKBN);
    END GET_THRESHOLD;

    -- 嵌套函数: 比较分数
    FUNCTION COMPARE_SCORE(
        i_nSCORE IN NUMBER,
        i_nTHRESHOLD IN NUMBER
    ) RETURN BOOLEAN
    IS
    BEGIN
        RETURN i_nSCORE >= i_nTHRESHOLD;
    EXCEPTION
        WHEN OTHERS THEN RETURN FALSE;
    END COMPARE_SCORE;

    -- 嵌套函数: 判定等级
    FUNCTION DETERMINE_LEVEL(
        i_nSCORE IN NUMBER,
        i_nEXC IN NUMBER,
        i_nGOOD IN NUMBER,
        i_nPASS IN NUMBER
    ) RETURN NUMBER
    IS
    BEGIN
        IF COMPARE_SCORE(i_nSCORE, i_nEXC) THEN RETURN 1;
        ELSIF COMPARE_SCORE(i_nSCORE, i_nGOOD) THEN RETURN 2;
        ELSIF COMPARE_SCORE(i_nSCORE, i_nPASS) THEN RETURN 3;
        ELSE RETURN 4;
        END IF;
    EXCEPTION
        WHEN OTHERS THEN RETURN 4;
    END DETERMINE_LEVEL;

    v_EXCELLENT NUMBER;
    v_GOOD      NUMBER;
    v_PASS      NUMBER;
BEGIN
    v_EXCELLENT := GET_THRESHOLD('EXCELLENT');
    v_GOOD := GET_THRESHOLD('GOOD');
    v_PASS := GET_THRESHOLD('PASS');

    RETURN DETERMINE_LEVEL(i_nSCORE, v_EXCELLENT, v_GOOD, v_PASS);
EXCEPTION
    WHEN OTHERS THEN RETURN 4;
END F_GET_GRADE_LEVEL;
/

-- ============================================================================
-- 4. 基础函数: 计算排名 (嵌套SQL构建)
-- ============================================================================
CREATE OR REPLACE FUNCTION F_CALC_RANK(
    i_nSTUDENT_NO  IN  NUMBER,
    i_nGRADE       IN  NUMBER,
    i_nCLASS_NO    IN  NUMBER,
    i_nRANK_TYPE   IN  NUMBER
) RETURN NUMBER
IS
    -- 嵌套函数: 获取学生总分
    FUNCTION GET_STUDENT_TOTAL(
        i_nSTU IN NUMBER
    ) RETURN NUMBER
    IS
    BEGIN
        RETURN F_GET_TOTAL_SCORE(i_nSTU, NULL);
    EXCEPTION
        WHEN OTHERS THEN RETURN 0;
    END GET_STUDENT_TOTAL;

    -- 嵌套函数: 构建子查询
    FUNCTION BUILD_SUBQUERY(
        i_nGRD IN NUMBER,
        i_nCLS IN NUMBER,
        i_nTYP IN NUMBER
    ) RETURN VARCHAR2
    IS
        -- 再嵌套: 获取分组条件
        FUNCTION GET_GROUP_COND(
            i_nTYP IN NUMBER,
            i_nCLS IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nTYP = 1 THEN
                RETURN 'CLASS_NO = ' || i_nCLS;
            ELSE
                RETURN '1=1';
            END IF;
        EXCEPTION
            WHEN OTHERS THEN RETURN '1=1';
        END GET_GROUP_COND;

        v_RET VARCHAR2(4000);
    BEGIN
        v_RET := 'SELECT S.STUDENT_NO, SUM(NVL(SC.SCORE, 0)) AS TOTAL ' ||
                 'FROM STUDENT_MST S, SCORE_REC SC ' ||
                 'WHERE S.STUDENT_NO = SC.STUDENT_NO(+) ' ||
                 'AND S.GRADE = ' || i_nGRD || ' ' ||
                 'AND ' || GET_GROUP_COND(i_nTYP, i_nCLS) || ' ' ||
                 'AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO ';
        RETURN v_RET;
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END BUILD_SUBQUERY;

    -- 嵌套函数: 构建完整SQL
    FUNCTION BUILD_FULL_SQL(
        i_nSTU IN NUMBER,
        i_nGRD IN NUMBER,
        i_nCLS IN NUMBER,
        i_nTYP IN NUMBER,
        i_nTOT IN NUMBER
    ) RETURN VARCHAR2
    IS
        v_SUB VARCHAR2(4000);
    BEGIN
        v_SUB := BUILD_SUBQUERY(i_nGRD, i_nCLS, i_nTYP);
        IF v_SUB IS NOT NULL THEN
            RETURN 'SELECT COUNT(*) + 1 FROM (' || v_SUB ||
                   'HAVING SUM(NVL(SC.SCORE, 0)) > ' || i_nTOT || ') ';
        END IF;
        RETURN NULL;
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END BUILD_FULL_SQL;

    g_nERR         NUMBER := -1;
    g_nRANK        NUMBER := 1;
    g_nTOTAL_SCORE NUMBER;
    g_vSQL         VARCHAR2(4000);
BEGIN
    g_nTOTAL_SCORE := GET_STUDENT_TOTAL(i_nSTUDENT_NO);
    g_vSQL := BUILD_FULL_SQL(i_nSTUDENT_NO, i_nGRADE, i_nCLASS_NO, i_nRANK_TYPE, g_nTOTAL_SCORE);

    IF g_vSQL IS NOT NULL THEN
        EXECUTE IMMEDIATE g_vSQL INTO g_nRANK;
    END IF;

    RETURN g_nRANK;

EXCEPTION
    WHEN OTHERS THEN
        RETURN 1;
END F_CALC_RANK;
/

-- ============================================================================
-- 5. PIPELINED函数: 返回学生列表 (复杂嵌套)
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_STUDENT_LIST_PIPELINE(
    i_nGRADE    IN  NUMBER DEFAULT NULL,
    i_nCLASS_NO IN  NUMBER DEFAULT NULL
) RETURN STUDENT_LIST PIPELINED
IS
    -- 嵌套子函数: 获取学生信息
    FUNCTION GET_STUDENT_INFO(
        p_STUDENT_NO IN NUMBER
    ) RETURN STUDENT_RECORD
    IS
        -- 再嵌套: 计算总分
        FUNCTION CALC_TOTAL(
            p_NO IN NUMBER
        ) RETURN NUMBER
        IS
        BEGIN
            RETURN F_GET_TOTAL_SCORE(p_NO, NULL);
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END CALC_TOTAL;

        -- 再嵌套: 计算平均分
        FUNCTION CALC_AVG(
            p_NO IN NUMBER
        ) RETURN NUMBER
        IS
        BEGIN
            RETURN F_GET_AVG_SCORE(p_NO, NULL);
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END CALC_AVG;

        -- 再嵌套: 计算排名
        FUNCTION CALC_RANK(
            p_NO IN NUMBER,
            p_GRD IN NUMBER,
            p_CLS IN NUMBER
        ) RETURN NUMBER
        IS
        BEGIN
            RETURN F_CALC_RANK(p_NO, p_GRD, p_CLS, 1);
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END CALC_RANK;

        vTOTAL NUMBER;
        vAVG   NUMBER;
        vRANK  NUMBER;
    BEGIN
        RETURN NULL;
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END GET_STUDENT_INFO;

    -- 嵌套函数: 验证过滤条件
    FUNCTION VALIDATE_FILTER(
        i_nGRD IN NUMBER,
        i_nCLS IN NUMBER
    ) RETURN BOOLEAN
    IS
    BEGIN
        RETURN (i_nGRD IS NULL OR i_nGRD > 0)
           AND (i_nCLS IS NULL OR i_nCLS > 0);
    EXCEPTION
        WHEN OTHERS THEN RETURN FALSE;
    END VALIDATE_FILTER;

    -- 嵌套函数: 构建查询
    FUNCTION BUILD_QUERY(
        i_nGRD IN NUMBER,
        i_nCLS IN NUMBER
    ) RETURN VARCHAR2
    IS
        v_SQL VARCHAR2(4000) := 'SELECT * FROM STUDENT_MST WHERE DELETE_FLG = 0 AND STATUS = 0 ';

        FUNCTION ADD_GRADE_COND(
            i_vSQL IN VARCHAR2,
            i_nGRD IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nGRD IS NOT NULL THEN
                RETURN i_vSQL || 'AND GRADE = ' || i_nGRD || ' ';
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vSQL;
        END ADD_GRADE_COND;

        FUNCTION ADD_CLASS_COND(
            i_vSQL IN VARCHAR2,
            i_nCLS IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nCLS IS NOT NULL THEN
                RETURN i_vSQL || 'AND CLASS_NO = ' || i_nCLS || ' ';
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vSQL;
        END ADD_CLASS_COND;

    BEGIN
        v_SQL := ADD_GRADE_COND(v_SQL, i_nGRD);
        v_SQL := ADD_CLASS_COND(v_SQL, i_nCLS);
        RETURN v_SQL || 'ORDER BY STUDENT_NO';
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END BUILD_QUERY;

    v_cursor SYS_REFCURSOR;
    v_student STUDENT_MST%ROWTYPE;
    v_total NUMBER;
    v_avg NUMBER;
    v_rank NUMBER;
BEGIN
    IF NOT VALIDATE_FILTER(i_nGRADE, i_nCLASS_NO) THEN
        RETURN;
    END IF;

    OPEN v_cursor FOR BUILD_QUERY(i_nGRADE, i_nCLASS_NO);

    LOOP
        FETCH v_cursor INTO v_student;
        EXIT WHEN v_cursor%NOTFOUND;

        v_total := F_GET_TOTAL_SCORE(v_student.STUDENT_NO, NULL);
        v_avg := F_GET_AVG_SCORE(v_student.STUDENT_NO, NULL);
        v_rank := F_CALC_RANK(v_student.STUDENT_NO, v_student.GRADE, v_student.CLASS_NO, 1);

        PIPE ROW(STUDENT_RECORD(
            v_student.STUDENT_NO,
            v_student.STUDENT_NAME,
            v_student.STUDENT_KANA,
            v_student.GENDER,
            v_student.GRADE,
            v_student.CLASS_NO,
            v_student.BIRTH_DATE,
            v_total,
            v_avg,
            v_rank
        ));
    END LOOP;

    CLOSE v_cursor;
    RETURN;

EXCEPTION
    WHEN OTHERS THEN
        IF v_cursor%ISOPEN THEN CLOSE v_cursor; END IF;
        RETURN;
END F_GET_STUDENT_LIST_PIPELINE;
/

-- ============================================================================
-- 6. PIPELINED函数: 返回成绩列表
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_SCORE_LIST_PIPELINE(
    i_nSTUDENT_NO IN NUMBER,
    i_nEXAM_TYPE  IN NUMBER DEFAULT NULL
) RETURN SCORE_LIST PIPELINED
IS
    -- 嵌套函数: 验证学生
    FUNCTION VALIDATE_STUDENT(
        i_nNO IN NUMBER
    ) RETURN BOOLEAN
    IS
        v_CNT NUMBER;
    BEGIN
        SELECT COUNT(*) INTO v_CNT FROM STUDENT_MST
        WHERE STUDENT_NO = i_nNO AND DELETE_FLG = 0;
        RETURN v_CNT > 0;
    EXCEPTION
        WHEN OTHERS THEN RETURN FALSE;
    END VALIDATE_STUDENT;

    -- 嵌套函数: 获取科目名称
    FUNCTION GET_SUBJECT_NAME(
        i_nCD IN NUMBER
    ) RETURN VARCHAR2
    IS
        v_NAME VARCHAR2(100);
    BEGIN
        SELECT SUBJECT_NAME INTO v_NAME FROM SUBJECT_MST
        WHERE SUBJECT_CD = i_nCD AND DELETE_FLG = 0;
        RETURN v_NAME;
    EXCEPTION
        WHEN OTHERS THEN RETURN 'Unknown';
    END GET_SUBJECT_NAME;

    -- 嵌套函数: 格式化内容
    FUNCTION FORMAT_CONTENT(
        i_vNAME IN VARCHAR2,
        i_nSCORE IN NUMBER
    ) RETURN VARCHAR2
    IS
    BEGIN
        RETURN i_vNAME || ':' || NVL(TO_CHAR(i_nSCORE), 'N/A');
    EXCEPTION
        WHEN OTHERS THEN RETURN 'Error';
    END FORMAT_CONTENT;

    -- 嵌套函数: 构建查询
    FUNCTION BUILD_QUERY(
        i_nSTU IN NUMBER,
        i_nTYP IN NUMBER
    ) RETURN VARCHAR2
    IS
        v_SQL VARCHAR2(4000) :=
            'SELECT SC.*, SB.SUBJECT_NAME, SB.PASS_SCORE ' ||
            'FROM SCORE_REC SC, SUBJECT_MST SB ' ||
            'WHERE SC.SUBJECT_CD = SB.SUBJECT_CD ' ||
            'AND SC.STUDENT_NO = ' || i_nSTU;

        FUNCTION ADD_TYPE_COND(
            i_vSQL IN VARCHAR2,
            i_nTYP IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nTYP IS NOT NULL THEN
                RETURN i_vSQL || ' AND SC.EXAM_TYPE = ' || i_nTYP;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vSQL;
        END ADD_TYPE_COND;

    BEGIN
        v_SQL := ADD_TYPE_COND(v_SQL, i_nTYP);
        RETURN v_SQL || ' ORDER BY SC.EXAM_DATE DESC';
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END BUILD_QUERY;

    v_cursor SYS_REFCURSOR;
    v_cnt NUMBER := 0;
    v_subject_name VARCHAR2(100);
BEGIN
    IF NOT VALIDATE_STUDENT(i_nSTUDENT_NO) THEN
        RETURN;
    END IF;

    OPEN v_cursor FOR BUILD_QUERY(i_nSTUDENT_NO, i_nEXAM_TYPE);

    LOOP
        FETCH v_cursor INTO v_cnt;
        EXIT WHEN v_cursor%NOTFOUND;

        v_subject_name := GET_SUBJECT_NAME(v_cnt);

        PIPE ROW(SCORE_RECORD(
            v_cnt,
            FORMAT_CONTENT(v_subject_name, v_cnt),
            CASE WHEN v_cnt > 0 THEN 0 ELSE 2 END,
            i_nSTUDENT_NO,
            v_cnt,
            v_cnt,
            v_cnt,
            CASE WHEN v_cnt > 0 THEN 1 ELSE 0 END
        ));
    END LOOP;

    CLOSE v_cursor;
    RETURN;

EXCEPTION
    WHEN OTHERS THEN
        IF v_cursor%ISOPEN THEN CLOSE v_cursor; END IF;
        RETURN;
END F_GET_SCORE_LIST_PIPELINE;
/

-- ============================================================================
-- 7. 多行返回函数: 使用REF CURSOR
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_STUDENT_CURSOR(
    i_nGRADE    IN  NUMBER DEFAULT NULL,
    i_nCLASS_NO IN  NUMBER DEFAULT NULL
) RETURN SYS_REFCURSOR
IS
    -- 嵌套函数: 构建SQL
    FUNCTION BUILD_SQL(
        i_nGRD IN NUMBER,
        i_nCLS IN NUMBER
    ) RETURN VARCHAR2
    IS
        v_SQL VARCHAR2(4000) :=
            'SELECT S.*, ' ||
            '(SELECT SUM(NVL(SC.SCORE, 0)) FROM SCORE_REC SC WHERE SC.STUDENT_NO = S.STUDENT_NO) AS TOTAL_SCORE ' ||
            'FROM STUDENT_MST S WHERE 1=1 AND S.DELETE_FLG = 0 ';

        FUNCTION ADD_COND(
            i_vSQL IN VARCHAR2,
            i_nGRD IN NUMBER,
            i_nCLS IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            IF i_nGRD IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND S.GRADE = ' || i_nGRD;
            END IF;
            IF i_nCLS IS NOT NULL THEN
                i_vSQL := i_vSQL || ' AND S.CLASS_NO = ' || i_nCLS;
            END IF;
            RETURN i_vSQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vSQL;
        END ADD_COND;

    BEGIN
        RETURN ADD_COND(v_SQL, i_nGRD, i_nCLS) || ' ORDER BY S.STUDENT_NO';
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END BUILD_SQL;

    v_cursor SYS_REFCURSOR;
    v_sql    VARCHAR2(4000);
BEGIN
    v_sql := BUILD_SQL(i_nGRADE, i_nCLASS_NO);

    IF v_sql IS NOT NULL THEN
        OPEN v_cursor FOR v_sql;
    ELSE
        OPEN v_cursor FOR 'SELECT * FROM STUDENT_MST WHERE 1=0';
    END IF;

    RETURN v_cursor;
END F_GET_STUDENT_CURSOR;
/

-- ============================================================================
-- 8. 聚合函数: 计算加权平均
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_WEIGHTED_AVG(
    i_nSTUDENT_NO IN NUMBER
) RETURN NUMBER
IS
    -- 嵌套函数: 获取所有成绩
    FUNCTION GET_ALL_SCORES(
        i_nSTU IN NUMBER
    ) RETURN SYS_REFCURSOR
    IS
        v_cursor SYS_REFCURSOR;
    BEGIN
        OPEN v_cursor FOR
            'SELECT SC.SCORE, SB.CREDIT ' ||
            'FROM SCORE_REC SC, SUBJECT_MST SB ' ||
            'WHERE SC.STUDENT_NO = ' || i_nSTU ||
            ' AND SC.SUBJECT_CD = SB.SUBJECT_CD ' ||
            ' AND SB.DELETE_FLG = 0';
        RETURN v_cursor;
    EXCEPTION
        WHEN OTHERS THEN
            OPEN v_cursor FOR 'SELECT 0 AS SCORE, 0 AS CREDIT FROM DUAL WHERE 1=0';
            RETURN v_cursor;
    END GET_ALL_SCORES;

    -- 嵌套函数: 计算加权和
    FUNCTION CALC_WEIGHTED_SUM(
        i_cursor IN OUT SYS_REFCURSOR
    ) RETURN NUMBER
    IS
        v_WEIGHTED_SUM NUMBER := 0;
        v_SCORE NUMBER;
        v_CREDIT NUMBER;
    BEGIN
        LOOP
            FETCH i_cursor INTO v_SCORE, v_CREDIT;
            EXIT WHEN i_cursor%NOTFOUND;
            v_WEIGHTED_SUM := v_WEIGHTED_SUM + (NVL(v_SCORE, 0) * NVL(v_CREDIT, 1));
        END LOOP;
        RETURN v_WEIGHTED_SUM;
    EXCEPTION
        WHEN OTHERS THEN RETURN 0;
    END CALC_WEIGHTED_SUM;

    -- 嵌套函数: 计算总权重
    FUNCTION CALC_TOTAL_WEIGHT(
        i_cursor IN OUT SYS_REFCURSOR
    ) RETURN NUMBER
    IS
        v_TOTAL_WEIGHT NUMBER := 0;
        v_CREDIT NUMBER;
    BEGIN
        LOOP
            FETCH i_cursor INTO v_CREDIT;
            EXIT WHEN i_cursor%NOTFOUND;
            v_TOTAL_WEIGHT := v_TOTAL_WEIGHT + NVL(v_CREDIT, 1);
        END LOOP;
        RETURN v_TOTAL_WEIGHT;
    EXCEPTION
        WHEN OTHERS THEN RETURN 0;
    END CALC_TOTAL_WEIGHT;

    v_WEIGHTED_SUM NUMBER;
    v_TOTAL_WEIGHT NUMBER;
    v_AVG NUMBER := 0;
    v_cursor SYS_REFCURSOR;
BEGIN
    v_cursor := GET_ALL_SCORES(i_nSTUDENT_NO);
    v_WEIGHTED_SUM := CALC_WEIGHTED_SUM(v_cursor);
    CLOSE v_cursor;

    v_cursor := GET_ALL_SCORES(i_nSTUDENT_NO);
    v_TOTAL_WEIGHT := CALC_TOTAL_WEIGHT(v_cursor);
    CLOSE v_cursor;

    IF v_TOTAL_WEIGHT > 0 THEN
        v_AVG := ROUND(v_WEIGHTED_SUM / v_TOTAL_WEIGHT, 2);
    END IF;

    RETURN v_AVG;
EXCEPTION
    WHEN OTHERS THEN RETURN 0;
END F_GET_WEIGHTED_AVG;
/

-- ============================================================================
-- 9. 配置获取函数 (嵌套配置处理)
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_CONFIG_VALUE(
    i_sCONFIG_CD IN NVARCHAR2
) RETURN NVARCHAR2
IS
    -- 嵌套函数: 验证配置代码
    FUNCTION VALIDATE_CD(
        i_sCD IN NVARCHAR2
    ) RETURN BOOLEAN
    IS
        v_LEN NUMBER;
    BEGIN
        v_LEN := LENGTH(i_sCD);
        RETURN v_LEN IS NOT NULL AND v_LEN > 0 AND v_LEN < 100;
    EXCEPTION
        WHEN OTHERS THEN RETURN FALSE;
    END VALIDATE_CD;

    -- 嵌套函数: 清理配置值
    FUNCTION CLEAN_VALUE(
        i_vRAW IN NVARCHAR2
    ) RETURN NVARCHAR2
    IS
        -- 再嵌套: 去除空格
        FUNCTION TRIM_VALUE(
            i_vIN IN NVARCHAR2
        ) RETURN NVARCHAR2
        IS
        BEGIN
            RETURN TRIM(i_vIN);
        EXCEPTION
            WHEN OTHERS THEN RETURN i_vIN;
        END TRIM_VALUE;

    BEGIN
        RETURN TRIM_VALUE(i_vRAW);
    EXCEPTION
        WHEN OTHERS THEN RETURN NULL;
    END CLEAN_VALUE;

    v_VALUE SYSTEM_CONFIG.CONFIG_VALUE%TYPE;
BEGIN
    IF NOT VALIDATE_CD(i_sCONFIG_CD) THEN
        RETURN NULL;
    END IF;

    SELECT CONFIG_VALUE INTO v_VALUE
    FROM SYSTEM_CONFIG
    WHERE CONFIG_CD = i_sCONFIG_CD AND DELETE_FLG = 0;

    RETURN CLEAN_VALUE(v_VALUE);

EXCEPTION
    WHEN NO_DATA_FOUND THEN
        RETURN NULL;
    WHEN OTHERS THEN
        RETURN NULL;
END F_GET_CONFIG_VALUE;
/

-- ============================================================================
-- 10. 批次号生成函数
-- ============================================================================
CREATE OR REPLACE FUNCTION F_GET_JOBNUM RETURN NUMBER
IS
    v_JOBNUM NUMBER;
BEGIN
    SELECT SEQ_JOBNUM.NEXTVAL INTO v_JOBNUM FROM DUAL;
    RETURN v_JOBNUM;
EXCEPTION
    WHEN OTHERS THEN RETURN 1;
END F_GET_JOBNUM;
/
