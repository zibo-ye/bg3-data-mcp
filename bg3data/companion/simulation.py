"""Deterministic rules fixture. This models scenarios; it is not the BG3 engine."""
from __future__ import annotations

import copy
import math
from collections import deque

from .protocol import ACTIONS, Rejected, action, digest, eligible, observation, validate_context

HOST = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000002"
FRIEND = "00000000-0000-0000-0000-000000000003"
ENEMY = "00000000-0000-0000-0000-000000000004"
HIDDEN = "00000000-0000-0000-0000-000000000005"
CHEST = "00000000-0000-0000-0000-000000000006"
PLAYER3 = "00000000-0000-0000-0000-000000000007"
THROW_WEAPON = "00000000-0000-0000-0000-000000000100"
THROW_CONSUMABLE = "00000000-0000-0000-0000-000000000101"
THROW_RETURNING = "00000000-0000-0000-0000-000000000102"


def _inventory_identity(actor_uuid, item):
    return "inventory_" + digest({"actor":actor_uuid, "instance":item})[:48]


def _inventory_catalog_record(item):
    return {k:copy.deepcopy(v) for k,v in item.items() if k not in ("session_id", "revision", "owner_uuid", "ownership_verified", "quantity")}


def fixture_inventory():
    out=[]
    for uid,label,quantity,mode,returning,damage in (
        (THROW_WEAPON,"Fixture throwing dagger",1,"weapon",False,5),
        (THROW_CONSUMABLE,"Fixture throwable consumable",2,"consumable",False,4),
        (THROW_RETURNING,"Fixture returning dagger",1,"weapon",True,5)):
        out.append(dict(item_uuid=uid,label=label,quantity=quantity,session_id="offline-world-1",revision="inventory_1",
                        owner_uuid=ACTOR,ownership_verified=True,throw_eligible=True,target_types=["entity","position"],
                        _source_record="fixture_source_"+uid[-3:],_mode=mode,_return_to_owner=returning,
                        _range=12,_damage=damage,_action_point_cost=1))
    return out


def _choice_identity(actor_uuid, option):
    return "cast_" + digest({"actor": actor_uuid, "option": {k:v for k,v in option.items() if k != "id"}})[:48]


def _catalog_record(option):
    return {k:copy.deepcopy(v) for k,v in option.items() if k not in ("id", "session_id", "revision", "label")}


