import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def write_keypair(keys_dir: Path, kid: str) -> dict:
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    (keys_dir / f"private-{kid}.pem").write_bytes(private_pem)
    (keys_dir / f"public-{kid}.pem").write_bytes(public_pem)

    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "alg": "EdDSA",
        "use": "sig",
        "x": b64url(public_raw),
    }


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: generate_demo_keys.py <keys_dir> <kid> [kid...]")

    keys_dir = Path(sys.argv[1])
    kids = sys.argv[2:]
    keys_dir.mkdir(parents=True, exist_ok=True)

    jwks = {"keys": [write_keypair(keys_dir, kid) for kid in kids]}
    (keys_dir / "jwks.json").write_text(json.dumps(jwks, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
