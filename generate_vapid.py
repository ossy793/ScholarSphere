"""
Generate VAPID keys for push notifications.
Run once on the server:
  python3 generate_vapid.py

Then add the output values to backend/.env:
  VAPID_PUBLIC_KEY=...
  VAPID_PRIVATE_KEY=...
  VAPID_CLAIM_EMAIL=admin@pritis.name.ng
  CRON_SECRET=<any random string>

Requires: pywebpush (already in requirements.txt), cryptography
"""
import base64
import secrets
from pywebpush import Vapid

v = Vapid()
v.generate_keys()

# Extract raw 32-byte EC private key scalar → base64url (no padding)
private_numbers = v._private_key.private_numbers()
private_key_bytes = private_numbers.private_value.to_bytes(32, 'big')
private_key_b64 = base64.urlsafe_b64encode(private_key_bytes).rstrip(b'=').decode()

# Public key: uncompressed EC point (65 bytes) → base64url (no padding)
# This is used as applicationServerKey in the browser
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
public_key_bytes = v._public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
public_key_b64 = base64.urlsafe_b64encode(public_key_bytes).rstrip(b'=').decode()

print("Add these to your backend/.env file:")
print()
print(f"VAPID_PUBLIC_KEY={public_key_b64}")
print(f"VAPID_PRIVATE_KEY={private_key_b64}")
print(f"VAPID_CLAIM_EMAIL=admin@pritis.name.ng")
print()
print("Also set a random secret for cron jobs:")
print(f"CRON_SECRET={secrets.token_urlsafe(32)}")
