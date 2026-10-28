local custom=(ac.var and ac.var.custom_data and ac.var.custom_data.plot_lock_dialog_landscape) or {}
local productId=custom.productId
local valid=productId~=nil and tostring(productId)~="" and tostring(productId)~="0"
local name=custom.productName or "剧情许可"
local function owned()
 local all=ac.getMallProductStatus() or {}
 for _,p in ipairs(all)do
  if tostring(p.productId or p.id)==tostring(productId)then return p.isAlreadyBuy==true end
 end
 return false
end
local function closeLock()
 if valid then
  ac.releaseProductLock({productId=productId})
  if not owned()then ac.quitProductLock({productId=productId})end
 end
 ac.closeUI({name=prefix.."root"})
 ac.closeUI({name="plot_lock_dialog_landscape"})
end
local function openMall()
 ac.showUI({name="mall",ui_id=bindings.home})
end
local function saveProgress()
 ac.showUI({name="tianma_save_game",ui_id="save"})
end
resetRoot(400)
image("paper",assets.paper,960,540,1920,1080,0,true)
text("eyebrow","RESTRICTED ACCESS  /  WARD 404",880,850,880,36,"inkRed",20)
text("title","这份档案尚未开放",940,790,960,80,"ink",43)
text("item",name,940,650,960,90,"ink",33)
local message="继续阅读需要对应的剧情许可。\n你可以先保存进度，或前往物资室查看价格与说明。"
if not valid then message="剧情许可记录缺失。\n请返回调查，本页不会发起购买或解锁。" end
text("body",message,940,500,960,150,"ink",28)
button("back","返回调查",closeLock,650,300,300,72)
button("save","保存进度",saveProgress,1000,300,300,72)
if valid then
 button("mall","前往物资室",openMall,1350,300,300,72,true)
 ac.registerEvent({target="mall",event="close",callback=closeLock})
 ac.createProductLock({productId=productId})
end
text("foot","是否购买由你决定。返回不会扣费。",940,190,960,40,"ink",21)
