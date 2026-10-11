"""Strict wire contracts shared by simulation and future validated native adapters."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid

ACTIONS = ("cast", "move", "end_turn", "react", "follow", "interact")
TERMINAL = frozenset(("completed", "rejected", "uncertain"))


class Rejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def reject(code, message):
    raise Rejected(code, message)


def identifier(value):
    if not isinstance(value, str):
        reject("invalid_uuid", "expected an entity UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        reject("invalid_uuid", "expected an entity UUID")


def token(value, name="token"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        reject("invalid_token", f"invalid {name}")
    return value


def number(value, name, low=-1e6, high=1e6):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or not math.isfinite(value):
        reject("invalid_number", f"invalid {name}")
    return float(value)


def position(value):
    if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
        reject("invalid_position", "position requires exactly x, y, z world coordinates")
    return {k: number(value[k], k) for k in ("x", "y", "z")}


def cast_target(value):
    if not isinstance(value, dict) or set(value) not in ({"entity_uuid"}, {"position"}):
        reject("invalid_target", "cast selection requires exactly one entity UUID or position")
    return {"entity_uuid": identifier(value["entity_uuid"])} if "entity_uuid" in value else {"position": position(value["position"])}


def action(value, supported_versions=(1, 2)):
    if isinstance(value, dict) and "schema_version" in value:
        if type(value["schema_version"]) is not int or value["schema_version"] != 2 or 2 not in supported_versions:
            reject("unsupported_action_version", "action contract version is not supported")
        if value.get("kind") == "throw":
            reject("unsupported_action", "inventory throw contract is not implemented")
        if value.get("kind") != "cast" or set(value) != {"kind", "schema_version", "spell_option_id", "targets"}:
            reject("invalid_action", "v2 cast requires only version, observed option and targets")
        if not isinstance(value["targets"], list) or not 1 <= len(value["targets"]) <= 16:
            reject("invalid_targets", "cast requires 1 to 16 ordered target selections")
        return {"kind": "cast", "schema_version": 2,
                "spell_option_id": token(value["spell_option_id"], "spell_option_id"),
                "targets": [cast_target(v) for v in value["targets"]]}
    if 1 not in supported_versions:
        reject("unsupported_action_version", "v1 action contract is not supported")
    if not isinstance(value, dict) or value.get("kind") not in ACTIONS:
        reject("invalid_action", "unknown action kind")
    kind = value["kind"]
    expected = {
        "cast": ({"kind", "spell_id"}, {"target_uuid", "position"}),
        "move": ({"kind", "position"}, set()),
        "end_turn": ({"kind"}, set()),
        "react": ({"kind", "reaction_id", "accept"}, set()),
        "follow": ({"kind", "leader_uuid", "distance"}, set()),
        "interact": ({"kind", "target_uuid"}, set()),
    }
    required, optional = expected[kind]
    if not required <= set(value) or set(value) - required - optional:
        reject("invalid_action", "missing or unexpected action fields")
    out = copy.deepcopy(value)
    if kind == "cast":
        if ("target_uuid" in value) == ("position" in value):
            reject("invalid_action", "cast requires exactly one entity target or world position")
        out["spell_id"] = token(value["spell_id"], "spell_id")
    for key in ("target_uuid", "leader_uuid"):
        if key in out:
            out[key] = identifier(out[key])
    if "position" in out:
        out["position"] = position(out["position"])
    if kind == "follow":
        out["distance"] = number(value["distance"], "distance", .5, 20)
    if kind == "react":
        out["reaction_id"] = token(value["reaction_id"], "reaction_id")
        if type(value["accept"]) is not bool:
            reject("invalid_action", "reaction accept must be a boolean")
    return out


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def eligible(unit):
    if not isinstance(unit, dict) or any(unit.get(k) is not True for k in ("avatar_verified", "party_verified", "party_member", "host_owned")):
        reject("identity_unknown", "host-owned recruited companion identity is not established")
    if unit.get("avatar") is not False:
        reject("avatar_forbidden", "avatars cannot be controlled")
    if unit.get("dead") is not False:
        reject("actor_unavailable", "actor is dead or death state is unknown")
    identifier(unit.get("uuid"))


UNIT_FIELDS = frozenset(("uuid", "name", "avatar", "avatar_verified", "party_member", "party_verified", "host_owned",
                        "dead", "hp", "max_hp", "position", "in_combat", "active_turn", "can_act", "turn_ended",
                        "relation", "resources", "spells", "spell_details", "cast_options", "visible", "visibility_verified", "interactable"))


def cast_options(value):
    if not isinstance(value, list) or len(value) > 64:
        reject("invalid_snapshot", "cast options must be a bounded list")
    out, seen = [], set()
    required = {"id", "session_id", "revision", "spell_id", "max_targets", "repeat_targets", "target_types", "resource_mode"}
    for item in value:
        if not isinstance(item, dict) or not required <= set(item):
            reject("invalid_snapshot", "cast option identity/target budget missing")
        row = {key: copy.deepcopy(item[key]) for key in required | {"slot_level", "label"} if key in item}
        row["id"], row["revision"], row["spell_id"] = token(row["id"]), token(row["revision"]), token(row["spell_id"])
        if row["id"] in seen or not isinstance(row["session_id"], str) or not 1 <= len(row["session_id"]) <= 128:
            reject("invalid_snapshot", "duplicate or unscoped cast option")
        seen.add(row["id"])
        if type(row["max_targets"]) is not int or not 1 <= row["max_targets"] <= 16 or type(row["repeat_targets"]) is not bool:
            reject("invalid_snapshot", "cast selection budget unverified")
        if (not isinstance(row["target_types"], list) or not row["target_types"]
            or any(not isinstance(t, str) or t not in ("entity", "position") for t in row["target_types"])
            or len(row["target_types"]) != len(set(row["target_types"]))):
            reject("invalid_snapshot", "unsupported cast target selection types")
        if row["resource_mode"] not in ("spell_slot", "pact_slot", "innate", "item", "weapon"):
            reject("invalid_snapshot", "unknown cast resource mode")
        if "slot_level" in row and (type(row["slot_level"]) is not int or not 0 <= row["slot_level"] <= 9):
            reject("invalid_snapshot", "invalid observed slot level")
        if "label" in row and (not isinstance(row["label"], str) or len(row["label"]) > 512):
            reject("invalid_snapshot", "cast option label too long")
        out.append(row)
    return out


def observation(raw, actor_uuid=None):
    """Whitelist public fields; nonparty data needs explicit visibility evidence."""
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("session_id"), str) or not raw["session_id"]:
        reject("invalid_snapshot", "backend snapshot/session identity is invalid")
    def unit(value):
        if not isinstance(value, dict):
            reject("invalid_snapshot", "unit must be an object")
        out = {k: copy.deepcopy(v) for k, v in value.items() if k in UNIT_FIELDS}
        if value.get("uuid") != (raw.get("actor") or {}).get("uuid"):
            out.pop("cast_options", None)
        elif "cast_options" in out:
            out["cast_options"] = cast_options(out["cast_options"])
        if value.get("party_member") is not True or value.get("party_verified") is not True:
            # Visible opponents are observable, not inspectable private character sheets.
            for key in ("resources", "spells", "spell_details", "cast_options", "host_owned", "avatar", "avatar_verified"):
                out.pop(key, None)
        out["uuid"] = identifier(out.get("uuid"))
        if "position" in out:
            out["position"] = position(out["position"])
        return out
    def known(value):
        return isinstance(value, dict) and (
            (value.get("party_verified") is True and value.get("party_member") is True)
            or (value.get("visibility_verified") is True and value.get("visible") is True))
    actor = unit(raw["actor"]) if raw.get("actor") else None
    if actor_uuid and (not actor or actor.get("uuid") != actor_uuid):
        reject("actor_mismatch", "backend returned a different actor")
    party = [unit(u) for u in raw.get("party", []) if isinstance(u, dict) and u.get("party_verified") is True and u.get("party_member") is True]
    combatants = [unit(u) for u in raw.get("combatants", []) if known(u)]
    objects = [unit(u) for u in raw.get("objects", []) if known(u)]
    reactions = []
    for reaction in raw.get("reactions", []):
        if isinstance(reaction, dict) and actor and reaction.get("actor_uuid") == actor["uuid"]:
            reactions.append({k: copy.deepcopy(reaction[k]) for k in ("id", "actor_uuid", "expires_at", "label") if k in reaction})
    out = {"schema_version": 1, "session_id": raw["session_id"], "actor": actor, "party": party,
           "combatants": combatants, "objects": objects, "combat_id": raw.get("combat_id"), "round": raw.get("round"),
           "reactions": reactions, "warnings": list(raw.get("warnings", []))}
    # Fail closed if a backend accidentally produces NaNs/non-JSON data.
    digest(out)
    return out


def validate_context(state, actor_uuid, request):
    actor = state.get("actor")
    eligible(actor)
    if actor["uuid"] != actor_uuid:
        reject("actor_mismatch", "actor changed")
    kind = request["kind"]
    if kind == "react":
        if request["reaction_id"] not in {r.get("id") for r in state["reactions"]}:
            reject("reaction_unavailable", "reaction is not pending for this actor")
    elif actor.get("in_combat") is True:
        if not state.get("combat_id") or actor.get("active_turn") is not True or actor.get("can_act") is not True or actor.get("turn_ended") is not False:
            reject("wrong_turn", "actor cannot act in this combat turn")
    elif actor.get("in_combat") is not False:
        reject("turn_unknown", "combat state is unknown")
    if kind == "end_turn" and actor.get("in_combat") is not True:
        reject("wrong_turn", "there is no combat turn to end")
    if kind in ("follow", "interact") and actor.get("in_combat") is not False:
        reject("combat_restriction", "exploration action unavailable in combat")
    choice = None
    if kind == "cast":
        if request.get("schema_version") == 2:
            choices = [c for c in actor.get("cast_options", []) if c["id"] == request["spell_option_id"]]
            if len(choices) != 1:
                reject("unknown_cast_option", "cast choice is missing or ambiguous")
            choice = choices[0]
            if choice["session_id"] != state["session_id"]:
                reject("stale_cast_option", "cast choice belongs to another session")
            if len(request["targets"]) > choice["max_targets"]:
                reject("target_budget_exceeded", "selection exceeds observed cast budget")
            if not choice["repeat_targets"] and len({digest(t) for t in request["targets"]}) != len(request["targets"]):
                reject("duplicate_target", "this choice does not permit repeated selections")
            for selection in request["targets"]:
                if ("entity" if "entity_uuid" in selection else "position") not in choice["target_types"]:
                    reject("invalid_target_type", "selection is not supported by observed choice")
        if (choice["spell_id"] if choice else request["spell_id"]) not in actor.get("spells", []):
            reject("unknown_spell", "actor does not know this spell")
    units = {u["uuid"]: u for u in [actor] + state["party"] + state["combatants"] + state["objects"]}
    for key in ("target_uuid", "leader_uuid"):
        if key in request and request[key] not in units:
            reject("unknown_target", "target is not in this companion's current known state")
    if choice:
        for selection in request["targets"]:
            if "entity_uuid" in selection and selection["entity_uuid"] not in units:
                reject("unknown_target", "cast selection is outside current permitted observation")
    if kind == "follow":
        leader = units[request["leader_uuid"]]
        if leader.get("party_member") is not True or leader.get("dead") is not False or leader["uuid"] == actor_uuid:
            reject("invalid_leader", "follow requires another living party member")
    if kind == "interact" and units[request["target_uuid"]].get("interactable") is not True:
        reject("invalid_target", "target is not a known interactable")