def fixture_cast_options(session_id="offline-world-1", revision="spellbook_1"):
    # Explicit fixture choices, not inferred native prototypes or source GUIDs.
    definitions = [
        ("Target_MainHandAttack", "weapon", None, 1, False, ["entity"], "fixture_weapon_a"),
        ("Target_MainHandAttack", "weapon", None, 1, False, ["entity"], "fixture_weapon_b"),
        ("Target_HealingWord", "spell_slot", 1, 1, False, ["entity"], "fixture_book_heal_1"),
        ("Fixture_HealingWord_Level3", "spell_slot", 3, 1, False, ["entity"], "fixture_book_heal_3"),
        ("Projectile_Fireball", "spell_slot", 3, 1, False, ["position"], "fixture_book_fireball"),
        ("Projectile_MagicMissile", "spell_slot", 1, 3, True, ["entity"], "fixture_book_missiles_1"),
        ("Fixture_MagicMissile_Level3", "spell_slot", 3, 5, True, ["entity"], "fixture_book_missiles_3"),
    ]
    options = []
    for spell, mode, slot, budget, repeat, types, source in definitions:
        option = dict(session_id=session_id, revision=revision, spell_id=spell, resource_mode=mode,
                      max_targets=budget, repeat_targets=repeat, target_types=types, _source_record=source)
        if slot is not None: option["slot_level"] = slot
        option["id"] = _choice_identity(ACTOR, option)
        options.append(option)
    return options


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
    actor["cast_options"] = fixture_cast_options()
    actor["inventory_items"] = fixture_inventory()
    actor["spells"] += ["Fixture_HealingWord_Level3", "Projectile_MagicMissile", "Fixture_MagicMissile_Level3"]
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
        "Fixture_HealingWord_Level3": {"range": 18, "costs": [("BonusActionPoint", "0", 1), ("SpellSlot", "3", 1)], "heal": 10, "relation": "ally"},
        "Projectile_MagicMissile": {"range": 18, "costs": [("ActionPoint", "0", 1), ("SpellSlot", "1", 1)], "damage": 4, "relation": "enemy", "multiple": True},
        "Fixture_MagicMissile_Level3": {"range": 18, "costs": [("ActionPoint", "0", 1), ("SpellSlot", "3", 1)], "damage": 4, "relation": "enemy", "multiple": True},
    }

    def __init__(self, state=None, deferred=False):
        self.state = copy.deepcopy(state or fixture())
        self.deferred = deferred
        self.requests, self.receipts, self.submissions = {}, {}, []
        self.blocked, self.hazards = set(), set()
        self.tick = 0
        if len(self.state['actor'].get('inventory_items',[]))>64:
            raise Rejected('invalid_snapshot','fixture inventory exceeds bound')
        self.cast_catalog = {option["_source_record"]: _catalog_record(option)
                             for option in self.state["actor"].get("cast_options", []) if "_source_record" in option}
        self.inventory_catalog = {item["item_uuid"]:_inventory_catalog_record(item) for item in self.state["actor"].get("inventory_items", [])}
        self.world_items=[]

    def capabilities(self):
        return {"backend": "simulation", "normal_rules_validated": False, "simulation_only": True,
                "actions": dict.fromkeys(ACTIONS, True), "action_contract_versions": [1, 2],
                "action_features": {"cast_multiple_targets": True, "cast_upcast_options": True,
                                    "throw_inventory_item": True, "dialogue_select": False},
                "limitations": ["deterministic scenario model; not proof of BG3 semantics"]}

    def observe(self, actor_uuid=None):
        if len(self.state['actor'].get('inventory_items',[]))>64:
            raise Rejected('invalid_snapshot','fixture inventory exceeds bound')
        for option in self.state["actor"].get("cast_options", []):
            # Bind token to current exact fixture source, session and revision;
            # metadata changes cannot silently reuse a formerly observed ID.
            option["id"] = _choice_identity(self.state["actor"]["uuid"], option)
        out = copy.deepcopy(self.state)
        options=[]
        for item in self.state["actor"].get("inventory_items", []):
            if item.get("owner_uuid") != self.state["actor"]["uuid"] or item.get("ownership_verified") is not True or item.get("throw_eligible") is not True:
                continue
            if type(item.get("quantity")) is int and item["quantity"] == 0: continue
            option={key:copy.deepcopy(item[key]) for key in ("session_id", "revision", "item_uuid", "label", "quantity", "owner_uuid", "ownership_verified", "throw_eligible", "target_types") if key in item}
            option.update(id=_inventory_identity(self.state["actor"]["uuid"],item),range=item.get("_range"),action_point_cost=item.get("_action_point_cost"))
            options.append(option)
        out["actor"]["inventory_options"]=options
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
        v2 = value.get("schema_version") == 2
        choice = next((c for c in actor.get("cast_options", []) if c["id"] == value.get("spell_option_id")), None) if v2 else None
        if v2 and (choice is None or choice["session_id"] != self.state["session_id"]
                   or _choice_identity(actor["uuid"], choice) != value["spell_option_id"]
                   or self.cast_catalog.get(choice.get("_source_record")) != _catalog_record(choice)):
            raise Rejected("cast_choice_changed", "exact private fixture source/choice identity changed before costs")
        spell_id = choice["spell_id"] if choice else value["spell_id"]
        selections = value["targets"] if v2 else [{"entity_uuid":value["target_uuid"]} if "target_uuid" in value else {"position":value["position"]}]
        spell = self.SPELLS.get(spell_id)
        if not spell:
            raise Rejected("unsupported_spell", "spell absent from simulation rules")
        targets = []
        for selection in selections:
            target = self._units().get(selection.get("entity_uuid"))
            destination = target["position"] if target else selection.get("position")
            if destination is None or self._distance(actor["position"], destination) > spell["range"]:
                raise Rejected("out_of_range", "spell target is out of range")
            if "radius" not in spell:
                if not target or target.get("dead") is not False:
                    raise Rejected("invalid_target", "requires a living entity target")
                relation = "ally" if target.get("party_member") else target.get("relation")
                if relation != spell["relation"]:
                    raise Rejected("invalid_target", "spell target relation is invalid")
                targets.append(target)
            else:
                targets.extend(u for u in self._units().values() if "hp" in u and self._distance(u["position"], destination) <= spell["radius"])
        for resource, level, amount in spell["costs"]:
            if actor["resources"].get(resource, {}).get(level, {}).get("amount", 0) < amount:
                raise Rejected("insufficient_resources", "spell costs cannot be paid")
        # All legality/cost checks precede mutations; rejection has no partial deductions.
        for resource, level, amount in spell["costs"]:
            actor["resources"][resource][level]["amount"] -= amount
        for u in targets:
            u["hp"] = min(u["max_hp"], max(0, u["hp"] + spell.get("heal", 0) - spell.get("damage", 0)))
            u["dead"] = u["hp"] == 0
        event = {"kind": "cast", "spell_id": spell_id}
        if v2: event.update(spell_option_id=value["spell_option_id"], targets=copy.deepcopy(selections))
        else: event.update(target_uuid=value.get("target_uuid"), position=value.get("position"))
        events.append(event)

    def _throw(self, actor, value, events):
        items=[i for i in actor.get("inventory_items", []) if _inventory_identity(actor["uuid"],i)==value["inventory_option_id"]]
        if len(items)!=1: raise Rejected("inventory_choice_changed", "exact item instance/stack changed before throw")
        item=items[0]
        if (item.get("session_id")!=self.state["session_id"] or item.get("owner_uuid")!=actor["uuid"]
            or item.get("ownership_verified") is not True or item.get("throw_eligible") is not True
            or self.inventory_catalog.get(item.get("item_uuid"))!=_inventory_catalog_record(item)):
            raise Rejected("inventory_choice_changed", "item source/ownership changed before throw")
        quantity=item.get("quantity")
        if type(quantity) is not int or quantity<1: raise Rejected("item_unavailable", "item stack is empty or unknown")
        if item['_mode'] not in ('weapon','consumable') or item['_mode']=='weapon' and quantity!=1:
            raise Rejected("unsupported_item_stack", "fixture does not split weapon stacks")
        selection=value['target']; target=self._units().get(selection.get('entity_uuid'))
        destination=target['position'] if target else selection.get('position')
        if target is not None and ('hp' not in target or target.get('dead') is not False):
            raise Rejected('invalid_target','fixture throw requires living character or world position')
        if destination is None or self._distance(actor['position'],destination)>item['_range']:
            raise Rejected('out_of_range','throw target is outside fixture range')
        if (round(destination['x']),round(destination['z'])) in self.blocked:
            raise Rejected('path_unavailable','fixture throw destination is blocked')
        resource=actor['resources'].get('ActionPoint',{}).get('0',{})
        if resource.get('amount',0)<item['_action_point_cost']:
            raise Rejected('insufficient_resources','throw cost cannot be paid')
        # All instance, source, target and cost checks precede every mutation.
        resource['amount']-=item['_action_point_cost']
        disposition='returned' if item['_return_to_owner'] else ('consumed' if item['_mode']=='consumable' else 'dropped')
        if disposition!='returned': item['quantity']-=1
        if disposition=='dropped':
            item['owner_uuid']=None; item['ownership_verified']=False
            self.world_items.append(dict(item_uuid=item['item_uuid'],quantity=1,position=copy.deepcopy(destination)))
        item['revision']='inventory_'+str(self.tick+2)
        if target is not None:
            target['hp']=max(0,target['hp']-item['_damage']); target['dead']=target['hp']==0
        events.append(dict(kind='thrown',inventory_option_id=value['inventory_option_id'],item_uuid=item['item_uuid'],
                           quantity_before=quantity,quantity_after=item['quantity'],disposition=disposition,
                           target=copy.deepcopy(selection),destination=copy.deepcopy(destination)))

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
            elif kind == "throw":
                self._throw(actor, value, events)
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
