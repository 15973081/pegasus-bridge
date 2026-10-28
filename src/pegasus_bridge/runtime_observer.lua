-- Opt-in observation only. Pass explicit game state; no IO, networking or secrets.
-- Embed once in a scene, then call pcall(pegasusEmitPlayerState, snapshot).
local function pegasusQuote(value)
 local function escape(char)
  if char=='"' then return '\\"' end
  if char=='\\' then return '\\\\' end
  return string.format('\\u%04x',string.byte(char))
 end
 return '"'..string.gsub(tostring(value),'[%z\1-\31\\"]',escape)..'"'
end
local function pegasusBoolMap(value)
 local parts={}
 for key,enabled in pairs(value or {}) do
  if type(key)=='string' and type(enabled)=='boolean' then
   parts[#parts+1]=pegasusQuote(key)..':'..tostring(enabled)
  end
 end
 table.sort(parts)
 return '{'..table.concat(parts,',')..'}'
end
local pegasusPlayerSequence=0
local function pegasusEmitPlayerState(state)
 local inventory={}
 for _,item in ipairs(state.inventory or {}) do
  if #inventory>=512 then return false end
  inventory[#inventory+1]='{"id":'..pegasusQuote(item.id)..',"name":'..pegasusQuote(item.name)..',"quantity":'..tostring(item.quantity)..',"category":'..pegasusQuote(item.category or 'item')..',"origin":'..pegasusQuote(item.origin or 'unknown')..'}'
 end
 pegasusPlayerSequence=pegasusPlayerSequence+1
 local payload='{"schema_version":1,"game_id":'..tostring(state.game_id)..',"scene_id":'..tostring(state.scene_id)..',"sequence":'..tostring(pegasusPlayerSequence)..',"node":'..pegasusQuote(state.node)..',"chapter":'..tostring(state.chapter)..',"highest_chapter":'..tostring(state.highest_chapter)..',"flags":'..pegasusBoolMap(state.flags)..',"endings":'..pegasusBoolMap(state.endings)..',"inventory":['..table.concat(inventory,',')..']}'
 if #payload>131072 then return false end
 print('[PegasusPlayerStateV1]'..payload)
 return true
end
