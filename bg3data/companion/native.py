"""Read-only SE adapter. Native execution remains unavailable until validated live."""
from __future__ import annotations

import json
from pathlib import Path

from .protocol import ACTIONS, identifier, reject


def lua_source(name):
    if name not in ("snapshot.lua", "probe.lua", "trace.lua", "prepare.lua"):
        raise ValueError("unknown companion Lua source")
    return (Path(__file__).parent / "lua" / name).read_text(encoding="utf-8")


class NativeBackend:
    def __init__(self, evaluator=None):
        self.evaluator = evaluator

    def capabilities(self):
        # No game lookup or Lua execution: safe even while the user is playing.
        return {"backend": "native", "normal_rules_validated": False, "simulation_only": False,
                "actions": dict.fromkeys(ACTIONS, False), "observation_validated_live": False,
                "reason": "native request submission, normal costs and multiplayer synchronization need live validation"}

    def _evaluate(self, code):
        if self.evaluator is None:
            from .. import se
            result = se.eval_lua(code, "server", timeout=10)
        else:
            result = self.evaluator(code)
        if not isinstance(result, dict) or result.get("ok") is not True:
            reject("bridge_error", "Script Extender read-only query failed")
        state = result.get("result")
        if not isinstance(state, dict):
            reject("bridge_error", "Script Extender returned an invalid object")
        return state

    def observe(self, actor_uuid=None):
        actor_uuid = identifier(actor_uuid) if actor_uuid is not None else None
        code = "local f=function(...)\n" + lua_source("snapshot.lua") + "\nend\nreturn f(" + (json.dumps(actor_uuid) if actor_uuid else "nil") + ")"
        raw = self._evaluate(code)
        for key in ("party", "combatants", "warnings", "objects", "reactions"):
            if raw.get(key) == {}:
                raw[key] = []
            raw.setdefault(key, [])
        for unit in [raw.get("actor", {})] + raw["party"] + raw["combatants"]:
            if unit.get("spells") == {}:
                unit["spells"] = []
            # Visibility semantics have not been validated. Do not expose engine-only enemies.
            unit["visibility_verified"] = False
            unit["visible"] = False
        raw["warnings"].append("Native visibility adapter unvalidated: nonparty entities are withheld")
        return raw

    def probe(self, actor_uuid):
        actor_uuid = identifier(actor_uuid)
        source = "local f=function(...)\n" + lua_source("probe.lua") + "\nend\nreturn f(" + json.dumps(actor_uuid) + ")"
        return self._evaluate(source)

    def submit(self, request_id, actor_uuid, value):
        # Explicitly reject; never use scripted casts or a UI fallback.
        reject("backend_unavailable", "native execution is not validated or enabled")

    def poll(self, request_id):
        reject("backend_unavailable", "there are no native requests to poll")
