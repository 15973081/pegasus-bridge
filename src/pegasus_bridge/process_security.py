"""Read process integrity labels; no elevation, credential values or UI input."""
import os

def process_security(pid=None):
    if os.name!='nt':return None
    import ctypes as c
    from ctypes import wintypes as w
    k=c.WinDLL('kernel32',use_last_error=True);a=c.WinDLL('advapi32',use_last_error=True)
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE]
    a.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)];a.OpenProcessToken.restype=w.BOOL
    a.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)];a.GetTokenInformation.restype=w.BOOL
    a.GetSidSubAuthorityCount.argtypes=[c.c_void_p];a.GetSidSubAuthorityCount.restype=c.POINTER(c.c_ubyte)
    a.GetSidSubAuthority.argtypes=[c.c_void_p,w.DWORD];a.GetSidSubAuthority.restype=c.POINTER(w.DWORD)
    handle=k.OpenProcess(0x1000,False,pid or os.getpid())
    if not handle:return None
    token=w.HANDLE()
    try:
        if not a.OpenProcessToken(handle,8,c.byref(token)):return None
        length=w.DWORD();a.GetTokenInformation(token,25,None,0,c.byref(length))
        if not 0<length.value<=16384:return None
        buf=c.create_string_buffer(length.value)
        if not a.GetTokenInformation(token,25,buf,length,c.byref(length)):return None
        sid=c.cast(buf,c.POINTER(c.c_void_p))[0]
        count=a.GetSidSubAuthorityCount(sid)[0]
        if not count:return None
        rid=a.GetSidSubAuthority(sid,count-1)[0]
        return {'integrity_rid':rid,'integrity_name':{4096:'low',8192:'medium',8448:'medium_plus',12288:'high',16384:'system'}.get(rid,'other')}
    finally:
        if token:k.CloseHandle(token)
        k.CloseHandle(handle)
