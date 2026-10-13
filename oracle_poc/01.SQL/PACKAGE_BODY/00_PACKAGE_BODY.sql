-- ============================================================================
-- 包主体: PKG_SCORE_MANAGE - 成绩管理完整包
-- 版本: 1.0.200.000
-- ============================================================================
CREATE OR REPLACE PACKAGE BODY PKG_SCORE_MANAGE
--1.0.200.000-02.02-02.02
IS
    ------------------------------------------------------------------------
    -- 全局变量
    ------------------------------------------------------------------------
    g_nJOBNUM              NUMBER;
    g_sWSNUM               NVARCHAR2(63);
    g_sTANTO_CD            CHAR(12);
    g_sSTARTDATE           NVARCHAR2(23);
    g_sMESSAGE             NVARCHAR2(4000);
    g_nPASS_SCORE          NUMBER := 60;
    g_nEXCELLENT_SCORE     NUMBER := 90;
    g_nGOOD_SCORE          NUMBER := 80;
    g_nMEDIUM_SCORE        NUMBER := 70;

    ------------------------------------------------------------------------
    -- 游标定义
    ------------------------------------------------------------------------
    CURSOR GAKUREIBO(p_STUDENT_NO IN NUMBER) IS
        SELECT
            S.STUDENT_NO,
            S.STUDENT_NAME,
            S.STUDENT_KANA,
            S.GENDER,
            S.GRADE,
            S.CLASS_NO,
            NVL(Sum(SC.SCORE), 0) AS TOTAL_SCORE,
            ROUND(NVL(AVG(SC.SCORE), 0), 2) AS AVG_SCORE,
            COUNT(SC.STUDENT_NO) AS EXAM_COUNT,
            SUM(CASE WHEN NVL(SC.IS_PASS, 0) = 1 THEN 1 ELSE 0 END) AS PASS_COUNT,
            SUM(CASE WHEN NVL(SC.IS_PASS, 0) = 0 THEN 1 ELSE 0 END) AS FAIL_COUNT
        FROM
            STUDENT_MST S, SCORE_REC SC
        WHERE
            S.STUDENT_NO = SC.STUDENT_NO(+)
            AND S.STUDENT_NO = p_STUDENT_NO
            AND S.DELETE_FLG = 0
        GROUP BY
            S.STUDENT_NO, S.STUDENT_NAME, S.STUDENT_KANA, S.GENDER, S.GRADE, S.CLASS_NO;

    CURSOR CUR_ALL_STUDENT(p_GRADE IN NUMBER, p_CLASS IN NUMBER) IS
        SELECT * FROM STUDENT_MST
        WHERE DELETE_FLG = 0 AND STATUS = 0
        AND (p_GRADE IS NULL OR GRADE = p_GRADE)
        AND (p_CLASS IS NULL OR CLASS_NO = p_CLASS)
        ORDER BY STUDENT_NO;

    CURSOR CUR_ALL_SCORE(p_STUDENT IN NUMBER) IS
        SELECT SC.*, SB.SUBJECT_NAME, SB.PASS_SCORE, SB.CREDIT
        FROM SCORE_REC SC, SUBJECT_MST SB
        WHERE SC.SUBJECT_CD = SB.SUBJECT_CD
        AND SC.STUDENT_NO = p_STUDENT
        ORDER BY SC.EXAM_DATE DESC;

    CURSOR CUR_SUBJECT_STATS(p_GRADE IN NUMBER) IS
        SELECT SB.SUBJECT_CD, SB.SUBJECT_NAME, SB.CREDIT, SB.SUBJECT_TYPE,
               COUNT(SC.STUDENT_NO) AS STU_CNT,
               ROUND(AVG(SC.SCORE), 2) AS AVG_SCORE,
               MAX(SC.SCORE) AS MAX_SCORE,
               MIN(SC.SCORE) AS MIN_SCORE
        FROM SUBJECT_MST SB, SCORE_REC SC, STUDENT_MST S
        WHERE SB.SUBJECT_CD = SC.SUBJECT_CD(+)
        AND S.STUDENT_NO = SC.STUDENT_NO(+)
        AND SB.DELETE_FLG = 0
        AND (p_GRADE IS NULL OR S.GRADE = p_GRADE)
        GROUP BY SB.SUBJECT_CD, SB.SUBJECT_NAME, SB.CREDIT, SB.SUBJECT_TYPE;

    ------------------------------------------------------------------------
    -- 类型定义 (包内类型)
    ------------------------------------------------------------------------
    TYPE t_score_rec IS RECORD (
        STUDENT_NO      NUMBER(10,0),
        SUBJECT_CD      NUMBER(5,0),
        EXAM_DATE       NUMBER(8,0),
        EXAM_TYPE       NUMBER(1,0),
        SCORE           NUMBER(5,2),
        GRADE_LEVEL     NUMBER(1,0),
        IS_PASS         NUMBER(1,0)
    );
    TYPE t_score_list IS TABLE OF t_score_rec INDEX BY PLS_INTEGER;
    TYPE t_student_list IS TABLE OF STUDENT_MST%ROWTYPE INDEX BY PLS_INTEGER;
    TYPE t_config_list IS TABLE OF SYSTEM_CONFIG%ROWTYPE INDEX BY PLS_INTEGER;

    ------------------------------------------------------------------------
    -- 子程序1: 日志输出 (含嵌套的格式化函数)
    ------------------------------------------------------------------------
    FUNCTION FUNC_SETLOG(
        i_sSTEP_NANE IN  NVARCHAR2,
        i_nDEBUG_KBN IN  NUMBER,
        i_nSTATUSID  IN  NUMBER,
        i_sSQLCODE   IN  NVARCHAR2,
        i_sSQLERRM   IN  NVARCHAR2,
        i_sERRMSG    IN  NVARCHAR2
    ) RETURN BOOLEAN
    IS
        PRAGMA AUTONOMOUS_TRANSACTION;
        v_LOG_ID NUMBER;

        -- 嵌套函数: 格式化日志消息
        FUNCTION FORMAT_MESSAGE(
            i_sSTEP IN NVARCHAR2,
            i_sMSG  IN NVARCHAR2,
            i_nSTAT IN NUMBER
        ) RETURN VARCHAR2
        IS
            v_RESULT VARCHAR2(4000);
            v_PREFIX VARCHAR2(20);
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
        END FORMAT_MESSAGE;

    BEGIN
        SELECT SEQ_LOG_ID.NEXTVAL INTO v_LOG_ID FROM DUAL;
        INSERT INTO PROCESS_LOG (
            LOG_ID, JOBNUM, PROCESS_NAME, STEP_NO, LOG_LEVEL,
            LOG_MESSAGE, SQLCODE, SQLERRM, TANTO_CD, WS_NUM, LOG_DATE
        ) VALUES (
            v_LOG_ID, g_nJOBNUM, 'PKG_SCORE_MANAGE', 0,
            i_nSTATUSID, i_sERRMSG, i_sSQLCODE, i_sSQLERRM,
            g_sTANTO_CD, g_sWSNUM, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3')
        );
        COMMIT;
        DBMS_OUTPUT.PUT_LINE(FORMAT_MESSAGE(i_sSTEP_NANE, i_sERRMSG, i_nSTATUSID));
        RETURN TRUE;
    EXCEPTION
        WHEN OTHERS THEN
            COMMIT;
            RETURN FALSE;
    END FUNC_SETLOG;

    ------------------------------------------------------------------------
    -- 子程序2: 初始化 (含嵌套配置加载)
    ------------------------------------------------------------------------
    FUNCTION FUNC_INIT(
        i_sWSNUM     IN  NVARCHAR2,
        i_sTANTO_CD  IN  CHAR
    ) RETURN BOOLEAN
    IS
        -- 嵌套函数: 初始化批次号
        FUNCTION INIT_JOBNUM RETURN NUMBER
        IS
            v_MAX_JOBNUM NUMBER;
            v_NEW_JOBNUM NUMBER;
        BEGIN
            SELECT NVL(MAX(JOBNUM), 0) INTO v_MAX_JOBNUM FROM WK_SCORE_REPORT;
            -- 嵌套函数: 生成新批次号
            FUNCTION GEN_NEW_JOBNUM(i_nMAX IN NUMBER) RETURN NUMBER
            IS
            BEGIN
                RETURN i_nMAX + 1;
            END GEN_NEW_JOBNUM;
            v_NEW_JOBNUM := GEN_NEW_JOBNUM(v_MAX_JOBNUM);
            RETURN v_NEW_JOBNUM;
        EXCEPTION
            WHEN OTHERS THEN RETURN 1;
        END INIT_JOBNUM;

        -- 嵌套函数: 初始化工作站信息
        FUNCTION INIT_WSINFO(
            i_sWS IN NVARCHAR2,
            i_sTC IN CHAR
        ) RETURN BOOLEAN
        IS
        BEGIN
            g_sWSNUM := i_sWS;
            g_sTANTO_CD := i_sTC;
            RETURN TRUE;
        EXCEPTION
            WHEN OTHERS THEN RETURN FALSE;
        END INIT_WSINFO;

    BEGIN
        g_sSTARTDATE := TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3');
        g_nJOBNUM := INIT_JOBNUM;
        RETURN INIT_WSINFO(i_sWSNUM, i_sTANTO_CD);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN FALSE;
    END FUNC_INIT;

    ------------------------------------------------------------------------
    -- 子程序3: 获取配置值 (多层嵌套)
    ------------------------------------------------------------------------
    FUNCTION FUNC_GET_CONFIG(
        i_sCONFIG_CD   IN  NVARCHAR2,
        o_nVALUE       OUT NUMBER
    ) RETURN NUMBER
    IS
        -- 嵌套函数: 验证配置代码
        FUNCTION VALIDATE_CONFIG_CD(
            i_sCD IN NVARCHAR2
        ) RETURN BOOLEAN
        IS
            v_CNT NUMBER;
        BEGIN
            SELECT COUNT(*) INTO v_CNT FROM SYSTEM_CONFIG
            WHERE CONFIG_CD = i_sCD;
            RETURN v_CNT > 0;
        EXCEPTION
            WHEN OTHERS THEN RETURN FALSE;
        END VALIDATE_CONFIG_CD;

        -- 嵌套函数: 转换配置值类型
        FUNCTION CONVERT_VALUE(
            i_vRAW IN NVARCHAR2
        ) RETURN NUMBER
        IS
            -- 再嵌套: 清理数值格式
            FUNCTION CLEAN_NUMERIC(i_vIN IN NVARCHAR2) RETURN VARCHAR2
            IS
                v_OUT VARCHAR2(100);
            BEGIN
                v_OUT := REPLACE(REPLACE(i_vIN, ' ', ''), ',', '');
                RETURN v_OUT;
            EXCEPTION
                WHEN OTHERS THEN RETURN i_vIN;
            END CLEAN_NUMERIC;
        BEGIN
            RETURN TO_NUMBER(CLEAN_NUMERIC(i_vRAW));
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END CONVERT_VALUE;

        v_VALUE SYSTEM_CONFIG.CONFIG_VALUE%TYPE;
        v_IS_VALID BOOLEAN;
    BEGIN
        -- 验证配置代码
        v_IS_VALID := VALIDATE_CONFIG_CD(i_sCONFIG_CD);
        IF NOT v_IS_VALID THEN
            o_nVALUE := 0;
            RETURN -1;
        END IF;

        SELECT CONFIG_VALUE INTO v_VALUE FROM SYSTEM_CONFIG
        WHERE CONFIG_CD = i_sCONFIG_CD AND DELETE_FLG = 0;

        o_nVALUE := CONVERT_VALUE(v_VALUE);
        RETURN 0;
    EXCEPTION
        WHEN OTHERS THEN
            o_nVALUE := 0;
            RETURN -1;
    END FUNC_GET_CONFIG;

    ------------------------------------------------------------------------
    -- 子程序4: 加载配置 (含嵌套验证)
    ------------------------------------------------------------------------
    PROCEDURE PROC_LOAD_CONFIG
    IS
        -- 嵌套函数: 验证配置值
        FUNCTION VALIDATE_SCORE_VALUE(
            i_nVALUE IN OUT NUMBER,
            i_nDEFAULT IN NUMBER,
            i_sNAME IN VARCHAR2
        ) RETURN BOOLEAN
        IS
            -- 再嵌套: 检查数值范围
            FUNCTION CHECK_RANGE(
                i_nVAL IN NUMBER,
                i_nMIN IN NUMBER,
                i_nMAX IN NUMBER
            ) RETURN BOOLEAN
            IS
            BEGIN
                RETURN i_nVAL >= i_nMIN AND i_nVAL <= i_nMAX;
            EXCEPTION
                WHEN OTHERS THEN RETURN FALSE;
            END CHECK_RANGE;
        BEGIN
            IF i_nVALUE IS NULL OR i_nVALUE <= 0 THEN
                i_nVALUE := i_nDEFAULT;
            END IF;
            IF NOT CHECK_RANGE(i_nVALUE, 0, 100) THEN
                i_nVALUE := i_nDEFAULT;
            END IF;
            RETURN TRUE;
        EXCEPTION
            WHEN OTHERS THEN
                i_nVALUE := i_nDEFAULT;
                RETURN FALSE;
        END VALIDATE_SCORE_VALUE;

        nRTN NUMBER;
        nTMP NUMBER;
    BEGIN
        nRTN := FUNC_GET_CONFIG('PASS_SCORE_DEFAULT', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 60, 'PASS_SCORE') THEN
            g_nPASS_SCORE := nTMP;
        END IF;

        nRTN := FUNC_GET_CONFIG('GRADE_LEVEL_EXCELLENT', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 90, 'EXCELLENT') THEN
            g_nEXCELLENT_SCORE := nTMP;
        END IF;

        nRTN := FUNC_GET_CONFIG('GRADE_LEVEL_GOOD', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 80, 'GOOD') THEN
            g_nGOOD_SCORE := nTMP;
        END IF;

        nRTN := FUNC_GET_CONFIG('GRADE_LEVEL_PASS', nTMP);
        IF VALIDATE_SCORE_VALUE(nTMP, 70, 'MEDIUM') THEN
            g_nMEDIUM_SCORE := nTMP;
        END IF;
    EXCEPTION
        WHEN OTHERS THEN
            g_nPASS_SCORE := 60;
            g_nEXCELLENT_SCORE := 90;
            g_nGOOD_SCORE := 80;
            g_nMEDIUM_SCORE := 70;
    END PROC_LOAD_CONFIG;

    ------------------------------------------------------------------------
    -- 子程序5: 格式化日期 (嵌套日期处理)
    ------------------------------------------------------------------------
    FUNCTION FUNC_FORMAT_DATE(
        i_YMD IN NUMBER
    ) RETURN VARCHAR2
    IS
        -- 嵌套函数: 验证日期格式
        FUNCTION VALIDATE_YMD(
            i_nYMD IN NUMBER
        ) RETURN BOOLEAN
        IS
            v_Y VARCHAR2(4);
            v_M VARCHAR2(2);
            v_D VARCHAR2(2);
        BEGIN
            IF i_nYMD IS NULL OR i_nYMD < 19000101 OR i_nYMD > 21001231 THEN
                RETURN FALSE;
            END IF;
            v_Y := SUBSTR(TO_CHAR(i_nYMD), 1, 4);
            v_M := SUBSTR(TO_CHAR(i_nYMD), 5, 2);
            v_D := SUBSTR(TO_CHAR(i_nYMD), 7, 2);
            RETURN TO_NUMBER(v_M) BETWEEN 1 AND 12 AND TO_NUMBER(v_D) BETWEEN 1 AND 31;
        EXCEPTION
            WHEN OTHERS THEN RETURN FALSE;
        END VALIDATE_YMD;

        -- 嵌套函数: 转换日期格式
        FUNCTION CONVERT_FORMAT(
            i_nYMD IN NUMBER,
            i_sFMT IN VARCHAR2
        ) RETURN VARCHAR2
        IS
            -- 再嵌套: 解析日期组件
            FUNCTION PARSE_YMD(
                i_nYMD IN NUMBER,
                o_nY OUT NUMBER,
                o_nM OUT NUMBER,
                o_nD OUT NUMBER
            ) RETURN BOOLEAN
            IS
            BEGIN
                o_nY := TO_NUMBER(SUBSTR(TO_CHAR(i_nYMD), 1, 4));
                o_nM := TO_NUMBER(SUBSTR(TO_CHAR(i_nYMD), 5, 2));
                o_nD := TO_NUMBER(SUBSTR(TO_CHAR(i_nYMD), 7, 2));
                RETURN TRUE;
            EXCEPTION
                WHEN OTHERS THEN RETURN FALSE;
            END PARSE_YMD;

            v_Y NUMBER; v_M NUMBER; v_D NUMBER;
        BEGIN
            IF PARSE_YMD(i_nYMD, v_Y, v_M, v_D) THEN
                RETURN TO_CHAR(TO_DATE(i_nYMD, 'YYYYMMDD'), i_sFMT);
            END IF;
            RETURN ' ';
        EXCEPTION
            WHEN OTHERS THEN RETURN ' ';
        END CONVERT_FORMAT;

    BEGIN
        IF i_YMD IS NOT NULL AND i_YMD > 0 AND VALIDATE_YMD(i_YMD) THEN
            RETURN CONVERT_FORMAT(i_YMD, 'YYYY/MM/DD');
        END IF;
        RETURN ' ';
    EXCEPTION
        WHEN OTHERS THEN
            RETURN ' ';
    END FUNC_FORMAT_DATE;

    ------------------------------------------------------------------------
    -- 子程序6: 计算等级 (多层嵌套阈值获取)
    ------------------------------------------------------------------------
    FUNCTION FUNC_GET_GRADE_LEVEL(
        i_nSCORE IN NUMBER
    ) RETURN NUMBER
    IS
        -- 嵌套函数: 获取分数线
        FUNCTION GET_THRESHOLD(
            i_sKBN IN VARCHAR2
        ) RETURN NUMBER
        IS
            -- 再嵌套: 从缓存获取配置
            FUNCTION GET_FROM_CACHE(
                i_sKEY IN VARCHAR2
            ) RETURN NUMBER
            IS
            BEGIN
                IF i_sKEY = 'EXCELLENT' THEN RETURN g_nEXCELLENT_SCORE;
                ELSIF i_sKEY = 'GOOD' THEN RETURN g_nGOOD_SCORE;
                ELSIF i_sKEY = 'PASS' THEN RETURN g_nMEDIUM_SCORE;
                ELSE RETURN 60;
                END IF;
            EXCEPTION
                WHEN OTHERS THEN RETURN 60;
            END GET_FROM_CACHE;

            -- 再嵌套: 从数据库获取
            FUNCTION GET_FROM_DB(
                i_sCD IN VARCHAR2
            ) RETURN NUMBER
            IS
                v_VALUE NUMBER;
            BEGIN
                SELECT NVL(TO_NUMBER(CONFIG_VALUE), 0)
                INTO v_VALUE
                FROM SYSTEM_CONFIG
                WHERE CONFIG_CD = 'GRADE_LEVEL_' || i_sCD
                AND DELETE_FLG = 0;
                RETURN v_VALUE;
            EXCEPTION
                WHEN OTHERS THEN RETURN 0;
            END GET_FROM_DB;

        BEGIN
            -- 优先从缓存获取
            RETURN GET_FROM_CACHE(i_sKBN);
        EXCEPTION
            WHEN OTHERS THEN
                RETURN GET_FROM_DB(i_sKBN);
        END GET_THRESHOLD;

        -- 嵌套函数: 比较分数与阈值
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

        v_EXCELLENT NUMBER := 90;
        v_GOOD      NUMBER := 80;
        v_PASS      NUMBER := 70;
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
        WHEN OTHERS THEN RETURN 4;
    END FUNC_GET_GRADE_LEVEL;

    ------------------------------------------------------------------------
    -- 子程序7: 计算总分 (嵌套验证)
    ------------------------------------------------------------------------
    FUNCTION FUNC_CALC_TOTAL(
        i_nSTUDENT_NO  IN  NUMBER,
        i_nEXAM_TYPE   IN  NUMBER DEFAULT NULL
    ) RETURN NUMBER
    IS
        -- 嵌套函数: 验证学生编号
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

        -- 嵌套函数: 构建查询条件
        FUNCTION BUILD_WHERE_CLAUSE(
            i_nSTU IN NUMBER,
            i_nEXM IN NUMBER
        ) RETURN VARCHAR2
        IS
            v_CLAUSE VARCHAR2(500) := ' WHERE 1=1 ';
        BEGIN
            IF i_nSTU IS NOT NULL AND i_nSTU > 0 THEN
                v_CLAUSE := v_CLAUSE || ' AND STUDENT_NO = ' || i_nSTU;
            END IF;
            IF i_nEXM IS NOT NULL THEN
                v_CLAUSE := v_CLAUSE || ' AND EXAM_TYPE = ' || i_nEXM;
            END IF;
            RETURN v_CLAUSE;
        EXCEPTION
            WHEN OTHERS THEN RETURN ' WHERE 1=1 ';
        END BUILD_WHERE_CLAUSE;

        nTOTAL  NUMBER := 0;
        v_SQL   VARCHAR2(1000);
    BEGIN
        IF NOT VALIDATE_STUDENT(i_nSTUDENT_NO) THEN
            RETURN 0;
        END IF;

        v_SQL := 'SELECT NVL(SUM(SCORE), 0) FROM SCORE_REC' || BUILD_WHERE_CLAUSE(i_nSTUDENT_NO, i_nEXAM_TYPE);
        EXECUTE IMMEDIATE v_SQL INTO nTOTAL;

        RETURN nTOTAL;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN 0;
    END FUNC_CALC_TOTAL;

    ------------------------------------------------------------------------
    -- 子程序8: 计算平均分 (嵌套统计)
    ------------------------------------------------------------------------
    FUNCTION FUNC_CALC_AVG(
        i_nSTUDENT_NO  IN  NUMBER,
        i_nSUBJECT_CD  IN  NUMBER DEFAULT NULL
    ) RETURN NUMBER
    IS
        -- 嵌套函数: 获取科目信息
        FUNCTION GET_SUBJECT_INFO(
            i_nCD IN NUMBER,
            o_sNAME OUT VARCHAR2,
            o_nCREDIT OUT NUMBER
        ) RETURN BOOLEAN
        IS
        BEGIN
            SELECT SUBJECT_NAME, CREDIT
            INTO o_sNAME, o_nCREDIT
            FROM SUBJECT_MST
            WHERE SUBJECT_CD = i_nCD AND DELETE_FLG = 0;
            RETURN TRUE;
        EXCEPTION
            WHEN OTHERS THEN
                o_sNAME := NULL;
                o_nCREDIT := 0;
                RETURN FALSE;
        END GET_SUBJECT_INFO;

        -- 嵌套函数: 计算加权平均
        FUNCTION CALC_WEIGHTED_AVG(
            i_nSTU IN NUMBER
        ) RETURN NUMBER
        IS
            v_WEIGHTED_SUM NUMBER := 0;
            v_TOTAL_WEIGHT NUMBER := 0;
            v_AVG NUMBER := 0;
        BEGIN
            FOR rec IN (
                SELECT SC.SCORE, SB.CREDIT
                FROM SCORE_REC SC, SUBJECT_MST SB
                WHERE SC.STUDENT_NO = i_nSTU
                AND SC.SUBJECT_CD = SB.SUBJECT_CD
                AND SB.DELETE_FLG = 0
            ) LOOP
                v_WEIGHTED_SUM := v_WEIGHTED_SUM + (rec.SCORE * rec.CREDIT);
                v_TOTAL_WEIGHT := v_TOTAL_WEIGHT + rec.CREDIT;
            END LOOP;
            IF v_TOTAL_WEIGHT > 0 THEN
                v_AVG := ROUND(v_WEIGHTED_SUM / v_TOTAL_WEIGHT, 2);
            END IF;
            RETURN v_AVG;
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END CALC_WEIGHTED_AVG;

        nAVG  NUMBER := 0;
        v_SUBJECT_NAME VARCHAR2(100);
        n_CREDIT NUMBER;
    BEGIN
        -- 如果指定了科目，使用简单平均
        IF i_nSUBJECT_CD IS NOT NULL THEN
            IF GET_SUBJECT_INFO(i_nSUBJECT_CD, v_SUBJECT_NAME, n_CREDIT) THEN
                SELECT ROUND(NVL(AVG(SCORE), 0), 2)
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
            RETURN 0;
    END FUNC_CALC_AVG;

    ------------------------------------------------------------------------
    -- 子程序9: 计算排名 (嵌套分组统计)
    ------------------------------------------------------------------------
    FUNCTION FUNC_CALC_RANK(
        i_nSTUDENT_NO  IN  NUMBER,
        i_nGRADE       IN  NUMBER,
        i_nCLASS_NO    IN  NUMBER,
        i_nRANK_TYPE   IN  NUMBER
    ) RETURN NUMBER
    IS
        -- 嵌套函数: 获取学生总分
        FUNCTION GET_TOTAL_SCORE(
            i_nSTU IN NUMBER
        ) RETURN NUMBER
        IS
        BEGIN
            RETURN FUNC_CALC_TOTAL(i_nSTU, NULL);
        EXCEPTION
            WHEN OTHERS THEN RETURN 0;
        END GET_TOTAL_SCORE;

        -- 嵌套函数: 构建排名SQL
        FUNCTION BUILD_RANK_SQL(
            i_nSTU IN NUMBER,
            i_nGRD IN NUMBER,
            i_nCLS IN NUMBER,
            i_nTYP IN NUMBER,
            i_nTOT IN NUMBER
        ) RETURN VARCHAR2
        IS
            -- 再嵌套: 获取分组字段
            FUNCTION GET_GROUP_BY(p_nTYP IN NUMBER) RETURN VARCHAR2
            IS
            BEGIN
                IF p_nTYP = 1 THEN
                    RETURN 'CLASS_NO = ' || i_nCLS;
                ELSE
                    RETURN '1=1';
                END IF;
            EXCEPTION
                WHEN OTHERS THEN RETURN '1=1';
            END GET_GROUP_BY;

            v_SQL VARCHAR2(4000);
        BEGIN
            IF i_nTYP = 1 THEN
                v_SQL := 'SELECT COUNT(*) + 1 FROM ( ' ||
                         'SELECT S.STUDENT_NO, SUM(NVL(SC.SCORE, 0)) AS TOTAL ' ||
                         'FROM STUDENT_MST S, SCORE_REC SC ' ||
                         'WHERE S.STUDENT_NO = SC.STUDENT_NO(+) ' ||
                         'AND S.GRADE = ' || i_nGRD || ' ' ||
                         'AND ' || GET_GROUP_BY(1) || ' ' ||
                         'AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO ' ||
                         'HAVING SUM(NVL(SC.SCORE, 0)) > ' || i_nTOT || ') ';
            ELSE
                v_SQL := 'SELECT COUNT(*) + 1 FROM ( ' ||
                         'SELECT S.STUDENT_NO, SUM(NVL(SC.SCORE, 0)) AS TOTAL ' ||
                         'FROM STUDENT_MST S, SCORE_REC SC ' ||
                         'WHERE S.STUDENT_NO = SC.STUDENT_NO(+) ' ||
                         'AND S.GRADE = ' || i_nGRD || ' ' ||
                         'AND ' || GET_GROUP_BY(2) || ' ' ||
                         'AND S.DELETE_FLG = 0 GROUP BY S.STUDENT_NO ' ||
                         'HAVING SUM(NVL(SC.SCORE, 0)) > ' || i_nTOT || ') ';
            END IF;
            RETURN v_SQL;
        EXCEPTION
            WHEN OTHERS THEN RETURN NULL;
        END BUILD_RANK_SQL;

        nTOTAL_SCORE   NUMBER;
        nRANK          NUMBER := 1;
        v_SQL          VARCHAR2(4000);
    BEGIN
        nTOTAL_SCORE := GET_TOTAL_SCORE(i_nSTUDENT_NO);
        v_SQL := BUILD_RANK_SQL(i_nSTUDENT_NO, i_nGRADE, i_nCLASS_NO, i_nRANK_TYPE, nTOTAL_SCORE);

        IF v_SQL IS NOT NULL THEN
            EXECUTE IMMEDIATE v_SQL INTO nRANK;
        END IF;

        RETURN nRANK;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN 1;
    END FUNC_CALC_RANK;

    ------------------------------------------------------------------------
    -- 子程序10: 清理工作表
    ------------------------------------------------------------------------
    PROCEDURE PROC_CLEAR_WORKTABLE(
        i_sWSNUM IN NVARCHAR2
    )
    IS
    BEGIN
        DELETE FROM WK_SCORE_REPORT WHERE WSNUM = i_sWSNUM;
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END PROC_CLEAR_WORKTABLE;

    ------------------------------------------------------------------------
    -- 子程序11: 插入工作表 (嵌套验证)
    ------------------------------------------------------------------------
    PROCEDURE PROC_INSERT_WORK(
        i_sWSNUM         IN  NVARCHAR2,
        i_nSTUDENT_NO    IN  NUMBER,
        i_vSTUDENT_NAME  IN  NVARCHAR2,
        i_nTOTAL_SCORE   IN  NUMBER,
        i_nAVG_SCORE     IN  NUMBER,
        i_nPASS_COUNT    IN  NUMBER,
        i_nFAIL_COUNT    IN  NUMBER
    )
    IS
        -- 嵌套函数: 验证数据
        FUNCTION VALIDATE_DATA(
            i_nNO IN NUMBER,
            i_nTOT IN NUMBER,
            i_nAVG IN NUMBER
        ) RETURN BOOLEAN
        IS
        BEGIN
            RETURN i_nNO IS NOT NULL AND i_nNO > 0;
        EXCEPTION
            WHEN OTHERS THEN RETURN FALSE;
        END VALIDATE_DATA;

        -- 嵌套函数: 生成行号
        FUNCTION GENERATE_ROWNO(
            i_sWS IN NVARCHAR2
        ) RETURN NUMBER
        IS
            v_MAX ROW_NO%TYPE;
        BEGIN
            SELECT NVL(MAX(ROW_NO), 0) + 1 INTO v_MAX
            FROM WK_SCORE_REPORT WHERE WSNUM = i_sWS;
            RETURN v_MAX;
        EXCEPTION
            WHEN OTHERS THEN RETURN 1;
        END GENERATE_ROWNO;

        nROWNO  NUMBER;
    BEGIN
        IF NOT VALIDATE_DATA(i_nSTUDENT_NO, i_nTOTAL_SCORE, i_nAVG_SCORE) THEN
            RETURN;
        END IF;

        nROWNO := GENERATE_ROWNO(i_sWSNUM);

        INSERT INTO WK_SCORE_REPORT (
            ROW_NO, NO, STUDENT_NO, STUDENT_NAME,
            TOTAL_SCORE, AVERAGE_SCORE, PASS_COUNT, FAIL_COUNT,
            WSNUM, BATCHKBN, JOBNUM
        ) VALUES (
            nROWNO, nROWNO, i_nSTUDENT_NO, i_vSTUDENT_NAME,
            i_nTOTAL_SCORE, i_nAVG_SCORE, i_nPASS_COUNT, i_nFAIL_COUNT,
            i_sWSNUM, c_ONLINE, g_nJOBNUM
        );
    EXCEPTION
        WHEN OTHERS THEN
            FUNC_SETLOG('PROC_INSERT_WORK', 0, 1, SQLCODE, SQLERRM, '插入工作表失败');
    END PROC_INSERT_WORK;

    ------------------------------------------------------------------------
    -- 子程序12: 处理单个学生成绩 (多层嵌套统计)
    ------------------------------------------------------------------------
    PROCEDURE PROC_PROCESS_STUDENT(
        i_nSTUDENT_NO    IN  NUMBER,
        i_vSTUDENT_NAME  IN  NVARCHAR2,
        i_nGRADE         IN  NUMBER,
        i_nCLASS_NO      IN  NUMBER,
        i_sWSNUM         IN  NVARCHAR2,
        o_nRTN           OUT NUMBER
    )
    IS
        -- 嵌套函数: 计算统计数据
        FUNCTION CALC_STATS RETURN NUMBER
        IS
            -- 再嵌套: 计算总分
            FUNCTION CALC_TOTAL_SCORE(
                i_nSTU IN NUMBER
            ) RETURN NUMBER
            IS
            BEGIN
                RETURN FUNC_CALC_TOTAL(i_nSTU, NULL);
            EXCEPTION
                WHEN OTHERS THEN RETURN 0;
            END CALC_TOTAL_SCORE;

            -- 再嵌套: 计算平均分
            FUNCTION CALC_AVG_SCORE(
                i_nSTU IN NUMBER
            ) RETURN NUMBER
            IS
            BEGIN
                RETURN FUNC_CALC_AVG(i_nSTU, NULL);
            EXCEPTION
                WHEN OTHERS THEN RETURN 0;
            END CALC_AVG_SCORE;

            -- 再嵌套: 计算及格/不及格数
            FUNCTION CALC_PASS_FAIL(
                i_nSTU IN NUMBER,
                o_nPASS OUT NUMBER,
                o_nFAIL OUT NUMBER
            ) RETURN BOOLEAN
            IS
            BEGIN
                SELECT
                    NVL(SUM(CASE WHEN IS_PASS = 1 THEN 1 ELSE 0 END), 0),
                    NVL(SUM(CASE WHEN IS_PASS = 0 THEN 1 ELSE 0 END), 0)
                INTO o_nPASS, o_nFAIL
                FROM SCORE_REC WHERE STUDENT_NO = i_nSTU;
                RETURN TRUE;
            EXCEPTION
                WHEN OTHERS THEN
                    o_nPASS := 0;
                    o_nFAIL := 0;
                    RETURN FALSE;
            END CALC_PASS_FAIL;

            nTOTAL NUMBER;
            nAVG   NUMBER;
            nPASS  NUMBER;
            nFAIL  NUMBER;
        BEGIN
            nTOTAL := CALC_TOTAL_SCORE(i_nSTUDENT_NO);
            nAVG := CALC_AVG_SCORE(i_nSTUDENT_NO);
            CALC_PASS_FAIL(i_nSTUDENT_NO, nPASS, nFAIL);

            PROC_INSERT_WORK(i_sWSNUM, i_nSTUDENT_NO, i_vSTUDENT_NAME,
                           nTOTAL, nAVG, nPASS, nFAIL);
            RETURN 0;
        EXCEPTION
            WHEN OTHERS THEN
                RETURN -1;
        END CALC_STATS;

    BEGIN
        o_nRTN := CALC_STATS;
    END PROC_PROCESS_STUDENT;

    ------------------------------------------------------------------------
    -- 子程序13: 处理批量成绩 (BULK COLLECT + FORALL)
    ------------------------------------------------------------------------
    PROCEDURE PROC_BATCH_SCORE_PROCESS(
        i_vWSNUM         IN  VARCHAR2,
        i_vTANTO_CD     IN  VARCHAR2,
        i_nEXAM_DATE    IN  NUMBER,
        i_nEXAM_TYPE    IN  NUMBER,
        o_nRTN          OUT NUMBER
    )
    IS
        v_scores         t_score_list;
        v_exists         NUMBER;
        v_grade_level    NUMBER;
        v_cnt            NUMBER := 0;

        -- 嵌套函数: 批量获取数据
        FUNCTION FETCH_BATCH_DATA(
            i_sWS IN VARCHAR2
        ) RETURN t_score_list
        IS
            v_result t_score_list;
        BEGIN
            SELECT STUDENT_NO, SUBJECT_CD, i_nEXAM_DATE, i_nEXAM_TYPE,
                   TO_NUMBER(SCORE), NULL, NULL
            BULK COLLECT INTO v_result
            FROM WK_SCORE_REPORT
            WHERE WSNUM = i_sWS
            AND STUDENT_NO IS NOT NULL
            AND SUBJECT_CD IS NOT NULL
            AND SCORE IS NOT NULL;
            RETURN v_result;
        EXCEPTION
            WHEN OTHERS THEN
                RETURN t_score_list();
        END FETCH_BATCH_DATA;

        -- 嵌套函数: 处理单条记录
        FUNCTION PROCESS_SINGLE(
            i_r IN t_score_rec
        ) RETURN t_score_rec
        IS
            v_rec t_score_rec := i_r;
        BEGIN
            v_rec.GRADE_LEVEL := FUNC_GET_GRADE_LEVEL(i_r.SCORE);
            v_rec.IS_PASS := CASE WHEN i_r.SCORE >= g_nPASS_SCORE THEN 1 ELSE 0 END;
            RETURN v_rec;
        EXCEPTION
            WHEN OTHERS THEN
                RETURN i_r;
        END PROCESS_SINGLE;

    BEGIN
        -- BULK COLLECT
        v_scores := FETCH_BATCH_DATA(i_vWSNUM);

        -- FORALL: 批量插入或更新
        FORALL i IN 1..v_scores.COUNT
            INSERT INTO SCORE_REC (
                STUDENT_NO, SUBJECT_CD, EXAM_DATE, EXAM_TYPE,
                SCORE, GRADE_LEVEL, IS_PASS,
                REC_CREATE_TANTO_CD, REC_CREATE_DATE, REC_CREATE_WS,
                REC_UPDATE_TANTO_CD, REC_UPDATE_DATE, REC_UPDATE_WS, REC_UPDATE_KBN
            ) VALUES (
                v_scores(i).STUDENT_NO,
                v_scores(i).SUBJECT_CD,
                v_scores(i).EXAM_DATE,
                v_scores(i).EXAM_TYPE,
                v_scores(i).SCORE,
                FUNC_GET_GRADE_LEVEL(v_scores(i).SCORE),
                CASE WHEN v_scores(i).SCORE >= g_nPASS_SCORE THEN 1 ELSE 0 END,
                i_vTANTO_CD, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'), i_vWSNUM,
                i_vTANTO_CD, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'), i_vWSNUM, '01'
            );

        o_nRTN := c_OK;
        FUNC_SETLOG('PROC_BATCH_SCORE_PROCESS', 0, 0, '', '', '批量处理: ' || SQL%ROWCOUNT || '件');

    EXCEPTION
        WHEN OTHERS THEN
            o_nRTN := c_ERR;
            FUNC_SETLOG('PROC_BATCH_SCORE_PROCESS', 0, 1, SQLCODE, SQLERRM, '批量处理失败');
    END PROC_BATCH_SCORE_PROCESS;

    ------------------------------------------------------------------------
    -- 子程序14: 批量更新排名 (FORALL + BULK COLLECT)
    ------------------------------------------------------------------------
    PROCEDURE PROC_BATCH_RANK_UPDATE(
        i_nGRADE     IN  NUMBER,
        o_nRTN       OUT NUMBER
    )
    IS
        TYPE t_rank_rec IS RECORD (
            STUDENT_NO  NUMBER,
            TOTAL_SCORE NUMBER,
            CLASS_RANK  NUMBER,
            GRADE_RANK  NUMBER
        );
        TYPE t_rank_list IS TABLE OF t_rank_rec INDEX BY PLS_INTEGER;
        v_ranks t_rank_list;
        v_sql   NVARCHAR2(4000);

        -- 嵌套函数: 构建排名查询
        FUNCTION BUILD_RANK_QUERY(
            i_nGRD IN NUMBER
        ) RETURN VARCHAR2
        IS
            v_sql VARCHAR2(4000);
        BEGIN
            v_sql := ' SELECT STUDENT_NO, TOTAL_SCORE, ' ||
                     ' RANK() OVER (PARTITION BY CLASS_NO ORDER BY TOTAL_SCORE DESC) AS CLASS_RANK, ' ||
                     ' RANK() OVER (PARTITION BY GRADE ORDER BY TOTAL_SCORE DESC) AS GRADE_RANK ' ||
                     ' FROM (SELECT S.STUDENT_NO, S.GRADE, S.CLASS_NO, ' ||
                     ' (SELECT SUM(NVL(SC.SCORE, 0)) FROM SCORE_REC SC WHERE SC.STUDENT_NO = S.STUDENT_NO) AS TOTAL_SCORE ' ||
                     ' FROM STUDENT_MST S WHERE S.DELETE_FLG = 0 AND S.GRADE = ' || i_nGRD || ') ';
            RETURN v_sql;
        EXCEPTION
            WHEN OTHERS THEN RETURN NULL;
        END BUILD_RANK_QUERY;

    BEGIN
        v_sql := BUILD_RANK_QUERY(i_nGRADE);

        IF v_sql IS NOT NULL THEN
            EXECUTE IMMEDIATE v_sql BULK COLLECT INTO v_ranks;

            FORALL i IN 1..v_ranks.COUNT
                UPDATE SCORE_REC
                SET SCORE_RANK = v_ranks(i).CLASS_RANK
                WHERE STUDENT_NO = v_ranks(i).STUDENT_NO;
        END IF;

        o_nRTN := c_OK;
        FUNC_SETLOG('PROC_BATCH_RANK_UPDATE', 0, 0, '', '', '排名更新: ' || v_ranks.COUNT || '件');

    EXCEPTION
        WHEN OTHERS THEN
            o_nRTN := c_ERR;
            FUNC_SETLOG('PROC_BATCH_RANK_UPDATE', 0, 1, SQLCODE, SQLERRM, '排名更新失败');
    END PROC_BATCH_RANK_UPDATE;

    ------------------------------------------------------------------------
    -- 子程序15: 导出CSV (嵌套文件处理)
    ------------------------------------------------------------------------
    PROCEDURE PROC_EXPORT_CSV(
        i_vWSNUM         IN  VARCHAR2,
        i_vFILENAME      IN  VARCHAR2,
        o_nRTN           OUT NUMBER,
        o_vFILE_PATH    OUT VARCHAR2
    )
    IS
        v_FILE     UTL_FILE.FILE_TYPE;
        v_LINE     VARCHAR2(4000);
        v_CNT      NUMBER := 0;

        -- 嵌套函数: 格式化CSV行
        FUNCTION FORMAT_CSV_LINE(
            i_nNO IN NUMBER,
            i_nSTU IN NUMBER,
            i_vNAME IN VARCHAR2,
            i_nTOT IN NUMBER,
            i_nAVG IN NUMBER
        ) RETURN VARCHAR2
        IS
        BEGIN
            RETURN i_nNO || ',' || i_nSTU || ',' ||
                   REPLACE(i_vNAME, ',', ';') || ',' ||
                   NVL(TO_CHAR(i_nTOT), '') || ',' ||
                   NVL(TO_CHAR(i_nAVG), '');
        EXCEPTION
            WHEN OTHERS THEN RETURN '';
        END FORMAT_CSV_LINE;

        -- 嵌套函数: 写入文件头
        FUNCTION WRITE_HEADER(
            i_f IN OUT UTL_FILE.FILE_TYPE
        ) RETURN BOOLEAN
        IS
        BEGIN
            UTL_FILE.PUT_LINE(i_f, 'NO,STUDENT_NO,STUDENT_NAME,TOTAL_SCORE,AVG_SCORE');
            RETURN TRUE;
        EXCEPTION
            WHEN OTHERS THEN RETURN FALSE;
        END WRITE_HEADER;

        CURSOR CUR_DATA IS
            SELECT ROW_NO, STUDENT_NO, STUDENT_NAME, TOTAL_SCORE, AVERAGE_SCORE
            FROM WK_SCORE_REPORT
            WHERE WSNUM = i_vWSNUM
            ORDER BY ROW_NO;
    BEGIN
        v_FILE := UTL_FILE.FOPEN('DATA_DIR', i_vFILENAME, 'W');
        WRITE_HEADER(v_FILE);

        FOR rec IN CUR_DATA LOOP
            v_LINE := FORMAT_CSV_LINE(rec.ROW_NO, rec.STUDENT_NO, rec.STUDENT_NAME,
                                      rec.TOTAL_SCORE, rec.AVERAGE_SCORE);
            UTL_FILE.PUT_LINE(v_FILE, v_LINE);
            v_CNT := v_CNT + 1;
        END LOOP;

        UTL_FILE.FCLOSE(v_FILE);
        o_nRTN := c_OK;
        o_vFILE_PATH := 'DATA_DIR/' || i_vFILENAME;
        FUNC_SETLOG('PROC_EXPORT_CSV', 0, 0, '', '', '导出: ' || v_CNT || '件');

    EXCEPTION
        WHEN OTHERS THEN
            o_nRTN := c_ERR;
            IF UTL_FILE.IS_OPEN(v_FILE) THEN UTL_FILE.FCLOSE(v_FILE); END IF;
            o_vFILE_PATH := NULL;
            FUNC_SETLOG('PROC_EXPORT_CSV', 0, 1, SQLCODE, SQLERRM, '导出失败');
    END PROC_EXPORT_CSV;

    ------------------------------------------------------------------------
    -- 在线处理入口
    ------------------------------------------------------------------------
    PROCEDURE PONLINE(
        i_sWSNUM          IN  NVARCHAR2,
        i_sTANTO_CD      IN  CHAR,
        i_sPARAM         IN  NVARCHAR2,
        o_nRESULT        OUT NUMBER,
        o_sMESSAGE       OUT NVARCHAR2
    )
    IS
        bRTN    BOOLEAN;
    BEGIN
        bRTN := FUNC_INIT(i_sWSNUM, i_sTANTO_CD);
        PROC_LOAD_CONFIG;
        PROC_CLEAR_WORKTABLE(g_sWSNUM);

        o_nRESULT := c_OK;
        o_sMESSAGE := '处理完成,批次号:' || g_nJOBNUM;
        bRTN := FUNC_SETLOG('PONLINE', 0, 0, '', '', o_sMESSAGE);

    EXCEPTION
        WHEN OTHERS THEN
            o_nRESULT := c_ERR;
            o_sMESSAGE := SQLERRM;
            bRTN := FUNC_SETLOG('PONLINE', 0, 1, SQLCODE, SQLERRM, '在线处理异常');
    END PONLINE;

    ------------------------------------------------------------------------
    -- 批次处理入口
    ------------------------------------------------------------------------
    PROCEDURE PBATCH(
        i_nJOBNUM        IN  NUMBER,
        i_sWSNUM         IN  NVARCHAR2,
        i_sTANTO_CD     IN  CHAR,
        i_nNENDO         IN  NUMBER,
        i_nGRADE         IN  NUMBER,
        i_nCLASS_NO      IN  NUMBER,
        i_nEXAM_TYPE     IN  NUMBER,
        o_nRTN           OUT NUMBER
    )
    IS
        bRTN        BOOLEAN;
        nRTN        NUMBER;
        nROWNO      NUMBER := 0;
        vUSER       NVARCHAR2(100);
        v_cursor    SYS_REFCURSOR;
        v_student   STUDENT_MST%ROWTYPE;
    BEGIN
        g_nJOBNUM := i_nJOBNUM;
        g_sWSNUM := i_sWSNUM;
        g_sTANTO_CD := i_sTANTO_CD;

        PROC_LOAD_CONFIG;
        PROC_CLEAR_WORKTABLE(g_sWSNUM);

        v_cursor := F_GET_STUDENT_CURSOR(i_nGRADE, i_nCLASS_NO);

        LOOP
            FETCH v_cursor INTO v_student;
            EXIT WHEN v_cursor%NOTFOUND;
            nROWNO := nROWNO + 1;
            PROC_PROCESS_STUDENT(v_student.STUDENT_NO, v_student.STUDENT_NAME,
                                v_student.GRADE, v_student.CLASS_NO, g_sWSNUM, nRTN);
        END LOOP;

        CLOSE v_cursor;
        COMMIT;

        g_sMESSAGE := '批次处理完成:' || nROWNO || '件';
        bRTN := FUNC_SETLOG('PBATCH', 0, 0, '', '', g_sMESSAGE);
        o_nRTN := c_OK;

    EXCEPTION
        WHEN OTHERS THEN
            IF v_cursor%ISOPEN THEN CLOSE v_cursor; END IF;
            o_nRTN := c_ERR;
            bRTN := FUNC_SETLOG('PBATCH', 0, 1, SQLCODE, SQLERRM, '批次处理异常');
    END PBATCH;

    ------------------------------------------------------------------------
    -- 成绩处理
    ------------------------------------------------------------------------
    PROCEDURE P_PROCESS_SCORE(
        i_nSTUDENT_NO      IN  NUMBER,
        i_nSUBJECT_CD      IN  NUMBER,
        i_nEXAM_DATE       IN  NUMBER,
        i_nEXAM_TYPE       IN  NUMBER,
        i_nSCORE           IN  NUMBER,
        o_nRTN             OUT NUMBER
    )
    IS
        nEXISTS    NUMBER;
        nGRADE     NUMBER;
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
                IS_PASS = CASE WHEN i_nSCORE >= g_nPASS_SCORE THEN 1 ELSE 0 END,
                REC_UPDATE_DATE = TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3')
            WHERE STUDENT_NO = i_nSTUDENT_NO
            AND SUBJECT_CD = i_nSUBJECT_CD
            AND EXAM_DATE = i_nEXAM_DATE
            AND EXAM_TYPE = i_nEXAM_TYPE;
        ELSE
            INSERT INTO SCORE_REC (
                STUDENT_NO, SUBJECT_CD, EXAM_DATE, EXAM_TYPE,
                SCORE, GRADE_LEVEL, IS_PASS,
                REC_CREATE_DATE, REC_UPDATE_DATE
            ) VALUES (
                i_nSTUDENT_NO, i_nSUBJECT_CD, i_nEXAM_DATE, i_nEXAM_TYPE,
                i_nSCORE, nGRADE, CASE WHEN i_nSCORE >= g_nPASS_SCORE THEN 1 ELSE 0 END,
                TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3'),
                TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3')
            );
        END IF;

    EXCEPTION
        WHEN OTHERS THEN
            o_nRTN := c_ERR;
            ROLLBACK;
    END P_PROCESS_SCORE;

    ------------------------------------------------------------------------
    -- 批量成绩处理
    ------------------------------------------------------------------------
    PROCEDURE P_BATCH_PROCESS_SCORE(
        i_vWSNUM         IN  VARCHAR2,
        i_vTANTO_CD     IN  VARCHAR2,
        i_nEXAM_DATE    IN  NUMBER,
        i_nEXAM_TYPE    IN  NUMBER,
        o_nRTN          OUT NUMBER
    )
    IS
    BEGIN
        PROC_BATCH_SCORE_PROCESS(i_vWSNUM, i_vTANTO_CD, i_nEXAM_DATE, i_nEXAM_TYPE, o_nRTN);
    END P_BATCH_PROCESS_SCORE;

    ------------------------------------------------------------------------
    -- 生成成绩报表
    ------------------------------------------------------------------------
    PROCEDURE P_GEN_REPORT(
        i_nNENDO           IN  NUMBER,
        i_nGRADE           IN  NUMBER,
        i_nCLASS_NO        IN  NUMBER,
        i_vWSNUM           IN  VARCHAR2,
        o_nRTN             OUT NUMBER
    )
    IS
        bRTN        BOOLEAN;
        nRTN        NUMBER;
        nROWNO      NUMBER := 0;
        vUSER       NVARCHAR2(100);
    BEGIN
        g_sWSNUM := i_vWSNUM;
        SELECT NVL(MAX(JOBNUM), 0) + 1 INTO g_nJOBNUM FROM WK_SCORE_REPORT;

        PROC_LOAD_CONFIG;
        PROC_CLEAR_WORKTABLE(g_sWSNUM);

        FOR v_student IN CUR_ALL_STUDENT(i_nGRADE, i_nCLASS_NO) LOOP
            PROC_PROCESS_STUDENT(v_student.STUDENT_NO, v_student.STUDENT_NAME,
                                v_student.GRADE, v_student.CLASS_NO, g_sWSNUM, nRTN);
            nROWNO := nROWNO + 1;
        END LOOP;

        g_sMESSAGE := '报表生成:' || nROWNO || '件';
        bRTN := FUNC_SETLOG('P_GEN_REPORT', 0, 0, '', '', g_sMESSAGE);
        o_nRTN := c_OK;

    EXCEPTION
        WHEN OTHERS THEN
            o_nRTN := c_ERR;
            ROLLBACK;
            bRTN := FUNC_SETLOG('P_GEN_REPORT', 0, 1, SQLCODE, SQLERRM, '报表生成异常');
    END P_GEN_REPORT;

    ------------------------------------------------------------------------
    -- 更新排名
    ------------------------------------------------------------------------
    PROCEDURE P_UPDATE_RANK(
        i_nGRADE           IN  NUMBER,
        o_nRTN             OUT NUMBER
    )
    IS
        nTOTAL     NUMBER;
    BEGIN
        o_nRTN := c_OK;
        FOR REC IN CUR_ALL_STUDENT(i_nGRADE, NULL) LOOP
            nTOTAL := FUNC_CALC_TOTAL(REC.STUDENT_NO, NULL);
        END LOOP;
    EXCEPTION
        WHEN OTHERS THEN
            o_nRTN := c_ERR;
            ROLLBACK;
    END P_UPDATE_RANK;

    ------------------------------------------------------------------------
    -- 批量更新排名
    ------------------------------------------------------------------------
    PROCEDURE P_BATCH_UPDATE_RANK(
        i_nGRADE           IN  NUMBER,
        o_nRTN             OUT NUMBER
    )
    IS
    BEGIN
        PROC_BATCH_RANK_UPDATE(i_nGRADE, o_nRTN);
    END P_BATCH_UPDATE_RANK;

    ------------------------------------------------------------------------
    -- 导出CSV
    ------------------------------------------------------------------------
    PROCEDURE P_EXPORT_CSV(
        i_vWSNUM         IN  VARCHAR2,
        i_vFILENAME      IN  VARCHAR2,
        o_nRTN           OUT NUMBER,
        o_vFILE_PATH    OUT VARCHAR2
    )
    IS
    BEGIN
        PROC_EXPORT_CSV(i_vWSNUM, i_vFILENAME, o_nRTN, o_vFILE_PATH);
    END P_EXPORT_CSV;

    ------------------------------------------------------------------------
    -- 获取学生成绩列表
    ------------------------------------------------------------------------
    FUNCTION F_GET_SCORE_LIST(i_nSTUDENT_NO IN NUMBER) RETURN SCORE_LIST
    IS
        tLIST   SCORE_LIST := SCORE_LIST();
        nCOUNT  NUMBER := 0;
    BEGIN
        FOR REC IN CUR_ALL_SCORE(i_nSTUDENT_NO) LOOP
            tLIST.EXTEND;
            nCOUNT := nCOUNT + 1;
            tLIST(nCOUNT) := SCORE_RECORD(
                nCOUNT, REC.SUBJECT_NAME || ':' || REC.SCORE,
                CASE REC.IS_PASS WHEN 1 THEN 0 ELSE 2 END,
                REC.STUDENT_NO, REC.SUBJECT_CD, REC.SCORE,
                REC.GRADE_LEVEL, REC.IS_PASS
            );
        END LOOP;
        RETURN tLIST;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN SCORE_LIST();
    END F_GET_SCORE_LIST;

    ------------------------------------------------------------------------
    -- 获取学生总成绩
    ------------------------------------------------------------------------
    FUNCTION F_GET_TOTAL(i_nSTUDENT_NO IN NUMBER) RETURN NUMBER
    IS
    BEGIN
        RETURN FUNC_CALC_TOTAL(i_nSTUDENT_NO, NULL);
    END F_GET_TOTAL;

    ------------------------------------------------------------------------
    -- 获取学生平均成绩
    ------------------------------------------------------------------------
    FUNCTION F_GET_AVG(i_nSTUDENT_NO IN NUMBER) RETURN NUMBER
    IS
    BEGIN
        RETURN FUNC_CALC_AVG(i_nSTUDENT_NO, NULL);
    END F_GET_AVG;

    ------------------------------------------------------------------------
    -- 获取学生排名
    ------------------------------------------------------------------------
    FUNCTION F_GET_RANK(i_nSTUDENT_NO IN NUMBER, i_nRANK_TYPE IN NUMBER) RETURN NUMBER
    IS
        vGRADE     STUDENT_MST.GRADE%TYPE;
        vCLASS     STUDENT_MST.CLASS_NO%TYPE;
    BEGIN
        SELECT GRADE, CLASS_NO INTO vGRADE, vCLASS
        FROM STUDENT_MST WHERE STUDENT_NO = i_nSTUDENT_NO;
        RETURN FUNC_CALC_RANK(i_nSTUDENT_NO, vGRADE, vCLASS, i_nRANK_TYPE);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN 1;
    END F_GET_RANK;

END PKG_SCORE_MANAGE;
/
