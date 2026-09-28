"""Windows Core Audio notification binding; no streams or endpoint contents."""

from ctypes import POINTER, Structure, c_int, c_uint32, c_void_p, c_wchar_p

from comtypes import COMMETHOD, COMObject, GUID, HRESULT, IUnknown, CoCreateInstance, CoInitialize, CoUninitialize


class PropertyKey(Structure):
    _fields_ = [('fmtid', GUID), ('pid', c_uint32)]


class IMMNotificationClient(IUnknown):
    _iid_ = GUID('{7991EEC9-7E89-4D85-8390-6C703CEC60C0}')
    _methods_ = [
        COMMETHOD([], HRESULT, 'OnDeviceStateChanged', (['in'], c_wchar_p, 'device'), (['in'], c_uint32, 'state')),
        COMMETHOD([], HRESULT, 'OnDeviceAdded', (['in'], c_wchar_p, 'device')),
        COMMETHOD([], HRESULT, 'OnDeviceRemoved', (['in'], c_wchar_p, 'device')),
        COMMETHOD([], HRESULT, 'OnDefaultDeviceChanged', (['in'], c_int, 'flow'), (['in'], c_int, 'role'),
                  (['in'], c_wchar_p, 'device')),
        COMMETHOD([], HRESULT, 'OnPropertyValueChanged', (['in'], c_wchar_p, 'device'), (['in'], PropertyKey, 'key')),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
    _methods_ = [
        COMMETHOD([], HRESULT, 'EnumAudioEndpoints', (['in'], c_int, 'flow'), (['in'], c_uint32, 'mask'),
                  (['out'], POINTER(c_void_p), 'devices')),
        COMMETHOD([], HRESULT, 'GetDefaultAudioEndpoint', (['in'], c_int, 'flow'), (['in'], c_int, 'role'),
                  (['out'], POINTER(c_void_p), 'device')),
        COMMETHOD([], HRESULT, 'GetDevice', (['in'], c_wchar_p, 'identifier'),
                  (['out'], POINTER(c_void_p), 'device')),
        COMMETHOD([], HRESULT, 'RegisterEndpointNotificationCallback', (['in'], POINTER(IMMNotificationClient), 'client')),
        COMMETHOD([], HRESULT, 'UnregisterEndpointNotificationCallback', (['in'], POINTER(IMMNotificationClient), 'client')),
    ]


class NotificationClient(COMObject):
    _com_interfaces_ = [IMMNotificationClient]

    def __init__(self, notify):
        super().__init__()
        self.notify = notify

    def OnDeviceStateChanged(self, this, device, state):
        self.notify()
        return 0

    def OnDeviceAdded(self, this, device):
        self.notify()
        return 0

    def OnDeviceRemoved(self, this, device):
        self.notify()
        return 0

    def OnDefaultDeviceChanged(self, this, flow, role, device):
        if flow in (1, 2):  # Capture or all flows, including removal of the default.
            self.notify()
        return 0

    def OnPropertyValueChanged(self, this, device, key):
        self.notify()
        return 0


class EndpointNotifications:
    """Create/register and unregister/release on the Qt owner thread."""

    def __init__(self, notify):
        CoInitialize()
        self.enumerator = None
        self.client = None
        try:
            self.client = NotificationClient(notify)
            self.enumerator = CoCreateInstance(GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}'),
                                               interface=IMMDeviceEnumerator, clsctx=1)
            self.enumerator.RegisterEndpointNotificationCallback(self.client)
        except Exception:
            self.enumerator = self.client = None
            CoUninitialize()
            raise

    def close(self):
        if self.enumerator is not None:
            self.enumerator.UnregisterEndpointNotificationCallback(self.client)
            self.enumerator = self.client = None
            CoUninitialize()
