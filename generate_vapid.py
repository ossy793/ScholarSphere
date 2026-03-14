"""
Generate VAPID keys for push notifications.
Run once on the server:
  python3 generate_vapid.py

Then add the output values to backend/.env:
  VAPID_PUBLIC_KEY=...
  VAPID_PRIVATE_KEY=...
  VAPID_CLAIM_EMAIL=admin@pritis.name.ng
  CRON_SECRET=<any random string>

Requires: pywebpush (already in requirements.txt)
"""
from pywebpush import Vapid

v = Vapid()
v.generate_keys()

print("Add these to your backend/.env file:")
print()
print(f"VAPID_PUBLIC_KEY={v.public_key_urlsafe_unpadded}")
print(f"VAPID_PRIVATE_KEY={v.private_key_urlsafe_unpadded}")
print(f"VAPID_CLAIM_EMAIL=admin@pritis.name.ng")
print()
print("Also set a random secret for cron jobs:")
import secrets
print(f"CRON_SECRET={secrets.token_urlsafe(32)}")
