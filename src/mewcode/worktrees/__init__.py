"""Git 工作副本的受管生命周期。"""

from .manager import WorktreeManager
from .records import Worktree

__all__ = ['WorktreeManager', 'Worktree']
