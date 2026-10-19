"""Authenticator-app codes (TOTP) for staff two-factor login."""

import hmac
import time

import pyotp
import segno

from app.core import crypto

ISSUER = "Pritis DPP"
STEP_SECONDS = 30


def now() -> float:
    """Current time; tests replace this to step through codes."""
    return time.time()


def new_secret() -> str:
    """A fresh secret, returned encrypted for storage."""
    return crypto.encrypt(pyotp.random_base32())


def setup_details(encrypted_secret: str, email: str) -> dict[str, str]:
    """What the invite page shows: the secret for typing in, and a QR code to scan."""
    secret = crypto.decrypt(encrypted_secret)
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)
    return {
        "secret": secret,
        "otpauth_uri": uri,
        "qr_svg": segno.make(uri, error="m").svg_data_uri(scale=5, border=2),
    }


def verify(encrypted_secret: str, code: str, last_step: int | None) -> int | None:
    """Returns the time step the code belongs to, or None if it's wrong or already used.

    Accepts the previous, current and next 30-second code to allow for clock drift.
    """
    if not (len(code) == 6 and code.isdigit()):
        return None
    totp = pyotp.TOTP(crypto.decrypt(encrypted_secret))
    t = now()
    for offset in (-1, 0, 1):
        at = t + offset * STEP_SECONDS
        if hmac.compare_digest(totp.at(at), code):
            step = int(at // STEP_SECONDS)
            if last_step is not None and step <= last_step:
                return None  # replayed code
            return step
    return None
