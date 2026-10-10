-- Read-only API presence probe; no queue writes or full request/engine data dumps.
local actor = ...
local function field(obj, key)
    local ok, value = pcall(function() return obj[key] end)
    return ok and value or nil
end
local function describe(value)
    if value == nil then return {present=false} end
    local out = {present=true, lua_type=type(value)}
    local ok, name = pcall(function() return Ext.Types.GetObjectType(value) end)
    if ok and name then out.cpp_type=tostring(name) end
    local sized, size = pcall(function() return #value end)
    if sized then out.length=size end
    return out
end
local system = field(Ext, "System")
local cast = field(system, "ServerCastRequest")
local turn = field(system, "ServerTurnOrder")
local sight = field(system, "ServerSightViewshed")
local entity = Ext.Entity.Get(actor)
local requests = field(entity, "ServerSpellCastRequests")
return {
    schema_version=1, actor_exists=entity ~= nil, execution_enabled=false,
    normal_rules_validated=false,
    systems={cast=describe(cast), turn=describe(turn), sight=describe(sight)},
    queues={network_cast=describe(field(cast, "NetworkStartRequests")),
            command_cast=describe(field(cast, "CommandProtocolStartRequests")),
            actor_cast=describe(field(requests, "StartRequests")),
            end_turn=describe(field(turn, "EndTurn"))},
    note="Presence and reflected array mutability do not prove valid player request submission"
}
