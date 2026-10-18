import asyncio


def tick():
    pass


def schedule():
    loop = asyncio.get_running_loop()
    loop.call_soon(tick)


def schedule_threadsafe():
    loop = asyncio.get_event_loop()
    loop.call_soon_threadsafe(tick)
