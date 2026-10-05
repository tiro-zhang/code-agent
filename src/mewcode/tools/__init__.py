"""MewCode 本地工具。"""


def default_registry():
    from .command import ExecuteCommand
    from .files import EditFile, ReadFile, WriteFile
    from .registry import ToolRegistry
    from .search import GlobFiles, SearchCode
    from ..skills.tool import LoadSkill

    return ToolRegistry([ReadFile(), WriteFile(), EditFile(), ExecuteCommand(), GlobFiles(), SearchCode(), LoadSkill()])
