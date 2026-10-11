-- Developer-only actor-scoped metadata. No graph, text, flags, answers or writes.
local spec = ...
assert(type(spec) == "table" and Ext.IsServer() == true, "server context required")
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
assert(member, "actor not recruited")
for _, row in ipairs(Osi.DB_Avatars:Get(nil)) do
    assert(tostring(row[1]):sub(-36):lower() ~= actor, "avatar forbidden")
end
local host_user = Osi.GetReservedUserID(Osi.GetHostCharacter())
assert(type(host_user) == "number" and host_user > 0 and host_user < math.huge and host_user % 1 == 0
    and Osi.GetReservedUserID(actor) == host_user, "ownership unknown")
assert(Osi.IsDead(actor) == 0, "actor dead or death state unknown")
local entity = Ext.Entity.Get(actor)
assert(entity ~= nil, "actor missing")
local result = {actor_uuid=actor, session_id=spec.session_id, research_only=true,
    execution_enabled=false, presented_choices_verified=false, selection_authority_verified=false,
    source="stock_dialog_participation_metadata"}
local function finish(reason)
    assert(BG3AC_Observer and BG3AC_Observer.epoch == spec.session_id, "session changed during read")
    result.reason = reason
    return result
end
local ok, state = pcall(function() return entity.DialogState end)
if not ok or state == nil then return finish("dialog_state_unavailable") end
local id_ok, id = pcall(function() return state.DialogId end)
-- The legacy alias is pinned in official SE source; it is not a guessed offset.
if not id_ok or id == nil then id_ok, id = pcall(function() return state.field_4 end) end
if not id_ok or type(id) ~= "number" or id ~= id or id % 1 ~= 0 or id < 0 or id > 2147483647 then
    return finish("dialog_instance_unavailable")
end
if not Ext.Utils or type(Ext.Utils.GetDialogManager) ~= "function" then
    return finish("dialog_manager_unavailable")
end
local instance_ok, instance = pcall(function()
    local manager = Ext.Utils.GetDialogManager()
    return manager and manager.Dialogs[id]
end)
if not instance_ok or instance == nil then return finish("dialog_instance_unavailable") end
local copied_ok, reason = pcall(function()
    assert(instance.DialogId == id, "dialog identity mismatch")
    local speakers = instance.Speakers
    local count = #speakers
    assert(type(count) == "number" and count % 1 == 0 and count >= 0 and count <= 64, "speakers unbounded")
    local participating = false
    for index = 1, count do
        local speaker = Ext.Entity.Get(speakers[index])
        if speaker and tostring(speaker.Uuid.EntityUuid):lower() == actor then participating = true end
    end
    -- Do not copy metadata from a conversation belonging to another player.
    if not participating then return "actor_not_participant" end
    assert(type(instance.IsPrivateDialog) == "boolean", "privacy unknown")
    result.participant_verified = true
    result.private_dialogue = instance.IsPrivateDialog
    -- A participant is not necessarily the speaker, listener/voter or answer owner.
    -- NodeSelection and CurrentNode are not proven presented UI choices.
    if instance.IsPrivateDialog then return "private_dialogue_choices_withheld" end
    result.dialogue_instance_id = id
    return "presented_choice_and_authority_adapter_required"
end)
if not copied_ok then
    result.participant_verified = nil
    result.private_dialogue = nil
    result.dialogue_instance_id = nil
    return finish("dialog_metadata_unverified")
end
return finish(reason)
