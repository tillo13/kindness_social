from base64    import b64encode, b64decode
from secrets   import token_bytes
from hashlib   import sha256
from ecdsa     import SigningKey, SECP256k1, util

# secp256k1 curve order
SECP256K1_ORDER = SECP256k1.order

class Anon:

    @staticmethod
    def publicKeyCreate(e) -> list:
        sk = SigningKey.from_string(bytes(e), curve=SECP256k1)
        vk = sk.get_verifying_key()
        pubkey_bytes = vk.to_string()
        x = pubkey_bytes[:32]
        y = pubkey_bytes[32:]
        prefix = b'\x02' if y[-1] % 2 == 0 else b'\x03'
        compressed = prefix + x
        return list(compressed)

    @staticmethod
    def xor(e) -> str:
        t = ""
        for n in range(len(e)):
            t += chr(e[n])
        return b64encode(t.encode('latin-1')).decode()

    @staticmethod
    def generate_keys() -> dict:
        e = token_bytes(32)
        n = Anon.publicKeyCreate(e)
        r = Anon.xor(e)

        return {
            "privateKey": r,
            "userPublicKey": n
        }

    @staticmethod
    def sign_challenge(challenge_data: bytes, key: str) -> dict:
        key_bytes = b64decode(key)
        sk = SigningKey.from_string(key_bytes, curve=SECP256k1)
        digest = sha256(challenge_data).digest()

        # Sign and get raw (r, s) integers
        def sigencode_raw(r, s, order):
            # Enforce low-S (like coincurve/libsecp256k1 does)
            if s > order // 2:
                s = order - s
            return (r, s)

        r_int, s_int = sk.sign_digest(digest, sigencode=sigencode_raw)
        sig = r_int.to_bytes(32, 'big') + s_int.to_bytes(32, 'big')

        return {
            "challenge": b64encode(challenge_data).decode(),
            "signature": b64encode(sig).decode()
        }
