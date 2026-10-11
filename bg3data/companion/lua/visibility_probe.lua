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
local ok_sight, actor_sight = pcall(function() return entity.SightData.SightUuid end)
if not ok_sight or actor_sight == nil then actor_sight = nil end
if actor_sight ~= nil then
    local ok_guid = pcall(uuid, tostring(actor_sight):lower())
    if not ok_guid or tostring(actor_sight) == "00000000-0000-0000-0000-000000000000" then actor_sight = nil end
end
local function read(object, getter, expected)
    local ok, value = pcall(getter, object)
    if not ok or type(value) ~= expected then return nil end
    return value
end
local function byte(object, getter)
    local value = read(object, getter, "number")
    if value and value >= 0 and value <= 255 and value % 1 == 0 then return value end
    return nil
end
local out = {actor_uuid=actor, session_id=spec.session_id,
    data_source="ServerViewshedParticipant.CanSee", research_only=true,
    visibility_verified=false, stealth_semantics="unvalidated", candidates={}}
for _, candidate in ipairs(spec.candidate_uuids) do
    local item = {uuid=candidate, membership_status="unknown", visibility_verified=false,
        reverse_membership_status="unknown", concealment={}, conservative_status="unknown"}
    local ok_handle, handle = pcall(Ext.Entity.UuidToHandle, candidate)
    local ok_uuid, resolved = false, nil
    if ok_handle and handle ~= nil then
        ok_uuid, resolved = pcall(Ext.Entity.HandleToUuid, handle)
    end
    if not ok_uuid or resolved == nil or tostring(resolved):lower() ~= candidate then
        item.reason = "candidate_handle_unavailable"
    else
        if not ok_set or can_see == nil then
            item.reason = "viewshed_component_unavailable"
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
        local ok_target, target = pcall(Ext.Entity.Get, candidate)
        if ok_target and target ~= nil then
            if actor_sight ~= nil then
                local reverse = read(target, function(t) return t.SightEntityViewshed.Viewshed[actor_sight] end, "boolean")
                if reverse ~= nil then
                    item.viewshed_member = reverse
                    item.reverse_membership_status = "observed"
                end
            end
            local flags = item.concealment
            flags.server_character_invisible = read(target, function(t) return t.ServerCharacter.Invisible end, "boolean")
            flags.server_character_off_stage = read(target, function(t) return t.ServerCharacter.OffStage end, "boolean")
            flags.character_sneaking = read(target, function(t) return t.Darkness.CharacterSneaking end, "boolean")
            flags.actually_sneaking = read(target, function(t) return t.Darkness.ActuallySneaking end, "boolean")
            flags.sneaking_raw = byte(target, function(t) return t.Darkness.Sneaking end)
            flags.seek_hidden = read(target, function(t) return t.Stealth.SeekHiddenFlag end, "boolean")
            local ok_invisibility, invisibility = pcall(function() return target.Invisibility end)
            if ok_invisibility then
                flags.invisibility_component_present = invisibility ~= nil
                if invisibility ~= nil then
                    flags.invisibility_raw = byte(invisibility, function(c) return c.field_0 end)
                end
            end
            -- Proposed restrictive policy, NOT a verified perception rule.
            -- Unknown/missing flags never count as false. Component presence
            -- blocks even when its opaque byte is zero; no semantics guessed.
            if item.los_member == false or item.viewshed_member == false
                or flags.server_character_invisible == true or flags.server_character_off_stage == true
                or flags.character_sneaking == true or flags.actually_sneaking == true
                or (flags.sneaking_raw ~= nil and flags.sneaking_raw ~= 0)
                or flags.invisibility_component_present == true then
                item.conservative_status = "blocked"
            elseif item.los_member == true and item.viewshed_member == true
                and flags.server_character_invisible == false and flags.server_character_off_stage == false
                and flags.character_sneaking == false and flags.actually_sneaking == false
                and flags.sneaking_raw == 0 and flags.invisibility_component_present == false then
                item.conservative_status = "candidate"
            end
        end
    end
    out.candidates[#out.candidates+1] = item
end
assert(BG3AC_Observer.epoch == spec.session_id, "session changed")
return out
