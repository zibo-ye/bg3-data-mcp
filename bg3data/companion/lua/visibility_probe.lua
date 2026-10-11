-- Developer read-only LOS probe. No enumeration, subscriptions or state writes.
-- CanSee membership is NOT verified visibility or stealth/perception legality.
local spec = ...
assert(type(spec) == "table", "invalid visibility spec")
local function uuid(value)
    assert(type(value) == "string" and #value == 36
        and value:match("^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$"), "invalid UUID")
    assert(value == value:lower(), "UUID must be canonical")
    return value
end
local actor = uuid(spec.actor_uuid)
assert(type(spec.session_id) == "string" and #spec.session_id > 0 and #spec.session_id <= 128, "invalid session")
assert(BG3AC_Observer and BG3AC_Observer.epoch == spec.session_id, "session changed")
assert(type(spec.candidate_uuids) == "table" and #spec.candidate_uuids <= 32, "candidate limit")
local seen = {}
local candidate_count = 0
for index, candidate in pairs(spec.candidate_uuids) do
    assert(type(index) == "number" and index >= 1 and index <= #spec.candidate_uuids and index % 1 == 0, "invalid candidate array")
    uuid(candidate)
    assert(not seen[candidate], "duplicate candidate")
    seen[candidate] = true
    candidate_count = candidate_count + 1
end
assert(candidate_count == #spec.candidate_uuids, "invalid candidate array")
local function rows(database)
    assert(database and database.Get, "identity database unavailable")
    local values = database:Get(nil)
    assert(type(values) == "table" and #values <= 64, "identity database unavailable or exceeds bound")
    return values
end
local member = false
for _, row in ipairs(rows(Osi.DB_PartyMembers)) do
    if tostring(row[1]):sub(-36):lower() == actor then member = true end
end
assert(member, "actor is not recruited")
for _, row in ipairs(rows(Osi.DB_Avatars)) do
    assert(tostring(row[1]):sub(-36):lower() ~= actor, "avatar forbidden")
end
local host = Osi.GetHostCharacter()
local host_user = Osi.GetReservedUserID(host)
assert(type(host_user) == "number" and host_user > 0
    and Osi.GetReservedUserID(actor) == host_user, "ownership unknown")
assert(Osi.IsDead(actor) == 0, "actor unavailable")
local entity = Ext.Entity.Get(actor)
assert(entity ~= nil, "actor entity unavailable")
local ok_set, can_see = pcall(function() return entity.ServerViewshedParticipant.CanSee end)
local out = {actor_uuid=actor, session_id=spec.session_id,
    data_source="ServerViewshedParticipant.CanSee", research_only=true,
    visibility_verified=false, stealth_semantics="unvalidated", candidates={}}
for _, candidate in ipairs(spec.candidate_uuids) do
    local item = {uuid=candidate, membership_status="unknown", visibility_verified=false}
    if not ok_set or can_see == nil then
        item.reason = "viewshed_component_unavailable"
    else
        local ok_handle, handle = pcall(Ext.Entity.UuidToHandle, candidate)
        local ok_uuid, resolved = false, nil
        if ok_handle and handle ~= nil then
            ok_uuid, resolved = pcall(Ext.Entity.HandleToUuid, handle)
        end
        if not ok_uuid or resolved == nil or tostring(resolved):lower() ~= candidate then
            item.reason = "candidate_handle_unavailable"
        else
            -- Official LuaSetProxy::Index implements HashSet membership.
            local ok_member, present = pcall(function() return can_see[handle] end)
            if ok_member and type(present) == "boolean" then
                item.membership_status = "observed"
                item.los_member = present
            else
                item.reason = "membership_query_unavailable"
            end
        end
    end
    out.candidates[#out.candidates+1] = item
end
assert(BG3AC_Observer.epoch == spec.session_id, "session changed")
return out
