"""Developer-only bounded native trace; never enables or submits companion actions."""
import json

from .native import NativeBackend, lua_source
from .protocol import identifier


class NativeRequestTrace:
    def __init__(self, evaluator=None):
        self.backend = NativeBackend(evaluator)

    def query(self, actor_uuid, operation="read"):
        actor_uuid = identifier(actor_uuid)
        if operation not in ("install", "read", "stop"):
            raise ValueError("invalid trace operation")
        source = "local f=function(...)\n" + lua_source("trace.lua") + "\nend\nreturn f(" + json.dumps(actor_uuid) + "," + json.dumps(operation) + ")"
        result = self.backend._evaluate(source)
        if result.get("records") == {}:
            result["records"] = []
        if result.get("errors") == {}:
            result["errors"] = []
        return result
