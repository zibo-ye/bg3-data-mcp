-- Developer-only path preview. No movement, queues, resource or entity writes.
-- Every allocated preview is released in this same Lua call, including failures.
local spec = ...
assert(type(spec) == "table", "invalid path preview spec")
assert(Ext.IsServer() == true, "server context required")
local actor = spec.actor_uuid
assert(type(actor) == "string" and #actor == 36 and actor == actor:lower()
    and actor:match("^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$"), "invalid actor UUID")
assert(type(spec.session_id) == "string" and #spec.session_id > 0 and #spec.session_id <= 128,
    "invalid session")
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
assert(type(host_user) == "number" and host_user > 0 and host_user < math.huge and host_user % 1 == 0
    and Osi.GetReservedUserID(actor) == host_user, "ownership unknown")
assert(Osi.IsDead(actor) == 0, "actor dead or death state unknown")
local entity = Ext.Entity.Get(actor)
assert(entity ~= nil, "actor entity missing")
local function finite(value, minimum, maximum)
    return type(value) == "number" and value == value and value >= minimum and value <= maximum
end
local target = spec.position
assert(type(target) == "table" and finite(target.x, -1e6, 1e6)
    and finite(target.y, -1e6, 1e6) and finite(target.z, -1e6, 1e6), "invalid destination")
local x, y, z = Osi.GetPosition(actor)
assert(finite(x, -1e6, 1e6) and finite(y, -1e6, 1e6) and finite(z, -1e6, 1e6), "source position unknown")
local dx, dy, dz = target.x-x, target.y-y, target.z-z
assert(dx*dx + dy*dy + dz*dz <= 30*30, "destination beyond research radius")
local path = Ext.Level.BeginPathfindingImmediate(entity, {target.x, target.y, target.z})
assert(path ~= nil, "path allocation failed")
local ok, copied = pcall(function()
    path:UsePlayerWeighting()
    local found = Ext.Level.FindPath(path)
    assert(BG3AC_Observer and BG3AC_Observer.epoch == spec.session_id, "session changed during preview")
    assert(type(found) == "boolean", "invalid path search result")
    assert(type(path.GoalFound) == "boolean" and type(path.SearchComplete) == "boolean", "invalid path search state")
    assert(found == path.GoalFound, "inconsistent path search result")
    assert(finite(path.ErrorCause, 0, 4294967295) and path.ErrorCause % 1 == 0, "invalid path error code")
    local count, aoo_count = #path.Nodes, #path.AoOPositions
    assert(finite(count, 0, 1e6) and count % 1 == 0
        and finite(aoo_count, 0, 1e6) and aoo_count % 1 == 0, "invalid path collection size")
    local result = {actor_uuid=actor, session_id=spec.session_id, preview_valid=true,
        execution_enabled=false, normal_rules_execution_validated=false,
        source="stock_immediate_pathfinding", goal_found=path.GoalFound,
        search_complete=path.SearchComplete, error_cause=path.ErrorCause,
        node_count=count, nodes_truncated=count>64, nodes={}, aoo_position_count=aoo_count}
    for index = 1, math.min(count, 64) do
        local node = path.Nodes[index]
        local p = node.Position
        local px, py, pz = p[1], p[2], p[3]
        assert(finite(px, -1e6, 1e6) and finite(py, -1e6, 1e6)
            and finite(pz, -1e6, 1e6) and finite(node.Distance, 0, 1e6), "invalid path node")
        result.nodes[index] = {position={x=px,y=py,z=pz}, distance=node.Distance}
    end
    return result
end)
-- Release must run even when weighting, search, node inspection or copying throws.
-- Do not access the path after release; the protected reference may be invalidated.
local released, release_result = pcall(function() return Ext.Level.ReleasePath(path) end)
if not released or release_result ~= nil then
    return {preview_valid=false, cleanup_release_succeeded=false,
        allocation_state="unknown", error="path_release_failed", execution_enabled=false}
end
if not ok then
    return {preview_valid=false, cleanup_release_succeeded=true,
        error="path_copy_failed", execution_enabled=false}
end
copied.cleanup_release_succeeded = true
return copied
