"""Deterministic rules fixture. This models scenarios; it is not the BG3 engine."""
from __future__ import annotations

import copy
import math
from collections import deque

from .protocol import ACTIONS, Rejected, action, eligible, observation, validate_context

HOST = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000002"
FRIEND = "00000000-0000-0000-0000-000000000003"
ENEMY = "00000000-0000-0000-0000-000000000004"
HIDDEN = "00000000-0000-0000-0000-000000000005"
CHEST = "00000000-0000-0000-0000-000000000006"
PLAYER3 = "00000000-0000-0000-0000-000000000007"


def unit(uid, name, x, z, party=False, avatar=False, owned=False):
    return {"uuid": uid, "name": name, "avatar": avatar, "avatar_verified": True,
            "party_member": party, "party_verified": True, "host_owned": owned, "dead": False,
            "hp": 20, "max_hp": 20, "position": {"x": x, "y": 0, "z": z}, "in_combat": True,
            "active_turn": uid in (ACTOR, HOST), "can_act": True, "turn_ended": False,
            "visible": True, "visibility_verified": True, "relation": "ally" if party else "enemy",
            "resources": {"ActionPoint": {"0": {"amount": 1, "max": 1}},
                          "BonusActionPoint": {"0": {"amount": 1, "max": 1}},
                          "ReactionActionPoint": {"0": {"amount": 1, "max": 1}},
                          "Movement": {"0": {"amount": 9, "max": 9}},
                          "SpellSlot": {"1": {"amount": 2, "max": 2}, "3": {"amount": 1, "max": 1}}},
            "spells": ["Target_MainHandAttack", "Target_HealingWord", "Projectile_Fireball"]}


def fixture(combat=True):
    actor = unit(ACTOR, "Fourth companion", 0, 0, True, owned=True)
    actor["relation"] = "self"
    host = unit(HOST, "Host", 1, 0, True, True, True)
    friend = unit(FRIEND, "Friend", 2, 0, True, True)
    player3 = unit(PLAYER3, "Third player", 0, 5, True, True)
    friend["hp"] = 10
    enemy = unit(ENEMY, "Visible enemy", 4, 0)
    hidden = unit(HIDDEN, "Hidden enemy", 8, 8)
    hidden["visible"] = False
    chest = {"uuid": CHEST, "name": "Chest", "position": {"x": 2, "y": 0, "z": 1},
             "visible": True, "visibility_verified": True, "interactable": True}
    if not combat:
        for u in (actor, host, friend, player3, enemy, hidden):
            u.update(in_combat=False, active_turn=False)
    return {"schema_version": 1, "session_id": "offline-world-1", "actor": actor, "party": [host, actor, friend, player3],
            "combatants": [host, actor, friend, player3, enemy, hidden] if combat else [enemy, hidden], "objects": [chest],
            "combat_id": "offline-combat" if combat else None, "round": 1 if combat else None, "reactions": [],
            "warnings": ["SIMULATION: no BG3 game connection"], "selected_uuid": HOST}


