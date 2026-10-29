local showNode
local function nextNode() showNode("end") end
local function restartNode() showNode("start") end
ac.createStyle({name="demo_text",font="NotoSansSC-Regular SDF",font_size=28,color="#DDD8CD"})
showNode=function(key)
 local title="调查开始"
 local callback=nextNode
 if key=="end"then title="调查完成";callback=restartNode end
 ac.createText({name="title",inlayer="window",content=title,pos={960,750},size={900,100},anchor={50,50},text_style="demo_text",index=30,visible=true})
 ac.createButton({name="next",inlayer="window",text="继续 / 返回",pos={960,300},size={350,80},anchor={50,50},index=20,visible=true,click_func=callback})
 -- PEGASUS-NODE-HOOK
end
showNode("start")
