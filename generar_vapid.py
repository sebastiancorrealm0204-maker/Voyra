from py_vapid import Vapid01
from cryptography.hazmat.primitives import serialization
import base64

v = Vapid01()
v.generate_keys()

priv = base64.urlsafe_b64encode(
    v.private_key.private_numbers().private_value.to_bytes(32, 'big')
).decode().rstrip('=')

pub = base64.urlsafe_b64encode(
    v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint
    )
).decode().rstrip('=')

print('VAPID_PUBLIC_KEY =', pub)
print('VAPID_PRIVATE_KEY =', priv)