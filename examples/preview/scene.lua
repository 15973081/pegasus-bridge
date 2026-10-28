local function render()
 ac.createText({name='title',content='病房门锁 · Lua 交互预览',pos={640,610},size={1000,80},font_size=42})
 local message='你在枕头下面摸到一把钥匙。'
 if ac.var.has_key then message='钥匙已放入背包。可以打开病房门。' end
 ac.createText({name='message',content=message,pos={640,450},size={1000,100},font_size=28})
end
local function take()
 ac.var.has_key=true
 render()
end
local function open()
 local message='门锁没有打开：需要先拿到钥匙。'
 if ac.var.has_key then message='门打开了。走廊尽头的广播响起。' end
 ac.createText({name='message',content=message,pos={640,450},size={1000,100},font_size=28})
end
render()
ac.createButton({name='take_key',text='拾取钥匙',pos={410,240},size={340,90},click_func=take})
ac.createButton({name='open_door',text='打开房门',pos={870,240},size={340,90},click_func=open})
