import asyncio
import sys
from transport.connection import Connection, ConnectionType

class StdOutConnection(Connection):
    def __init__(self):
        super().__init__(ConnectionType.SEND)
        self.loop = asyncio.get_running_loop()

    async def send(self, msg: str):
        await self.loop.run_in_executor(None, print, msg)

async def open(**metadata):
    return StdOutConnection()
