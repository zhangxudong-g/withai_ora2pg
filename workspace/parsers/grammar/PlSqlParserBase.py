from antlr4 import *


class PlSqlParserBase(Parser):

    _isVersion10 = False
    _isVersion11 = True
    _isVersion12 = True

    def isVersion10(self):
        return self._isVersion10

    def isVersion11(self):
        return self._isVersion11

    def isVersion12(self):
        return self._isVersion12

    def setVersion10(self, value):
        self._isVersion10 = value

    def setVersion11(self, value):
        self._isVersion11 = value

    def setVersion12(self, value):
        self._isVersion12 = value

    def IsNotNumericFunction(self):
        # 使用相对导入
        try:
            from .PlSqlLexer import PlSqlLexer as _Lexer
        except ImportError:
            # 回退：直接内联检查
            lt1 = self._input.LT(1)
            lt2 = self._input.LT(2)
            # SUM, COUNT, AVG, MIN, MAX, ROUND, LEAST, GREATEST 是常见的数值函数
            numeric_tokens = {1, 2, 3, 4, 5, 6, 7, 8}  # 占位符
            return not (lt1.type in numeric_tokens and lt2.type == 9)  # 9 = LEFT_PAREN
        
        lt1 = self._input.LT(1)
        lt2 = self._input.LT(2)
        if (lt1.type in (_Lexer.SUM, _Lexer.COUNT, _Lexer.AVG,
                         _Lexer.MIN, _Lexer.MAX, _Lexer.ROUND,
                         _Lexer.LEAST, _Lexer.GREATEST) and
                lt2.type == _Lexer.LEFT_PAREN):
            return False
        return True

    def isNotStartOfJoin(self):
        try:
            from .PlSqlLexer import PlSqlLexer as _Lexer
        except ImportError:
            # 回退
            lt1 = self._input.LT(1)
            join_tokens = {1, 2, 3, 4, 5, 6, 7, 8}  # 占位符
            return lt1.type not in join_tokens
        
        lt1 = self._input.LT(1)
        if (lt1.type in (_Lexer.INNER, _Lexer.CROSS, _Lexer.NATURAL,
                         _Lexer.PARTITION, _Lexer.FULL, _Lexer.LEFT,
                         _Lexer.RIGHT, _Lexer.OUTER)):
            return False
        return True
