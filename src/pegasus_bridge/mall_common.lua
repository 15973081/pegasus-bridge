local prefix="mall404_"..role.."_"
local function text(name,content,x,y,w,h,style,size,align)
 ac.createText({name=prefix..name,inlayer=prefix.."root",content=tostring(content),pos={x,y},size={w,h},anchor={50,50},text_style=prefix..style,font_size=size,index=25,visible=true,raycast=false,alignment=align or "MiddleLeft"})
end
local function image(name,res,x,y,w,h,index,raycast)
 ac.createImage({name=prefix..name,inlayer=prefix.."root",resId=res,pos={x,y},size={w,h},anchor={50,50},index=index,visible=true,raycast=raycast or false})
end
local function button(name,label,fn,x,y,w,h,selected)
 local normal=assets.button
 if selected then normal=assets.accent end
 ac.createButton({name=prefix..name,inlayer=prefix.."root",text="",pos={x,y},size={w,h},anchor={50,50},index=20,visible=true,click_func=fn,normal_resId=normal,hover_resId=assets.hover})
 text(name.."_label",label,x,y,w-34,h,"title",24,"MiddleCenter")
end
local function resetRoot(index)
 ac.closeUI({name=prefix.."root"})
 ac.createLayer({name=prefix.."root",inlayer="system_ui",pos={960,540},size={1920,1080},anchor={50,50},index=index,visible=true})
 for name,color in pairs(colors)do
  ac.createStyle({name=prefix..name,font=theme.font,font_size=26,color=color,bold=name=="title"})
 end
end
