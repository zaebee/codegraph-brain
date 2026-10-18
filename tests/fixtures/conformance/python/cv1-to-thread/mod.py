import asyncio


def work():
    pass


async def offload():
    await asyncio.to_thread(work)