class SimulationBackend:
    SPELLS = {
        "Target_MainHandAttack": {"range": 1.5, "costs": [("ActionPoint", "0", 1)], "damage": 6, "relation": "enemy"},
        "Target_HealingWord": {"range": 18, "costs": [("BonusActionPoint", "0", 1), ("SpellSlot", "1", 1)], "heal": 5, "relation": "ally"},
        "Projectile_Fireball": {"range": 18, "costs": [("ActionPoint", "0", 1), ("SpellSlot", "3", 1)], "damage": 20, "radius": 3},
    }

    def __init__(self, state=None, deferred=False):
        self.state = copy.deepcopy(state or fixture())
        self.deferred = deferred
        self.requests, self.receipts, self.submissions = {}, {}, []
        self.blocked, self.hazards = set(), set()
        self.tick = 0

    def capabilities(self):
        return {"backend": "simulation", "normal_rules_validated": False, "simulation_only": True,
                "actions": dict.fromkeys(ACTIONS, True), "limitations": ["deterministic scenario model; not proof of BG3 semantics"]}

    def observe(self, actor_uuid=None):
        out = copy.deepcopy(self.state)
        if actor_uuid and actor_uuid != out["actor"]["uuid"]:
            out["actor"] = next((u for u in out["party"] if u["uuid"] == actor_uuid), None)
        return out

    def submit(self, request_id, actor_uuid, value):
        if request_id in self.requests:
            if self.requests[request_id] != (actor_uuid, value):
                raise ValueError("simulation request ID collision")
            return copy.deepcopy(self.receipts[request_id])
        self.requests[request_id] = (actor_uuid, copy.deepcopy(value))
        self.submissions.append(request_id)
        self.receipts[request_id] = {"request_id": request_id, "actor_uuid": actor_uuid, "status": "pending", "backend": "simulation"}
        if not self.deferred:
            self._execute(request_id)
        return copy.deepcopy(self.receipts[request_id])

    def poll(self, request_id):
        if self.receipts[request_id]["status"] == "pending":
            self._execute(request_id)
        return copy.deepcopy(self.receipts[request_id])

    def _units(self):
        return {u["uuid"]: u for u in [self.state["actor"]] + self.state["party"] + self.state["combatants"] + self.state["objects"]}

    @staticmethod
    def _distance(a, b):
        return math.dist([a[k] for k in ("x", "y", "z")], [b[k] for k in ("x", "y", "z")])

    def _path(self, start, end):
        # Bounded grid with deterministic tie-breaking, obstacles and explicit path cost.
        if end["y"] != start["y"] or any(end[k] != int(end[k]) for k in ("x", "z")):
            raise Rejected("path_unavailable", "simulation only models same-height integer grid positions")
        source, target = (int(start["x"]), int(start["z"])), (int(end["x"]), int(end["z"]))
        if target in self.blocked or not all(-20 <= n <= 20 for n in source + target):
            raise Rejected("path_unavailable", "destination is blocked or outside fixture bounds")
        queue, parents = deque([source]), {source: None}
        while queue:
            cell = queue.popleft()
            if cell == target:
                path = []
                while parents[cell] is not None:
                    path.append(cell)
                    cell = parents[cell]
                return list(reversed(path))
            for dx, dz in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                nxt = (cell[0] + dx, cell[1] + dz)
                if nxt not in parents and nxt not in self.blocked and all(-20 <= n <= 20 for n in nxt):
                    parents[nxt] = cell
                    queue.append(nxt)
        raise Rejected("path_unavailable", "no path")

    def _move(self, actor, position, events):
        path = self._path(actor["position"], position)
        movement = actor["resources"]["Movement"]["0"]
        if actor["in_combat"] and len(path) > movement["amount"]:
            raise Rejected("insufficient_movement", "path exceeds movement budget")
        start = copy.deepcopy(actor["position"])
        if actor["in_combat"]:
            movement["amount"] -= len(path)
            # Fixture models one opportunity attack when leaving a living enemy's melee reach.
            for enemy in self._units().values():
                if enemy.get("relation") == "enemy" and not enemy.get("dead") and "resources" in enemy:
                    resource = enemy["resources"]["ReactionActionPoint"]["0"]
                    if resource["amount"] >= 1 and self._distance(start, enemy["position"]) <= 1.5 and self._distance(position, enemy["position"]) > 1.5:
                        resource["amount"] -= 1
                        actor["hp"] = max(0, actor["hp"] - 3)
                        events.append({"kind": "opportunity_attack", "actor_uuid": actor["uuid"], "damage": 3})
        actor["position"] = copy.deepcopy(position)
        actor["hp"] = max(0, actor["hp"] - sum(2 for cell in path if cell in self.hazards))
        actor["dead"] = actor["hp"] == 0
        events.append({"kind": "moved", "position": copy.deepcopy(position), "path_length": len(path)})

    def _cast(self, actor, value, events):
        spell = self.SPELLS.get(value["spell_id"])
        if not spell:
            raise Rejected("unsupported_spell", "spell absent from simulation rules")
        target = self._units().get(value.get("target_uuid"))
        destination = target["position"] if target else value.get("position")
        if destination is None or self._distance(actor["position"], destination) > spell["range"]:
            raise Rejected("out_of_range", "spell target is out of range")
        if "radius" not in spell:
            if not target or target.get("dead") is not False:
                raise Rejected("invalid_target", "requires a living entity target")
            relation = "ally" if target.get("party_member") else target.get("relation")
            if relation != spell["relation"]:
                raise Rejected("invalid_target", "spell target relation is invalid")
        for resource, level, amount in spell["costs"]:
            if actor["resources"].get(resource, {}).get(level, {}).get("amount", 0) < amount:
                raise Rejected("insufficient_resources", "spell costs cannot be paid")
        # All legality/cost checks precede mutations; rejection has no partial deductions.
        for resource, level, amount in spell["costs"]:
            actor["resources"][resource][level]["amount"] -= amount
        targets = [target] if "radius" not in spell else [u for u in self._units().values() if "hp" in u and self._distance(u["position"], destination) <= spell["radius"]]
        for u in targets:
            u["hp"] = min(u["max_hp"], max(0, u["hp"] + spell.get("heal", 0) - spell.get("damage", 0)))
            u["dead"] = u["hp"] == 0
        events.append({"kind": "cast", "spell_id": value["spell_id"], "target_uuid": value.get("target_uuid"), "position": value.get("position")})

    def _execute(self, request_id):
        actor_uuid, value = self.requests[request_id]
        events = []
        try:
            value = action(value)
            public = observation(self.observe(actor_uuid), actor_uuid)
            validate_context(public, actor_uuid, value)
            actor = self.state["actor"]
            eligible(actor)
            if actor["uuid"] != actor_uuid:
                raise Rejected("actor_mismatch", "simulation refuses other actors")
            before = copy.deepcopy(actor)
            kind = value["kind"]
            if kind == "cast":
                self._cast(actor, value, events)
            elif kind == "move":
                self._move(actor, value["position"], events)
            elif kind == "end_turn":
                actor.update(turn_ended=True, active_turn=False)
                events.append({"kind": "turn_ended"})
            elif kind == "react":
                reaction = next(r for r in self.state["reactions"] if r["id"] == value["reaction_id"])
                if reaction.get("expires_at", float("inf")) <= self.tick:
                    raise Rejected("reaction_expired", "reaction already expired")
                resource = actor["resources"]["ReactionActionPoint"]["0"]
                if value["accept"] and resource["amount"] < 1:
                    raise Rejected("insufficient_resources", "reaction resource exhausted")
                if value["accept"]:
                    resource["amount"] -= 1
                self.state["reactions"].remove(reaction)
                events.append({"kind": "reaction_decided", "accepted": value["accept"]})
            elif kind == "follow":
                leader = self._units()[value["leader_uuid"]]
                path = self._path(actor["position"], leader["position"])
                destination = copy.deepcopy(actor["position"])
                for x, z in path:
                    if self._distance(destination, leader["position"]) <= value["distance"]:
                        break
                    destination.update(x=x, z=z)
                self._move(actor, destination, events)
            elif kind == "interact":
                target = self._units()[value["target_uuid"]]
                if self._distance(actor["position"], target["position"]) > 2:
                    raise Rejected("out_of_range", "interaction requires proximity")
                events.append({"kind": "interacted", "target_uuid": target["uuid"]})
            self.tick += 1
            self.receipts[request_id].update(status="completed", events=events, changes={k: {"before": before[k], "after": copy.deepcopy(actor[k])} for k in ("position", "resources", "hp", "active_turn", "turn_ended") if before[k] != actor[k]})
        except Rejected as exc:
            self.receipts[request_id].update(status="rejected", reason=exc.code)
