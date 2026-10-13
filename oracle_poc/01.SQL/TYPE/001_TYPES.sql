-- ============================================================================
-- 类型定义 - Oracle特有复杂类型
-- ============================================================================

-- ============================================================================
-- 1. 通用参数数组类型 (Oracle嵌套表)
-- ============================================================================
CREATE OR REPLACE TYPE A_CONS_PRM AS TABLE OF NVARCHAR2(1000);
/

-- ============================================================================
-- 2. 成绩记录对象类型 (Oracle对象类型)
-- ============================================================================
CREATE OR REPLACE TYPE SCORE_RECORD AS OBJECT (
    CNT             NUMBER,              -- 计数
    NAIYOU          NVARCHAR2(1000),    -- 内容说明
    KBN             NUMBER,             -- 区分 INFO:0 INPUT:1 OUTPUT:2
    STUDENT_NO      NUMBER(10,0),       -- 学生编号
    SUBJECT_CD      NUMBER(5,0),        -- 科目代码
    SCORE           NUMBER(5,2),        -- 分数
    GRADE_LEVEL     NUMBER(1,0),        -- 等级
    IS_PASS         NUMBER(1,0),         -- 是否及格
    MEMBER FUNCTION GET_STATUS RETURN VARCHAR2,
    MEMBER FUNCTION GET_LETTER_GRADE RETURN VARCHAR2
)
NOT FINAL
/
-- ============================================================================
-- 3. 对象类型的方法实现
-- ============================================================================
CREATE OR REPLACE TYPE BODY SCORE_RECORD AS
    MEMBER FUNCTION GET_STATUS RETURN VARCHAR2 IS
    BEGIN
        IF SELF.IS_PASS = 1 THEN
            RETURN '合格';
        ELSE
            RETURN '不合格';
        END IF;
    END GET_STATUS;

    MEMBER FUNCTION GET_LETTER_GRADE RETURN VARCHAR2 IS
    BEGIN
        IF SELF.SCORE >= 90 THEN
            RETURN 'A';
        ELSIF SELF.SCORE >= 80 THEN
            RETURN 'B';
        ELSIF SELF.SCORE >= 70 THEN
            RETURN 'C';
        ELSIF SELF.SCORE >= 60 THEN
            RETURN 'D';
        ELSE
            RETURN 'F';
        END IF;
    END GET_LETTER_GRADE;
END;
/

-- ============================================================================
-- 4. 成绩列表类型 (嵌套表类型)
-- ============================================================================
CREATE OR REPLACE TYPE SCORE_LIST AS TABLE OF SCORE_RECORD;
/

-- ============================================================================
-- 5. 学生记录对象类型
-- ============================================================================
CREATE OR REPLACE TYPE STUDENT_RECORD AS OBJECT (
    STUDENT_NO      NUMBER(10,0),
    STUDENT_NAME    NVARCHAR2(100),
    STUDENT_KANA    NVARCHAR2(200),
    GENDER          NUMBER(1,0),
    GRADE           NUMBER(2,0),
    CLASS_NO        NUMBER(2,0),
    BIRTH_DATE      NUMBER(8,0),
    TOTAL_SCORE     NUMBER(10,2),
    AVG_SCORE       NUMBER(5,2),
    RANK_NO         NUMBER(5,0),
    MEMBER FUNCTION GET_AGE RETURN NUMBER,
    MEMBER FUNCTION GET_GENDER_NAME RETURN VARCHAR2,
    MEMBER FUNCTION GET_RANK_STR RETURN VARCHAR2
)
NOT FINAL
/
CREATE OR REPLACE TYPE BODY STUDENT_RECORD AS
    MEMBER FUNCTION GET_AGE RETURN NUMBER IS
        vBIRTH_YEAR NUMBER;
        vNOW_YEAR NUMBER;
    BEGIN
        vBIRTH_YEAR := TO_NUMBER(SUBSTR(TO_CHAR(SELF.BIRTH_DATE), 1, 4));
        vNOW_YEAR := TO_NUMBER(TO_CHAR(SYSDATE, 'YYYY'));
        RETURN vNOW_YEAR - vBIRTH_YEAR;
    END GET_AGE;

    MEMBER FUNCTION GET_GENDER_NAME RETURN VARCHAR2 IS
    BEGIN
        IF SELF.GENDER = 1 THEN
            RETURN '男';
        ELSIF SELF.GENDER = 2 THEN
            RETURN '女';
        ELSE
            RETURN '未知';
        END IF;
    END GET_GENDER_NAME;

    MEMBER FUNCTION GET_RANK_STR RETURN VARCHAR2 IS
    BEGIN
        RETURN TO_CHAR(SELF.RANK_NO) || '位';
    END GET_RANK_STR;
END;
/

-- ============================================================================
-- 6. 学生列表类型
-- ============================================================================
CREATE OR REPLACE TYPE STUDENT_LIST AS TABLE OF STUDENT_RECORD;
/

-- ============================================================================
-- 7. REF CURSOR类型定义 (Oracle特有)
-- ============================================================================
CREATE OR REPLACE TYPE REF_CURSOR_T AS OBJECT (
    CURSOR_NAME VARCHAR2(100),
    STATIC FUNCTION OPEN_STUDENT_CURSOR RETURN SYS_REFCURSOR,
    STATIC FUNCTION OPEN_SCORE_CURSOR(p_STUDENT_NO IN NUMBER) RETURN SYS_REFCURSOR,
    STATIC FUNCTION OPEN_SUBJECT_CURSOR RETURN SYS_REFCURSOR
)
/
CREATE OR REPLACE TYPE BODY REF_CURSOR_T AS
    STATIC FUNCTION OPEN_STUDENT_CURSOR RETURN SYS_REFCURSOR IS
        vCURSOR SYS_REFCURSOR;
    BEGIN
        OPEN vCURSOR FOR
            SELECT * FROM STUDENT_MST WHERE DELETE_FLG = 0;
        RETURN vCURSOR;
    END OPEN_STUDENT_CURSOR;

    STATIC FUNCTION OPEN_SCORE_CURSOR(p_STUDENT_NO IN NUMBER) RETURN SYS_REFCURSOR IS
        vCURSOR SYS_REFCURSOR;
    BEGIN
        OPEN vCURSOR FOR
            SELECT * FROM SCORE_REC WHERE STUDENT_NO = p_STUDENT_NO;
        RETURN vCURSOR;
    END OPEN_SCORE_CURSOR;

    STATIC FUNCTION OPEN_SUBJECT_CURSOR RETURN SYS_REFCURSOR IS
        vCURSOR SYS_REFCURSOR;
    BEGIN
        OPEN vCURSOR FOR
            SELECT * FROM SUBJECT_MST WHERE DELETE_FLG = 0;
        RETURN vCURSOR;
    END OPEN_SUBJECT_CURSOR;
END;
/

-- ============================================================================
-- 8. 配置记录类型
-- ============================================================================
CREATE OR REPLACE TYPE CONFIG_RECORD AS OBJECT (
    CONFIG_CD       NVARCHAR2(100),
    CONFIG_NAME     NVARCHAR2(200),
    CONFIG_VALUE    NVARCHAR2(1000),
    CONFIG_TYPE     NUMBER(2,0)
)
/
CREATE OR REPLACE TYPE CONFIG_LIST AS TABLE OF CONFIG_RECORD;
/
