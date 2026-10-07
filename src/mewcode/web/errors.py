"""公开错误不携带内部异常、配置或请求正文。"""


class WebError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

    def body(self):
        return {'error': {'code': self.code, 'message': self.message}}
