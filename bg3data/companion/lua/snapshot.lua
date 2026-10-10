-- Read-only snapshot. No UseSpell, teleports, resource writes, or spawning.
local requested = ...
local function uuid(g)
    if g == nil then return nil end
    local s = tostring(g)
    return #s >= 36 and s:sub(-36):lower() or nil
end
local function try(fn)
    local ok, result = pcall(fn)
    return ok and result or nil
end
-- Observe cast events so a changed HP total alone cannot be mistaken for our action.
-- This records events in the console Lua environment; it does not alter combat rules.
if not BG3AC_Observer then
    BG3AC_Observer = {seq=0, casts={}, epoch=tostring({})}
    pcall(function()
        Ext.Events.SessionLoaded:Subscribe(function()
            BG3AC_Observer.epoch = tostring({})
            BG3AC_Observer.seq = 0
            BG3AC_Observer.casts = {}
        end)
    end)
    Ext.Osiris.RegisterListener("CastedSpell", 5, "after", function(caster, spell)
        local t = BG3AC_Observer
        t.seq = t.seq + 1
        t.casts[#t.casts+1] = {seq=t.seq, caster=uuid(caster), spell=tostring(spell)}
        if #t.casts > 24 then table.remove(t.casts, 1) end
    end)
end
local function entity_id(e) return try(function() return uuid(e.Uuid.EntityUuid) end) end
local function db_members(db)
    local out = {}
    for _, row in ipairs(db:Get(nil)) do
        local id = uuid(row[1])
        if id then out[id] = true end
    end
    return out
end
local avatars = try(function() return db_members(Osi.DB_Avatars) end)
local party = try(function() return db_members(Osi.DB_PartyMembers) end)
local host = uuid(Osi.GetHostCharacter())
if not host then error("No loaded host character; load a save first") end
local host_user = try(function() return Osi.GetReservedUserID(host) end)
local selected = host_user and try(function() return uuid(Osi.GetCurrentCharacter(host_user)) end)
local anchor = requested or host
local ae = Ext.Entity.Get(anchor)
if not ae then error("Requested companion does not exist in this session") end
local combat = try(function() return ae.CombatParticipant.CombatHandle.CombatState end)
local combat_id = try(function() return tostring(combat.MyGuid) end)
local round = try(function() return ae.CombatParticipant.CombatHandle.TurnOrder.Round end)
local function unit(g, full)
    local e = Ext.Entity.Get(g)
    if not e then return nil end
    local u = {uuid = g, avatar = avatars and avatars[g] or false,
        avatar_verified = avatars ~= nil, party_member = party and party[g] or false,
        party_verified = party ~= nil, host_owned = false, resources = {}, spells = {},
        in_combat = Osi.IsInCombat(g) == 1, dead = Osi.IsDead(g) == 1,
        hp = Osi.GetHitpoints(g), max_hp = Osi.GetMaxHitpoints(g)}
    u.name = try(function() return e.CharacterCreationStats.Name end)
    if not u.name or u.name == "" then
        u.name = try(function() return Ext.Loca.GetTranslatedString(e.DisplayName.NameKey.Handle.Handle) end) or g
    end
    u.host_owned = host_user ~= nil and try(function() return Osi.GetReservedUserID(g) == host_user end) == true
    u.active_turn = try(function() return e.TurnBased.IsActiveCombatTurn end) == true
    u.can_act = try(function() return e.TurnBased.CanActInCombat end) == true
    u.turn_ended = try(function() return e.TurnBased.RequestedEndTurn or e.TurnBased.TurnActionsCompleted end) == true
    u.position = try(function() local x,y,z = Osi.GetPosition(g) return {x=x,y=y,z=z} end)
    u.relation = g == anchor and "self" or "unknown"
    if g ~= anchor then
        if try(function() return Osi.IsEnemy(anchor, g) end) == 1 then u.relation = "enemy"
        elseif g == host or (party and party[g]) then u.relation = "ally" end
    end
    try(function()
        for id, entries in pairs(e.ActionResources.Resources) do
            local d = Ext.StaticData.Get(id, "ActionResource")
            local name = d and d.Name or tostring(id)
            local levels = {}
            for _, r in ipairs(entries) do levels[tostring(r.Level)] = {amount=r.Amount, max=r.MaxAmount} end
            u.resources[name] = levels
        end
    end)
    if full then
        try(function()
            for _, s in ipairs(e.SpellBook.Spells) do
                if #u.spells >= 80 then break end
                u.spells[#u.spells+1] = tostring(s.Id.OriginatorPrototype)
            end
        end)
    end
    return u
end
local out = {schema_version=1, host_uuid=host, selected_uuid=selected,
    session_id=BG3AC_Observer.epoch,
    anchor_uuid=anchor, host_user=host_user, combat_id=combat_id, round=round,
    avatar_detection_ok=avatars ~= nil, party_detection_ok=party ~= nil,
    party={}, combatants={}, actor=unit(anchor, true), warnings={},
    event_seq=BG3AC_Observer.seq, casts=BG3AC_Observer.casts}
local ids = {}
for id in pairs(party or {}) do ids[#ids+1] = id end
table.sort(ids)
for _, id in ipairs(ids) do if #out.party < 16 then out.party[#out.party+1] = unit(id, id == anchor) end end
if combat then
    for _, e in ipairs(combat.Participants) do
        local id = entity_id(e)
        if id and #out.combatants < 64 then out.combatants[#out.combatants+1] = unit(id, id == anchor) end
    end
elseif Osi.IsInCombat(anchor) == 1 then
    out.warnings[#out.warnings+1] = "CombatState not readable; execution will be blocked"
end
if avatars == nil then out.warnings[#out.warnings+1] = "DB_Avatars unavailable; binding is blocked" end
if party == nil then out.warnings[#out.warnings+1] = "DB_PartyMembers unavailable; binding is blocked" end
return out
