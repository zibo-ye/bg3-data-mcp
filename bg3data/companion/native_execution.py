"""Explicit opt-in developer executor; not part of autonomous companion MCP."""
import json
from pathlib import Path

from .native import NativeBackend
from .protocol import action, identifier, reject


class NativeResearchExecution:
    def __init__(self, evaluator=None, *, allow_execution=False):
        self.backend = NativeBackend(evaluator)
        self.allow_execution = allow_execution is True

    def _call(self, operation, actor_uuid, session_id, request_guid, value=None, known_target=None):
        actor_uuid, request_guid = identifier(actor_uuid), identifier(request_guid)
        if request_guid == "00000000-0000-0000-0000-000000000000":
            reject("invalid_uuid", "request GUID must be nonzero")
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
            reject("invalid_session", "expected observed session ID")
        if value is not None:
            value = action(value, supported_versions=(1,))
            if value["kind"] not in ("cast", "end_turn"):
                reject("unsupported_action", "unsupported native research action")
            if value["kind"] == "cast":
                targeted = value["spell_id"] == "Projectile_MainHandAttack" and value.get("target_uuid") not in (None, actor_uuid)
                self_shout = value.get("target_uuid") == actor_uuid and value["spell_id"] in ("Shout_Hide", "Shout_Dash")
                if not (targeted or self_shout):
                    reject("unsupported_action", "only observed self-Shout or explicitly known main-hand projectile targets are supported")
                if targeted:
                    if not isinstance(known_target, dict) or set(known_target) != {"actor_uuid", "session_id", "target_uuid", "source"}:
                        reject("target_knowledge_missing", "target requires explicit ordinary player observation")
                    known_target = dict(known_target, actor_uuid=identifier(known_target["actor_uuid"]), target_uuid=identifier(known_target["target_uuid"]))
                    if (known_target["actor_uuid"] != actor_uuid or known_target["session_id"] != session_id
                        or known_target["target_uuid"] != value["target_uuid"] or known_target["source"] != "ordinary_player_observation"):
                        reject("target_knowledge_mismatch", "target observation must match actor, session and target")
                elif known_target is not None:
                    reject("invalid_target_knowledge", "target attestation is only accepted for targeted research")
            elif known_target is not None:
                reject("invalid_target_knowledge", "end-turn does not accept target knowledge")
        spec = dict(operation=operation, actor_uuid=actor_uuid, session_id=session_id,
                    request_guid=request_guid, action=value, execution_opt_in=self.allow_execution, known_target=known_target)
        source = (Path(__file__).parent / "lua" / "execute_research.lua").read_text(encoding="utf-8")
        encoded = json.dumps(json.dumps(spec, allow_nan=False))
        return self.backend._evaluate("local f=function(...)\n" + source + "\nend\nreturn f(Ext.Json.Parse(" + encoded + "))")

    def submit(self, actor_uuid, session_id, value, request_guid, *, known_target=None):
        if not self.allow_execution:
            reject("execution_not_opted_in", "developer execution requires explicit allow_execution=True")
        return self._call("submit", actor_uuid, session_id, request_guid, value, known_target)

    def poll(self, actor_uuid, session_id, request_guid):
        return self._call("poll", actor_uuid, session_id, request_guid)

    def reconcile(self, actor_uuid, session_id, request_guid):
        """Read late exact-GUID evidence without enqueueing or fresh cost claims."""
        return self._call("reconcile", actor_uuid, session_id, request_guid)
