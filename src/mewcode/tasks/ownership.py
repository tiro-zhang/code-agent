"""顶层入口共用单一锁，直接 Skill 内部接续复用所有者。"""

from functools import wraps


def serialized_turn(function):
    @wraps(function)
    async def invoke(session, *args, **options):
        nested = options.pop('_within_skill', False)
        if nested:
            async for event in function(session, *args, **options):
                yield event
        else:
            async with session._main_owner:
                source = function(session, *args, **options)
                try:
                    async for event in source:
                        yield event
                finally:
                    await source.aclose()
    return invoke
