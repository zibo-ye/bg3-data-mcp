-- Developer-only ordinary interaction capture; no task calls or queue writes.
local spec = ...
assert(type(spec) == "table" and (spec.side == "server" or spec.side == "client"), "invalid interaction spec")
assert(spec.operation == "install" or spec.operation == "read" or spec.operation == "stop", "invalid operation")
local server = spec.side == "server"
assert((server and Ext.IsServer() == true) or (not server and Ext.IsClient() == true), "wrong engine context")
local function finite(v, lo, hi)
    return type(v) == "number" and v == v and v >= lo and v <= hi
end
local function canonical(value)
    return type(value) == "string" and #value == 36 and value == value:lower()
        and value:match("^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$")
end
assert(canonical(spec.actor_uuid) and canonical(spec.target_uuid) and spec.actor_uuid ~= spec.target_uuid, "invalid entity UUID")
assert(type(spec.session_id) == "string" and #spec.session_id > 0 and #spec.session_id <= 128, "invalid session")
local function matches(value, expected)
    if value == nil then return false end
    local ok, id = pcall(function()
        local entity = value
        local direct = pcall(function() return entity.Uuid.EntityUuid end)
        if not direct then entity=Ext.Entity.Get(value) end
        return entity and tostring(entity.Uuid.EntityUuid):sub(-36):lower()
    end)
    return ok and id == expected
end
local function server_authority()
    assert(BG3AC_Observer and BG3AC_Observer.epoch == spec.session_id, "session changed")
    local member = false
    for _, row in ipairs(Osi.DB_PartyMembers:Get(nil)) do
        if tostring(row[1]):sub(-36):lower() == spec.actor_uuid then member = true end
    end
    assert(member, "actor not recruited")
    for _, row in ipairs(Osi.DB_Avatars:Get(nil)) do
        assert(tostring(row[1]):sub(-36):lower() ~= spec.actor_uuid, "avatar forbidden")
    end
    local user = Osi.GetReservedUserID(Osi.GetHostCharacter())
    assert(finite(user, 1, 4294967295) and user % 1 == 0
        and Osi.GetReservedUserID(spec.actor_uuid) == user, "ownership unknown")
    assert(Osi.IsDead(spec.actor_uuid) == 0, "actor unavailable")
    return user
end
local function client_actor(user)
    assert(finite(user, 1, 4294967295) and user % 1 == 0, "server authority missing")
    local entity = Ext.Entity.Get(spec.actor_uuid)
    assert(entity and entity.ClientCharacter and entity.ClientCharacter.ReservedUserID == user, "client reservation changed")
    assert(entity.ClientCharacter.InputController, "client controller missing")
    return entity.ClientCharacter.InputController
end
local existing = BG3AC_InteractionTrace
if existing and (spec.operation ~= "install" or existing.active) then
    assert(existing.actor_uuid == spec.actor_uuid and existing.target_uuid == spec.target_uuid
        and existing.session_id == spec.session_id, "another interaction trace is installed")
end
local function stop(trace)
    trace.active = false
    for _, sub in ipairs(trace.subscriptions) do
        local ok = pcall(function() sub.event:Unsubscribe(sub.id) end)
        if not ok then trace.cleanup_failed = true end
    end
    trace.subscriptions = {}
end
if spec.operation == "stop" then
    if existing then stop(existing) end
elseif spec.operation == "install" then
    assert(not existing or not existing.active, "trace already installed")
    local user = server and server_authority() or spec.host_user
    if not server then client_actor(user) end
    assert(Ext.Entity.Get(spec.target_uuid), "supplied target unavailable")
    local trace = {actor_uuid=spec.actor_uuid, target_uuid=spec.target_uuid, session_id=spec.session_id,
        side=spec.side, host_user=user, active=true, records={}, errors={}, subscriptions={}, ticks=0, last={}}
    local function record(value)
        if #trace.records >= 96 then trace.truncated=true; stop(trace); return end
        value.tick, value.seq = trace.ticks, #trace.records+1
        trace.records[#trace.records+1] = value
    end
    local function changed(key, value)
        local encoded = Ext.Json.Stringify(value)
        if trace.last[key] ~= encoded then trace.last[key]=encoded; record(value) end
    end
    local function sample()
        if server then server_authority() else client_actor(user) end
        local target = Ext.Entity.Get(spec.target_uuid)
        assert(target, "supplied target disappeared")
        changed("target", {kind="target_markers", target_uuid=spec.target_uuid,
            is_door=target.IsDoor ~= nil, has_opened_marker=target.HasOpened ~= nil,
            in_use_marker=target.ItemInUse ~= nil, interaction_disabled_marker=target.InteractionDisabled ~= nil})
        if server then
            -- This inventory notification array is a candidate trace, not a general use API.
            local available, system = pcall(function() return Ext.System.ServerInventoryInteractionRequest end)
            if not available or not system then
                changed("queue", {kind="inventory_notify", available=false}); return
            end
            local queue = system.NotifyInteraction
            local complete, count = #queue <= 64, 0
            for index=1, math.min(#queue, 64) do
                local request=queue[index]
                if matches(request.Actor, spec.actor_uuid) and matches(request.Container, spec.target_uuid) then count=count+1 end
            end
            if not complete then trace.queue_truncated=true end
            changed("queue", {kind="inventory_notify", available=true, sample_complete=complete,
                matching_count=count, departure_is_not_completion=true})
        else
            local controller = client_actor(user)
            local types = {ItemUse=true, ItemUseRemotely=true, ItemInteractArea=true, DefaultTargetAction=true,
                Dialog=true, Loot=true, PickUp=true, Lockpick=true, DisarmTrap=true}
            local numeric = {[5]="Dialog",[6]="ItemUseRemotely",[7]="ItemUse",[9]="ItemInteractArea",
                [15]="Lockpick",[16]="DisarmTrap",[18]="PickUp",[20]="Loot",[22]="DefaultTargetAction"}
            for _, role in ipairs({"PreviewTask", "RunningTask"}) do
                local task = controller[role]
                local kind = task and (numeric[task.TaskType] or tostring(task.TaskType))
                local present = false
                if task and types[kind] and task.CharacterEntity and matches(task.CharacterEntity.Handle, spec.actor_uuid) then
                    if kind == "ItemUse" or kind == "PickUp" then present=matches(task.Item, spec.target_uuid)
                    elseif kind == "ItemUseRemotely" then present=matches(task.HighlightTarget, spec.target_uuid)
                    elseif kind == "ItemInteractArea" then
                        local target_entity=Ext.Entity.Get(spec.target_uuid)
                        present=task.Items[target_entity] ~= nil
                    else present=matches(task.Target, spec.target_uuid) end
                end
                local value={kind="client_task", role=role, matching_target=present}
                if present then
                    value.task_type=kind
                    if type(task.Ready)=="boolean" then value.ready=task.Ready end
                    if type(task.Previewing)=="boolean" then value.previewing=task.Previewing end
                    local ok1, transaction = pcall(function() return task.BehaviourTransactionId end)
                    if not ok1 or transaction == nil then
                        local ok2, movement = pcall(function() return task.MoveBehaviourTransactionId end)
                        transaction=ok2 and movement or nil
                    end
                    if finite(transaction, -2147483648, 2147483647) and transaction%1==0 then value.transaction_id=transaction end
                end
                changed(role, value)
            end
        end
    end
    local function guarded_sample()
        if not trace.active then return end
        trace.ticks=trace.ticks+1
        if trace.ticks>3600 then trace.expired=true; stop(trace); return end
        local allowed=pcall(function() if server then server_authority() else client_actor(user) end end)
        if not allowed then trace.invalidated=true; stop(trace); return end
        local ok=pcall(sample)
        if not ok then
            trace.errors[#trace.errors+1]="sample_failed"
            if #trace.errors>=3 then stop(trace) end
        end
    end
    local ok = pcall(function()
        sample()
        local tick=Ext.Events.Tick
        trace.subscriptions[#trace.subscriptions+1]={event=tick,id=tick:Subscribe(guarded_sample)}
        local loaded=Ext.Events.SessionLoaded
        trace.subscriptions[#trace.subscriptions+1]={event=loaded,id=loaded:Subscribe(function()
            trace.invalidated=true; stop(trace)
        end)}
    end)
    if not ok then stop(trace); error("interaction trace installation failed") end
    BG3AC_InteractionTrace=trace
    existing=trace
else
    assert(existing, "interaction trace not installed")
    if existing.active then
        local ok=pcall(function() if server then server_authority() else client_actor(spec.host_user) end end)
        if not ok then existing.invalidated=true; stop(existing) end
    end
end
if not existing then
    return {active=false, records={}, errors={}, execution_enabled=false}
end
return {actor_uuid=existing.actor_uuid, target_uuid=existing.target_uuid, session_id=existing.session_id,
    side=existing.side, host_user=existing.host_user, active=existing.active, records=existing.records,
    errors=existing.errors, ticks=existing.ticks, truncated=existing.truncated or false,
    queue_truncated=existing.queue_truncated or false, invalidated=existing.invalidated or false,
    expired=existing.expired or false, cleanup_failed=existing.cleanup_failed or false, execution_enabled=false}
