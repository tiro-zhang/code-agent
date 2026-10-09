import re


def slugify(text):
    """把文本转换为 slug。

    去掉首尾空白，把任意连续空白替换为一个连字符，并转为小写。
    空文本返回空字符串。
    """
    return re.sub(r"\s+", "-", text.strip()).lower()
