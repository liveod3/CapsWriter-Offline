# coding: utf-8

from core.i18n import Notice
import socket
from . import logger
from config_client import ClientConfig as Config

def broadcast_output_udp(text: str):
    """
    Broadcast recognition results to configured UDP destinations.
    """
    if not Config.udp_broadcast or not Config.udp_broadcast_targets:
        return

    message = text.encode('utf-8')
    for addr, port in Config.udp_broadcast_targets:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(message, (addr, port))
                logger.debug(Notice('diagnostic.udp_broadcaster.sending_udp_output_to_chars', value0=addr, value1=port, value2=len(text)))
        except Exception as e:
            logger.warning(Notice('diagnostic.udp_broadcaster.udp_text_delivery_failed_error'), type(e).__name__)
