import asyncio


def work():
    pass


async def running():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, work)


async def event():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, work)
