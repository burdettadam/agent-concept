import asyncio
from contextlib import suppress

from indy import crypto

from config import Config
from errors import UnknownTransportException
from hooks import self_hook_point
from messages.message import Message
import indy_sdk_utils as utils
from transport.connection import ConnectionImpossible
import transport.inbound.standard_in as StdIn
import transport.outbound.standard_out as StdOut
import transport.inbound.http as HttpIn
import transport.outbound.http as HttpOut

class Conductor:
    hooks = {}
    def __init__(self):
        self.wallet_handle = None
        self.inbound_transport = None
        self.outbound_transport = None
        self.transport_options = {}
        self.connection_queue = asyncio.Queue()
        self.open_connections = {}
        self.queues = {}
        self.message_queue = asyncio.Queue()
        self.hooks = Conductor.hooks.copy()
        self.async_tasks = []

    @staticmethod
    def in_transport_str_to_mod(transport_str):
        return {
            'stdin': StdIn,
            'http': HttpIn
        }[transport_str]

    @staticmethod
    def out_transport_str_to_mod(transport_str):
        return {
            'stdout': StdOut,
            'http': HttpOut,
        }[transport_str]

    @staticmethod
    def from_wallet_handle_config(wallet_handle, config: Config):
        conductor = Conductor()
        conductor.wallet_handle = wallet_handle
        conductor.transport_options = config.transport_options()

        try:
            conductor.inbound_transport = \
                    Conductor.in_transport_str_to_mod(config.inbound_transport)
            conductor.outbound_transport = \
                    Conductor.out_transport_str_to_mod(config.outbound_transport)
        except KeyError:
            raise UnknownTransportException

        return conductor

    def schedule_task(self, coro):
        task = asyncio.create_task(coro)
        self.async_tasks.append(task)

    async def start(self):
        inbound_task = asyncio.create_task(
            self.inbound_transport.accept(
                self.connection_queue,
                **self.transport_options
            )
        )
        accept_task = asyncio.create_task(self.accept())
        await asyncio.gather(inbound_task, accept_task)

    async def shutdown(self):
        await self.message_queue.join()
        for task in self.async_tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def accept(self):
        while True:
            conn = await self.connection_queue.get()
            self.schedule_task(self.message_reader(conn))

    async def message_reader(self, conn):
        conn.reciever_attached = True
        async for msg_bytes in conn.recv():
            if not msg_bytes:
                continue

            msg = await self.unpack(msg_bytes)
            if not msg.context:
                # plaintext messages are ignored
                conn.close() # TODO keeping connection open may be appropriate
                continue

            self.message_queue.put_nowait(msg)

            if not msg.context['from_key']:
                # anonymous messages cannot be return routed
                conn.close()
                continue

            if not '~transport' in msg or 'return_route' not in msg['~transport']:
                if not conn.can_recv():
                    # Can't get any more messages and not marked as
                    # return_route so close
                    conn.close()
                # Connection thinks there are more messages so don't close
                continue

            # Return route handling
            return_route = msg['~transport']['return_route']
            conn_id = msg.context['from_key']

            if return_route == 'all':
                self.open_connections[msg.context['from_key']] = conn
                self.schedule_task(self.connection_cleanup(conn, msg.context['from_key']))

            elif return_route == 'none' and conn_id in self.open_connections:
                del self.open_connections[conn_id]

            elif return_route == 'thread':
                # TODO Implement thread return route
                pass

        conn.reciever_attached = False

    async def connection_cleanup(self, conn, conn_id):
        await conn.wait()
        if conn_id in self.open_connections:
            del self.open_connections[conn_id]

    async def recv(self):
        msg = await self.message_queue.get()
        return msg

    async def message_handled(self):
        self.message_queue.task_done()

    @self_hook_point()
    async def unpack(self, message: bytes):
        """ Perform processing to convert bytes off the wire to Message. """
        return await utils.unpack(self.wallet_handle, message)

    async def send(self, to_did, to_key, from_key, msg):
        meta = await utils.get_did_metadata(self.wallet_handle, to_did)

        if to_key not in self.open_connections:
            try:
                conn = await self.outbound_transport.open(**meta)
            except ConnectionImpossible:
                if to_key not in self.queues:
                    self.queues[to_key] = asyncio.Queue()
                self.queues[to_key].put_nowait(msg)
                return
        else:
            conn = self.open_connections[to_key]

        if to_key in self.queues:
            if self.queues[to_key].qsize():
                self.queues[to_key].put_nowait(msg)
                msg = self.queues[to_key].get_nowait()

            msg += {
                '~transport': {
                    'pending_message_count': self.queues[to_key].qsize()
                }
            }

        wire_msg = await crypto.pack_message(
            self.wallet_handle,
            msg.serialize(),
            [to_key],
            from_key
        )

        await conn.send(wire_msg)

        if not conn.closed() and conn.can_recv() and not conn.reciever_attached:
            self.schedule_task(self.message_reader(conn))
