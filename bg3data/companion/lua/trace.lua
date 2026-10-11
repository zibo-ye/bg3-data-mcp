-- Developer-only observation hooks. No request writes or game action execution.
local actor, operation = ...
local function id(e)
    if not e then return nil end
    local ok, value = pcall(function() return tostring(e.Uuid.EntityUuid):sub(-36):lower() end)
    return ok and value or nil
end
local function actor_allowed()
    local host = Osi.GetHostCharacter()
    local host_user = Osi.GetReservedUserID(host)
    assert(type(host_user) == "number" and host_user > 0 and Osi.GetReservedUserID(actor) == host_user, "trace ownership unknown")
    for _, row in ipairs(Osi.DB_Avatars:Get(nil)) do
        assert(tostring(row[1]):sub(-36):lower() ~= actor, "trace avatar forbidden")
    end
    for _, row in ipairs(Osi.DB_PartyMembers:Get(nil)) do
        if tostring(row[1]):sub(-36):lower() == actor then return true end
    end
    error("trace requires a recruited companion")
end
if operation ~= "install" and operation ~= "read" and operation ~= "stop" then error("invalid trace operation") end
local existing = BG3AC_RequestTrace
if existing then assert(existing.actor_uuid == actor, "another companion trace is installed") end
if operation == "install" then
    assert(not existing or not existing.active, "trace already installed")
    assert(BG3AC_Observer and BG3AC_Observer.epoch, "observe before tracing")
    actor_allowed()
    local trace = {actor_uuid=actor, session_id=BG3AC_Observer.epoch,
        active=true, seq=0, records={}, errors={}, subscriptions={}, ticks=0, queued={}}
    local function stop()
        trace.active = false
        for _, subscription in ipairs(trace.subscriptions) do pcall(Ext.Entity.Unsubscribe, subscription) end
        trace.subscriptions = {}
    end
    local function record(value)
        if #trace.records >= 96 then trace.truncated=true; return end
        trace.seq = trace.seq + 1
        value.seq, value.tick = trace.seq, trace.ticks
        trace.records[#trace.records+1] = value
    end
    local function guarded(fn)
        return function(...)
            if not trace.active then return end
            if not BG3AC_Observer or BG3AC_Observer.epoch ~= trace.session_id then
                trace.invalidated=true; stop(); return
            end
            local ok, err = pcall(fn, ...)
            if not ok then
                trace.errors[#trace.errors+1] = tostring(err):sub(1, 300)
                if #trace.errors >= 3 then stop() end
            end
        end
    end
    local function cast_record(kind, e, component)
        actor_allowed()
        local state = kind == "started" and component or e.SpellCastState
        if not state or id(state.Caster) ~= actor then return end
        record({kind=kind, actor_uuid=actor, cast_guid=tostring(state.SpellCastGuid),
            cast_entity=tostring(e), net_guid=state.NetGuid and tostring(state.NetGuid) or nil,
            spell=tostring(state.SpellId.Prototype),
            result=kind ~= "started" and tostring(component.Result) or nil})
    end
    local ok, err = pcall(function()
        trace.subscriptions[#trace.subscriptions+1] = Ext.Entity.OnSystemUpdate("ServerCastRequest", guarded(function()
            trace.ticks = trace.ticks + 1
            if trace.ticks > 3600 then trace.expired=true; stop(); return end
            actor_allowed()
            local system = Ext.System.ServerCastRequest
            local present = {}
            local count = 0
            for _, queue_name in ipairs({"NetworkStartRequests", "CommandProtocolStartRequests"}) do
                for _, request in ipairs(system[queue_name]) do
                    if id(request.Caster) == actor then
                        count = count + 1
                        if count > 96 then trace.truncated=true; break end
                        local spell = request.Spell
                        local request_guid = tostring(request.RequestGuid)
                        local key = queue_name .. ":" .. request_guid
                        present[key] = {queue=queue_name, request_guid=request_guid}
                        if not trace.queued[key] then
                        record({kind="queued", actor_uuid=actor, queue=queue_name,
                            request_guid=request_guid, spell=tostring(spell.Prototype),
                            net_guid=request.NetGuid and tostring(request.NetGuid) or nil,
                            originator_prototype=tostring(spell.OriginatorPrototype),
                            source_type=tostring(spell.SourceType), source=tostring(spell.Source),
                            progression_source=tostring(spell.ProgressionSource),
                            cast_options=tostring(request.CastOptions)})
                        end
                    end
                end
            end
            -- Absence at a later sample does not prove consumption or success.
            if count <= 96 then
                for key, request in pairs(trace.queued) do
                    if not present[key] then
                        record({kind="queue_departed", actor_uuid=actor,
                            queue=request.queue, request_guid=request.request_guid})
                    end
                end
                trace.queued = present
            end
        end))
        for _, spec in ipairs({{"SpellCastState", "started"}, {"SpellCastOutcome", "outcome"}, {"SpellCastFinishedEvent", "finished"}}) do
            local component_type, kind = spec[1], spec[2]
            trace.subscriptions[#trace.subscriptions+1] = Ext.Entity.OnCreate(component_type, guarded(function(e, _, component)
                cast_record(kind, e, component)
            end))
        end
    end)
    if not ok then stop(); error("trace installation failed: " .. tostring(err)) end
    BG3AC_RequestTrace = trace
    existing = trace
end
assert(existing, "trace is not installed")
if operation == "stop" then
    existing.active = false
    for _, subscription in ipairs(existing.subscriptions) do pcall(Ext.Entity.Unsubscribe, subscription) end
    existing.subscriptions = {}
end
return {actor_uuid=existing.actor_uuid, session_id=existing.session_id, active=existing.active,
    records=existing.records, errors=existing.errors, truncated=existing.truncated or false,
    invalidated=existing.invalidated or false, expired=existing.expired or false,
    ticks=existing.ticks, execution_enabled=false}
