-- Developer-only normal request research. Never exposed by companion MCP.
local spec = ...
assert(type(spec) == "table" and (spec.operation == "submit" or spec.operation == "poll" or spec.operation == "reconcile"), "invalid operation")
local actor, guid = spec.actor_uuid, spec.request_guid
assert(type(actor) == "string" and type(guid) == "string" and #actor == 36 and #guid == 36, "invalid identity")
assert(guid ~= "00000000-0000-0000-0000-000000000000", "zero request GUID")
local function clock()
    local now = Ext.Timer.MonotonicTime()
    assert(type(now) == "number" and now >= 0, "monotonic clock unavailable")
    return now
end
local function actor_entity()
    local member = false
    for _, row in ipairs(Osi.DB_PartyMembers:Get(nil)) do
        if tostring(row[1]):sub(-36):lower() == actor then member = true end
    end
    assert(member, "actor not recruited")
    for _, row in ipairs(Osi.DB_Avatars:Get(nil)) do
        assert(tostring(row[1]):sub(-36):lower() ~= actor, "avatar forbidden")
    end
    local host_user = Osi.GetReservedUserID(Osi.GetHostCharacter())
    assert(type(host_user) == "number" and host_user > 0 and Osi.GetReservedUserID(actor) == host_user, "ownership unknown")
    assert(Osi.IsDead(actor) == 0, "actor dead or unknown")
    local e = Ext.Entity.Get(actor)
    assert(e ~= nil and e.TurnBased ~= nil, "actor entity unavailable")
    return e
end
local function resources(e)
    local out = {}
    local ok = pcall(function()
        local count = 0
        for id, entries in pairs(e.ActionResources.Resources) do
            count = count + 1
            assert(count <= 64, "resources exceed bound")
            local definition = Ext.StaticData.Get(id, "ActionResource")
            local name = definition and tostring(definition.Name)
            if name == "ActionPoint" or name == "Movement" then
                for _, r in ipairs(entries) do
                    if r.Level == 0 and type(r.Amount) == "number" then
                        assert(out[name] == nil, "ambiguous resource")
                        out[name] = r.Amount
                    end
                end
            end
        end
    end)
    return ok and out or {}
end
if not BG3AC_ResearchExecution then BG3AC_ResearchExecution = {entries={}, count=0} end
local registry = BG3AC_ResearchExecution
registry.traces = registry.traces or {}
local existing = registry.entries[guid]
local function uncertain(entry, reason)
    entry.status, entry.reason = "uncertain", reason
    return entry
end
local function poll(entry, reconciling)
    -- Stored terminal evidence belongs to its historical session. A later load
    -- or reassignment cannot rewrite that result; callers still supply the
    -- exact registry actor/session identity before this function is reached.
    if entry.status == "completed" or entry.status == "rejected" then return entry end
    if entry.status ~= "pending" and not reconciling then return entry end
    if reconciling and (entry.status ~= "uncertain" or entry.action.kind ~= "cast") then return entry end
    if not BG3AC_Observer or BG3AC_Observer.epoch ~= entry.session_id or spec.session_id ~= entry.session_id then
        return uncertain(entry, "session_changed")
    end
    local ok, e = pcall(actor_entity)
    if not ok then return uncertain(entry, "actor_identity_changed") end
    if not reconciling then
        entry.after = resources(e)
        if entry.target_uuid then
            local hp_ok, hp = pcall(Osi.GetHitpoints, entry.target_uuid)
            if hp_ok and type(hp) == "number" then
                entry.target_hp_after = hp
                if type(entry.target_hp_before) == "number" then entry.target_hp_delta = hp - entry.target_hp_before end
            end
        end
        entry.resource_delta = {}
        for _, name in ipairs({"ActionPoint", "Movement"}) do
            if type(entry.before[name]) == "number" and type(entry.after[name]) == "number" then
                entry.resource_delta[name] = entry.after[name] - entry.before[name]
            end
        end
    end
    if entry.action.kind == "end_turn" then
        if e.TurnBased.RequestedEndTurn == true then
            entry.status, entry.reason = "completed", "own_requested_end"
        elseif e.TurnBased.RequestedEndTurn ~= false or e.TurnBased.TurnActionsCompleted ~= false then
            return uncertain(entry, "end_turn_state_changed")
        end
        if entry.status == "pending" and clock() >= entry.deadline_ms then return uncertain(entry, "request_deadline_elapsed") end
        return entry
    end
    local trace = BG3AC_RequestTrace
    if reconciling and (registry.traces[guid] ~= trace or not trace or trace.active ~= true) then
        return uncertain(entry, "reconcile_trace_continuity_unknown")
    end
    if not trace or trace.actor_uuid ~= actor or trace.session_id ~= entry.session_id
        or trace.invalidated or trace.truncated or trace.expired or #trace.errors > 0 or #trace.records > 96 then
        return uncertain(entry, "trace_incomplete")
    end
    local events = {}
    local previous = 0
    for _, row in ipairs(trace.records) do
        if type(row.seq) ~= "number" or row.seq <= previous then return uncertain(entry, "trace_sequence_invalid") end
        previous = row.seq
        if row.seq > entry.trace_seq and (row.request_guid == guid or row.cast_guid == guid) then
            if row.kind ~= "queued" and row.kind ~= "started" and row.kind ~= "queue_departed"
                and row.kind ~= "finished" and row.kind ~= "outcome" then
                return uncertain(entry, "correlated_event_unknown")
            end
            if row.actor_uuid ~= actor or (row.kind ~= "queue_departed" and row.spell ~= entry.action.spell_id) then
                return uncertain(entry, "correlated_identity_mismatch")
            end
            if events[row.kind] then return uncertain(entry, "correlated_events_ambiguous") end
            events[row.kind] = row
        end
    end
    entry.evidence = events
    local q, s, f, o = events.queued, events.started, events.finished, events.outcome
    if f or o then
        if not (q and s and f and o) then
            if not trace.active then return uncertain(entry, "trace_stopped_without_full_result") end
            if clock() >= entry.deadline_ms then return uncertain(entry, "request_deadline_elapsed") end
            return entry -- wait for all callbacks
        end
        if q.queue ~= "NetworkStartRequests" or not (q.seq < s.seq and s.seq < f.seq and s.seq < o.seq)
            or s.cast_guid ~= guid or f.cast_guid ~= guid or o.cast_guid ~= guid
            or not s.cast_entity or f.cast_entity ~= s.cast_entity or o.cast_entity ~= s.cast_entity
            or f.result ~= o.result then
            return uncertain(entry, "correlated_lifecycle_mismatch")
        end
        if o.result == "None" then entry.status = "completed"
        elseif o.result == "CantCast" then entry.status = "rejected"
        else return uncertain(entry, "unvalidated_terminal_result") end
        entry.engine_result, entry.reason, entry.cast_guid = o.result, "correlated_engine_result", guid
        if reconciling then entry.reconciled = true end
        entry.casted_spell_observed = false
        for _, cast in ipairs(BG3AC_Observer.casts or {}) do
            if cast.seq > entry.event_seq and cast.caster == actor and cast.spell == entry.action.spell_id then
                entry.casted_spell_observed = true
            end
        end
    elseif not trace.active then return uncertain(entry, "trace_stopped_without_result") end
    if entry.status == "pending" and clock() >= entry.deadline_ms then return uncertain(entry, "request_deadline_elapsed") end
    return entry
end
if spec.operation == "poll" or spec.operation == "reconcile" then
    assert(existing and existing.actor_uuid == actor and existing.session_id == spec.session_id, "unknown request identity")
    return poll(existing, spec.operation == "reconcile")
end
assert(spec.execution_opt_in == true, "execution not opted in")
local value = spec.action
assert(type(value) == "table" and (value.kind == "end_turn" or value.kind == "cast"), "unsupported action")
local targeted = value.kind == "cast" and value.spell_id == "Projectile_MainHandAttack"
if value.kind == "cast" then
    assert(value.position == nil and ((value.target_uuid == actor
        and (value.spell_id == "Shout_Hide" or value.spell_id == "Shout_Dash"))
        or (targeted and type(value.target_uuid) == "string" and value.target_uuid ~= actor)), "unsupported cast")
    if targeted then
        local known = spec.known_target
        assert(type(known) == "table" and known.actor_uuid == actor and known.session_id == spec.session_id
            and known.target_uuid == value.target_uuid and known.source == "ordinary_player_observation", "target knowledge missing or mismatched")
    end
end
local payload_key = actor .. "|" .. spec.session_id .. "|" .. value.kind .. "|" .. (value.spell_id or "")
if targeted then payload_key = payload_key .. "|" .. value.target_uuid end
if existing then
    assert(existing.payload_key == payload_key, "request GUID payload conflict")
    return poll(existing)
end
assert(BG3AC_Observer and BG3AC_Observer.epoch == spec.session_id, "session changed")
assert(registry.count < 64, "research registry full; entries never evicted automatically")
for _, entry in pairs(registry.entries) do
    assert(entry.actor_uuid ~= actor or entry.session_id ~= spec.session_id
        or (entry.status ~= "pending" and entry.status ~= "uncertain"),
        "another request for this actor is unresolved")
end
local e = actor_entity()
assert(Osi.IsInCombat(actor) == 1, "not in combat")
local turn = e.TurnBased
assert(turn.IsActiveCombatTurn == true and turn.CanActInCombat == true, "wrong turn")
assert(turn.RequestedEndTurn == false and turn.TurnActionsCompleted == false, "turn ended or unknown")
local queue, request, trace, target, target_hp
local targeting_type = targeted and "Projectile" or "Shout"
if targeted then
    target = Ext.Entity.Get(value.target_uuid)
    assert(target ~= nil and Osi.IsDead(value.target_uuid) == 0 and Osi.IsEnemy(actor, value.target_uuid) == 1
        and Osi.IsInCombat(value.target_uuid) == 1, "target not a living combat enemy")
    for _, row in ipairs(Osi.DB_PartyMembers:Get(nil)) do
        assert(tostring(row[1]):sub(-36):lower() ~= value.target_uuid, "party target forbidden")
    end
    for _, row in ipairs(Osi.DB_Avatars:Get(nil)) do
        assert(tostring(row[1]):sub(-36):lower() ~= value.target_uuid, "avatar target forbidden")
    end
    local own_combat = e.CombatParticipant.CombatHandle.CombatState.MyGuid
    local target_combat = target.CombatParticipant.CombatHandle.CombatState.MyGuid
    assert(own_combat ~= nil and target_combat ~= nil and tostring(own_combat) == tostring(target_combat), "target in different combat")
    local handle = Ext.Entity.UuidToHandle(value.target_uuid)
    assert(handle ~= nil and e.ServerViewshedParticipant.CanSee[handle] == true, "target outside current LOS")
    -- These are restrictive research checks, not a validated stealth adapter.
    -- Named server flags are reflected directly on ServerCharacter in SE v32.
    assert(target.ServerCharacter.Invisible == false and target.ServerCharacter.OffStage == false
        and target.Darkness.CharacterSneaking == false and target.Darkness.ActuallySneaking == false
        and target.Darkness.Sneaking == 0 and target.Invisibility == nil, "target concealed or concealment unknown")
    target_hp = Osi.GetHitpoints(value.target_uuid)
    assert(type(target_hp) == "number" and target_hp > 0, "target HP unknown")
end
if value.kind == "end_turn" then
    queue, request = Ext.System.ServerTurnOrder.EndTurn, e
else
    trace = BG3AC_RequestTrace
    assert(trace and trace.active and trace.actor_uuid == actor and trace.session_id == spec.session_id
        and not trace.invalidated and not trace.truncated and not trace.expired and #trace.errors == 0
        and #trace.records <= 88, "active bounded trace required")
    local spell, count = nil, 0
    for _, s in ipairs(e.SpellBook.Spells) do
        if tostring(s.Id.Prototype) == value.spell_id then spell, count = s.Id, count + 1 end
    end
    assert(count == 1, "spell absent or source ambiguous")
    for _, field in ipairs({"Prototype", "OriginatorPrototype", "SourceType", "Source", "ProgressionSource"}) do
        assert(spell[field] ~= nil, "missing spell metadata")
    end
    local stats = Ext.Stats.Get(value.spell_id)
    assert(stats and tostring(stats.SpellType) == targeting_type, "targeting type changed")
    if targeted then assert(tostring(spell.SourceType) == "WeaponSpell", "weapon spell source changed") end
    queue = Ext.System.ServerCastRequest.NetworkStartRequests
    request = {Spell=spell, CastOptions={"Immediate", "FromClient"}, Caster=e,
        Targets={{Target=target or e, TargetingType=targeting_type}},
        Originator={ActionGuid="00000000-0000-0000-0000-000000000000", PassiveId="", StatusId="", InterruptId="", CanApplyConcentration=true},
        StoryActionId=0, NetGuid="", field_A8=1, RequestGuid=guid}
end
assert(#queue == 0, "request queue busy")
local entry = {request_guid=guid, actor_uuid=actor, session_id=spec.session_id, payload_key=payload_key,
    action=value, status="pending", reason="reserved_before_append", execution_enabled=false,
    developer_submission=true, retry_allowed=false, costs_verified=false, full_round_completed=false,
    before=resources(e), event_seq=BG3AC_Observer.seq, trace_seq=trace and trace.seq or 0,
    deadline_ms=clock()+60000, target_uuid=targeted and value.target_uuid or nil,
    target_hp_before=target_hp, knowledge_source=targeted and spec.known_target.source or nil,
    visibility_verified=false}
registry.entries[guid], registry.count = entry, registry.count + 1
if trace then registry.traces[guid] = trace end
local ok = pcall(function() queue[#queue+1] = request end)
entry.append_ok = ok
if not ok then return uncertain(entry, "append_outcome_unknown") end
entry.reason = "submitted_once"
return entry
