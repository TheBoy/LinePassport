"""LINE Official Accounts through LINE Official Account Manager (chat.line.biz).

Unofficial: LINE publishes no API for the OA Manager chat, so this uses the
endpoints the OA Manager web page uses, signed in by scanning a QR code with
a LINE account that manages the OA.
"""

from .channels import LineOAChannels, is_user_chat, normalize_event
from .client import OAManagerClient, OAManagerError, new_send_id
from .login import OAManagerLoginError, OAManagerLoginResult, OAManagerQrLogin

__all__ = [
    "LineOAChannels",
    "OAManagerClient",
    "OAManagerError",
    "OAManagerLoginError",
    "OAManagerLoginResult",
    "OAManagerQrLogin",
    "is_user_chat",
    "new_send_id",
    "normalize_event",
]
