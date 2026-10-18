import asyncio


def tick():
    pass


def schedule():
    loop = asyncio.get_running_loop()
    loop.call_later(1.0, tick)
