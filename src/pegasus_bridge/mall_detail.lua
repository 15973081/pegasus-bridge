local custom=(ac.var and ac.var.custom_data and ac.var.custom_data.mall_detail) or {}
local selectedId=custom.productId
local render
local function closeDetail()
 ac.closeUI({name=prefix.."root"})
 ac.closeUI({name="mall_detail"})
end
render=function()
 resetRoot(400)
 image("paper",assets.paper,960,540,1920,1080,0,true)
 text("eyebrow","ST. VISCERA  /  SUPPLY RECORD",830,851,780,34,"inkRed",19)
 text("title","物资领取单",840,804,800,70,"ink",43)
 button("close","返回物资室",closeDetail,1330,843,270,58)
 local all=ac.getMallProductStatus()
 local product=nil
 if type(all)=="table" and selectedId~=nil then
  for _,p in ipairs(all)do if tostring(p.id)==tostring(selectedId)then product=p end end
 end
 if not product then
  text("missing","物资记录不可用",940,613,960,70,"ink",38)
  text("missing_body","未找到对应物资，或记录尚未就绪。\n请返回物资室刷新后重试。",940,485,960,120,"ink",27)
  return
 end
 local info=descriptions[tostring(product.id)] or {}
 text("name",product.name or "未命名物资",940,666,960,96,"ink",38)
 local description=info.description or "请核对物资名称、价格和领取方式。具体内容以作品中的实际说明为准。"
 text("description",description,940,512,960,160,"ink",27)
 local policy="领取方式待确认"
 if product.payType==1 then policy="单次领取 · 已拥有后不可重复购买" end
 if product.payType==2 then policy="可重复领取 · 每次购买均会计费" end
 text("policy",policy,940,374,960,54,"inkRed",22)
 text("price",theme.currency.."  "..tostring(product.price or "—"),740,274,560,70,"ink",34)
 local owned=product.payType==1 and product.isAlreadyBuy==true
 if owned then
  text("owned","已拥有",1240,274,320,64,"inkRed",29,"MiddleCenter")
 elseif type(product.id)=="number" and product.id>0 and (product.payType==1 or product.payType==2) then
  ac.createBuyButton({name=prefix.."buy",inlayer=prefix.."root",goodId=product.id,text="确认购买",textStyle=prefix.."title",pos={1240,274},size={320,70},index=30,visible=true,normal_resId=assets.accent,hover_resId=assets.hover,buy_resId=assets.button})
 else
  text("unavailable","暂不可购买",1240,274,320,64,"inkRed",25,"MiddleCenter")
 end
 text("foot","购买结果由系统确认。关闭详情不会自动扣费。",940,190,960,42,"ink",20)
end
render()
