"""
cosmos_helper.py — Azure Cosmos DB helper

Manages the Cosmos DB connection and user profile operations.
Uses the Cosmos DB SDK (azure-cosmos).

Required env vars:
  COSMOS_CONNECTION_STR   Connection string for the Cosmos DB account
  COSMOS_DATABASE         Database name, e.g. "shelf"
  COSMOS_USERS_CONTAINER  Container name, e.g. "users"

Container settings (set up in Azure Portal or via CLI):
  Partition key: /userId
  Indexing:      automatic
"""

import os
import logging
from datetime import datetime, timezone

from azure.cosmos import CosmosClient, PartitionKey, exceptions

COSMOS_CONNECTION_STR  = os.environ.get("COSMOS_CONNECTION_STR", "")
COSMOS_DATABASE        = os.environ.get("COSMOS_DATABASE", "shelf")
COSMOS_USERS_CONTAINER = os.environ.get("COSMOS_USERS_CONTAINER", "users")

_cosmos_client    = None
_users_container  = None


def _get_users_container():
    """Lazy-init the Cosmos container client (cached per Function instance)."""
    global _cosmos_client, _users_container

    if _users_container is not None:
        return _users_container

    if not COSMOS_CONNECTION_STR:
        raise RuntimeError("COSMOS_CONNECTION_STR is not configured")

    _cosmos_client = CosmosClient.from_connection_string(COSMOS_CONNECTION_STR)
    db = _cosmos_client.create_database_if_not_exists(id=COSMOS_DATABASE)
    _users_container = db.create_container_if_not_exists(
        id=COSMOS_USERS_CONTAINER,
        partition_key=PartitionKey(path="/userId"),
        offer_throughput=400,   # minimum; upgrade later if needed
    )
    return _users_container


# ── User profile operations ────────────────────────────────────────────────────

def get_or_create_user(user_id: str, email: str, display_name: str = "") -> dict:
    """
    Fetch the user profile from Cosmos DB.
    If the user doesn't exist yet (first login), create a new profile.

    Returns the user profile dict.
    """
    container = _get_users_container()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Try to read existing profile
        item = container.read_item(item=user_id, partition_key=user_id)
        # Update last login timestamp
        item["lastLoginAt"] = now
        container.upsert_item(item)
        return item

    except exceptions.CosmosResourceNotFoundError:
        # First login — create profile
        new_user = {
            "id":          user_id,
            "userId":      user_id,
            "email":       email,
            "displayName": display_name or email.split("@")[0],
            "createdAt":   now,
            "lastLoginAt": now,
            "preferences": {
                "language": "en",
            },
        }
        container.create_item(new_user)
        logging.info(f"New user created: {user_id}")
        return new_user

    except Exception as exc:
        logging.error(f"get_or_create_user failed: {exc}")
        raise


def update_user_profile(user_id: str, updates: dict) -> dict:
    """
    Update allowed fields on a user's profile.
    Only whitelisted fields can be updated to prevent injection.

    Returns the updated profile dict.
    """
    ALLOWED_FIELDS = {"displayName", "preferences"}

    container  = _get_users_container()
    item       = container.read_item(item=user_id, partition_key=user_id)
    now        = datetime.now(timezone.utc).isoformat()

    for key, value in updates.items():
        if key in ALLOWED_FIELDS:
            if key == "preferences" and isinstance(value, dict):
                # Merge preferences rather than replace
                item.setdefault("preferences", {}).update(value)
            else:
                item[key] = value

    item["updatedAt"] = now
    container.upsert_item(item)
    return item
