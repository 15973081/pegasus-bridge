-- Native mall UI. Product data comes from the current game's mall manager.
local selected=0
local page=1
local render
local function closeMall()
 ac.closeUI({name=prefix.."root"})
 ac.closeUI({name="mall"})
end
local function category(p)
 if p.type==1 then return "剧情许可" end
 if p.type==2 then return "永久增益" end
 return "补给用品"
end
local function detailAction(p)
 local function open()
  local target=bindings.detail
  if p.type==2 then target=bindings.detail_permanent end
  if p.payType==2 then target=bindings.detail_repeat end
  ac.showUI({name="mall_detail",ui_id=target,custom_data={productId=p.id}})
 end
 return open
end
local function selectCategory(value)
 local function change()
  selected=value
  page=1
  render()
 end
 return change
end
local function previous()
 page=math.max(1,page-1)
 render()
end
local function nextPage()
 page=page+1
 render()
end
render=function()
 resetRoot(200)
 image("background",assets.background,960,540,1920,1080,0)
 image("vignette",assets.vignette,960,540,1920,1080,1)
 text("brand",theme.subtitle,360,1000,520,34,"muted",20)
 text("title",theme.title,330,932,460,80,"title",58)
 button("close","返回调查",closeMall,1665,905,290,64)
 local choices={{0,"全部物资"},{1,"剧情许可"},{2,"永久增益"},{3,"补给用品"}}
 for i,c in ipairs(choices)do
  button("category_"..i,c[2],selectCategory(c[1]),255,750-(i-1)*100,280,68,selected==c[1])
 end
 text("rail_note","物资记录\n\n查看用途与价格后，\n再决定是否领取。",255,270,280,160,"muted",23)
 local all=ac.getMallProductStatus()
 local valid=type(all)=="table"
 local list={}
 if valid then
  for _,p in ipairs(all)do
   if type(p.id)=="number" and p.id>0 and (selected==0 or p.type==selected)then list[#list+1]=p end
  end
 end
 local pages=math.max(1,math.ceil(#list/6))
 page=math.min(page,pages)
 text("count",tostring(#list).." 项物资    /    领取前请阅读说明",1035,827,1050,42,"muted",22)
 if #list==0 then
  image("empty_panel",assets.card,1160,520,1160,470,4)
  image("empty_seal",assets.seal,1160,636,76,80,8)
  local title="物资尚未入库"
  local body="这里暂时没有可领取的物资。\n你可以返回调查，继续整理医院留下的线索。"
  if selected~=0 then title="此类物资暂缺";body="当前分类还没有物资。\n可切换其他分类，或返回继续调查。" end
  if not valid then title="暂时无法读取物资";body="物资记录尚未就绪。\n请稍后刷新，或返回调查。" end
  text("empty_title",title,1160,527,900,70,"title",39,"MiddleCenter")
  text("empty_body",body,1160,410,900,110,"muted",25,"MiddleCenter")
 else
  for slot=1,6 do
   local p=list[(page-1)*6+slot]
   if p then
    local x=690+((slot-1)%3)*440
    local y=655-math.floor((slot-1)/3)*298
    local key="item_"..slot
    image(key.."_card",assets.card,x,y,410,276,4)
    image(key.."_seal",assets.seal,x-153,y+80,52,55,8)
    text(key.."_type",category(p),x+22,y+88,266,34,"red",20)
    text(key.."_name",p.name or "未命名物资",x,y+22,356,82,"title",27)
    text(key.."_price",theme.currency.."  "..tostring(p.price or "—"),x-70,y-54,210,38,"muted",22)
    local label="查看详情"
    if p.payType==1 and p.isAlreadyBuy==true then label="已拥有 · 查看" end
    button(key.."_detail",label,detailAction(p),x,y-104,290,54)
   end
  end
 end
 button("refresh","刷新物资",render,255,130,280,62)
 button("previous","上一页",previous,765,130,230,62)
 text("page",tostring(page).." / "..tostring(pages),1160,130,220,50,"muted",24,"MiddleCenter")
 button("next","下一页",nextPage,1550,130,230,62)
end
render()
