"""
auth_helper.py — JWT validation for Azure Entra External ID

Validates Bearer tokens sent by the frontend (MSAL.js) on every
protected Azure Function endpoint.

How it works:
  1. Decode the JWT header to find the kid (key ID)
  2. Fetch the JWKS (public keys) from Entra's well-known endpoint
  3. Verify the signature, issuer, audience, and expiry

Required env vars:
  ENTRA_TENANT_ID       The Entra External ID tenant ID
  ENTRA_CLIENT_ID       The App Registration client ID (audience claim)

The JWKS endpoint is cached in memory for 1 hour to avoid fetching
on every request (Function App instance lifetime).
"""

import os
import time
import json
import base64
import hashlib
import hmac
import logging
import requests

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
import jwt  # PyJWT

ENTRA_TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
ENTRA_CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "")

# Cache structure: {"keys": [...], "fetched_at": timestamp}
_jwks_cache = None
_JWKS_TTL   = 3600  # seconds


def _get_jwks():
    """Fetch (or return cached) JWKS from Entra External ID."""
    global _jwks_cache

    now = time.time()
    if _jwks_cache and (now - _jwks_cache["fetched_at"]) < _JWKS_TTL:
        return _jwks_cache["keys"]

    # Entra External ID JWKS endpoint
    # For standard Entra tenants: https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys
    jwks_url = (
        f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}"
        f"/discovery/v2.0/keys"
    )

    try:
        resp = requests.get(jwks_url, timeout=10)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
        _jwks_cache = {"keys": keys, "fetched_at": now}
        return keys
    except Exception as exc:
        logging.error(f"JWKS fetch failed: {exc}")
        return _jwks_cache["keys"] if _jwks_cache else []


def _b64_to_int(b64_str):
    """Convert base64url string to integer (for RSA key construction)."""
    # Add padding
    padded = b64_str + "=" * (4 - len(b64_str) % 4)
    raw = base64.urlsafe_b64decode(padded)
    return int.from_bytes(raw, "big")


def _jwk_to_public_key(jwk):
    """Convert a JWK dict (RSA) to a cryptography public key object."""
    n = _b64_to_int(jwk["n"])
    e = _b64_to_int(jwk["e"])
    pub_numbers = RSAPublicNumbers(e, n)
    return pub_numbers.public_key(default_backend())


def validate_token(authorization_header: str):
    """
    Validate the Bearer token from the Authorization header.

    Returns:
      (claims_dict, None)          on success
      (None, error_message_str)    on failure

    Usage in a Function:
      claims, err = validate_token(req.headers.get("Authorization", ""))
      if err:
          return _json_response({"error": err}, status_code=401)
      user_id = claims["oid"]   # Entra object ID — stable unique user ID
    """
    if not ENTRA_TENANT_ID or not ENTRA_CLIENT_ID:
        return None, "Auth not configured on server"

    if not authorization_header.startswith("Bearer "):
        return None, "Missing or invalid Authorization header"

    token = authorization_header[len("Bearer "):]

    keys = _get_jwks()
    if not keys:
        return None, "Could not fetch signing keys"

    # Decode header without verification to find kid
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception:
        return None, "Invalid token format"

    kid = unverified_header.get("kid")
    matching_key = next((k for k in keys if k.get("kid") == kid), None)
    if not matching_key:
        return None, "No matching signing key found"

    try:
        public_key = _jwk_to_public_key(matching_key)
    except Exception as exc:
        logging.error(f"Key construction failed: {exc}")
        return None, "Failed to construct signing key"

    # Valid issuers for Entra External ID
    valid_issuers = [
        f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/v2.0",
        f"https://sts.windows.net/{ENTRA_TENANT_ID}/",
    ]

    for issuer in valid_issuers:
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=ENTRA_CLIENT_ID,
                issuer=issuer,
                options={"verify_exp": True},
            )
            return claims, None
        except jwt.ExpiredSignatureError:
            return None, "Token has expired"
        except jwt.InvalidIssuerError:
            continue   # try next issuer
        except jwt.InvalidAudienceError:
            return None, "Token audience mismatch"
        except Exception:
            continue

    return None, "Token validation failed"


def get_user_id(claims: dict) -> str:
    """
    Extract the stable unique user ID from validated token claims.
    'oid' (object ID) is the preferred claim — it never changes for a user.
    Falls back to 'sub' if oid is absent.
    """
    return claims.get("oid") or claims.get("sub", "")
