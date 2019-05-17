""" Agent """
from config import Config
from indy_sdk_utils import open_wallet
from hooks import self_hook_point
from errors import NoRegisteredRouteException

class Agent:
    """ Agent """
    hooks = {}

    def __init__(self):
        self.config = None
        self.wallet_handle = None
        self.routes = {}
        self.hooks = Agent.hooks.copy() # Copy statically configured hooks

    @staticmethod
    async def from_config(config: Config):
        agent = Agent()
        agent.config = config
        agent.wallet_handle = await open_wallet(
            agent.config.wallet,
            agent.config.passphrase,
            agent.config.ephemeral
        )
        return agent

    def register_route(self, msg_type):
        """ Register route decorator. """
        def register_route_dec(func):
            self.routes[msg_type] = func

        return register_route_dec

    async def route(self, msg, *args, **kwargs):
        """ Route message """
        if not msg.type in self.routes:
            raise NoRegisteredRouteException

        await self.routes[msg.type](msg, *args, **kwargs)

    async def deserialize(self, wire):
        """ Deserialization of message from bytes. """

    # Hooks discovered at runtime
    @self_hook_point()
    async def unpack(self, packed_message):
        """ Perform processing to convert bytes off the wire to Message. """