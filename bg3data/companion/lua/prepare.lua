-- Read-only metadata resolution. This does not append, cast or end a turn.
local spec = ...
assert(type(spec) == "table", "invalid preparation spec")
local actor = spec.actor_uuid
assert(BG3AC_Observer and BG3AC_Observer.epoch == spec.session_id, "session changed")
local member = false
for _, row in ipairs(Osi.DB_PartyMembers:Get(nil)) do
    if tostring(row[1]):sub(-36):lower() == actor then member = true end
end
assert(member, "actor is not recruited")
for _, row in ipairs(Osi.DB_Avatars:Get(nil)) do
    assert(tostring(row[1]):sub(-36):lower() ~= actor, "avatar forbidden")
end
local host = Osi.GetHostCharacter()
local host_user = Osi.GetReservedUserID(host)
assert(type(host_user) == "number" and host_user > 0 and Osi.GetReservedUserID(actor) == host_user, "ownership unknown")
assert(Osi.IsDead(actor) == 0 and Osi.IsInCombat(actor) == 1, "actor unavailable")
local entity = Ext.Entity.Get(actor)
assert(entity and entity.TurnBased, "turn state missing")
local turn = entity.TurnBased
assert(turn.IsActiveCombatTurn == true and turn.CanActInCombat == true, "not an active turn")
assert(turn.RequestedEndTurn == false and turn.TurnActionsCompleted == false, "turn already ended or unknown")
local value = spec.action
local plan = {actor_uuid=actor, session_id=spec.session_id, request_guid=spec.request_guid,
    action=value, execution_enabled=false, engine_legality="unvalidated", prepared=true}
if value.kind == "end_turn" then
    local queue = Ext.System.ServerTurnOrder.EndTurn
    assert(#queue == 0, "end-turn queue busy")
    plan.queue = "ServerTurnOrder.EndTurn"
    return plan
end
assert(value.kind == "cast" and type(value.target_uuid) == "string" and value.position == nil, "unsupported target")
-- Party membership is independently established here; arbitrary visible-looking
-- UUIDs do not establish knowledge of an enemy. Position targeting is not yet
-- validated. Target conditions, range and hit validity remain unvalidated.
local target_member = false
for _, row in ipairs(Osi.DB_PartyMembers:Get(nil)) do
    if tostring(row[1]):sub(-36):lower() == value.target_uuid then target_member = true end
end
assert(target_member, "target is not a known recruited party member")
assert(Osi.IsDead(value.target_uuid) == 0, "target dead or death state unknown")
local target = Ext.Entity.Get(value.target_uuid)
assert(target ~= nil, "target entity unavailable")
local spell_id = nil
local candidates = 0
for _, spell in ipairs(entity.SpellBook.Spells) do
    if tostring(spell.Id.Prototype) == value.spell_id then
        candidates = candidates + 1
        spell_id = spell.Id
    end
end
assert(candidates == 1, "spell missing or source ambiguous")
local stats = Ext.Stats.Get(value.spell_id)
assert(stats ~= nil, "spell stats unavailable")
local targeting_type = tostring(stats.SpellType)
assert((targeting_type == "Shout" and value.target_uuid == actor)
    or targeting_type == "Target", "targeting type not validated")
local queue = Ext.System.ServerCastRequest.NetworkStartRequests
assert(#queue == 0, "cast queue busy")
local function metadata(field)
    local v = spell_id[field]
    assert(v ~= nil, "spell metadata missing: " .. field)
    return tostring(v)
end
-- Preserve actual source identity; never infer it from only the prototype.
plan.queue = "ServerCastRequest.NetworkStartRequests"
plan.spell = {Prototype=metadata("Prototype"), OriginatorPrototype=metadata("OriginatorPrototype"),
    SourceType=metadata("SourceType"), Source=metadata("Source"), ProgressionSource=metadata("ProgressionSource")}
plan.targeting_type = targeting_type
-- This describes the target for later same-call resolution; it is deliberately
-- not an EntityHandle serialized across calls or an enqueue-ready request.
plan.target = {uuid=value.target_uuid, knowledge="recruited_party", targeting_type=targeting_type}
plan.cast_options = {"Immediate", "FromClient"}
plan.originator = {ActionGuid="00000000-0000-0000-0000-000000000000",
    CanApplyConcentration=true, PassiveId="", StatusId="", InterruptId=""}
plan.net_guid = ""
plan.story_action_id = 0
plan.field_A8 = 1
-- Resource, target condition and game legality checks remain the engine's job.
-- This plan is research metadata, not a successful or rejected action receipt.
return plan
