"""Read-only Core Audio subscription checks; never open streams or change defaults."""

import os

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows Core Audio notifications')
def test_native_audio_subscription_callback_abi_and_idempotent_close():
    from core.settings_gui.endpoint_notifications import EndpointNotifications, IMMNotificationClient, PropertyKey
    calls = []
    subscription = EndpointNotifications(lambda: calls.append(True))
    try:
        callback = subscription.client.QueryInterface(IMMNotificationClient)
        callback.OnDefaultDeviceChanged(0, 0, None)
        assert not calls  # Output-default changes alone do not select an input.
        callback.OnDefaultDeviceChanged(1, 0, None)
        callback.OnDeviceAdded('synthetic')
        callback.OnDeviceRemoved('synthetic')
        callback.OnDeviceStateChanged('synthetic', 8)
        callback.OnPropertyValueChanged('synthetic', PropertyKey())
        assert len(calls) == 5
    finally:
        subscription.close()
        subscription.close()
    assert subscription.enumerator is None and subscription.client is None
