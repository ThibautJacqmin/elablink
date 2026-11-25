"""Server-backed record lists for the attachment browser."""

import json

from ._attachment_store import _REQUEST_TIMEOUT
from .client import _api_type


def _read_rows(method, *args, **kwargs):
    # Keep the native list fields (including tags_decoded) across SDK releases.
    response = method(*args, **kwargs, _preload_content=False, _request_timeout=_REQUEST_TIMEOUT)
    try:
        rows = json.loads(response.data)
    finally:
        response.release_conn()
    if not isinstance(rows, list):
        raise ValueError("Expected a list from the eLabFTW server")
    return rows


def filter_choices(client, entity_type):
    """Return team categories, statuses and shared tag names for the selected tab."""
    team = client.config.get("TEAM_ID") or "current"
    if entity_type == "experiments":
        read_categories = client.categories.read_team_experiments_categories
        read_statuses = client.statuses.read_team_experiments_status
    elif entity_type == "items":
        # /items_types contains resource templates. Filters use team category IDs.
        api = _api_type("ResourceCategoriesApi", "ResourcesCategoriesApi")(client.api_client)
        read_categories = api.read_team_resources_categories
        read_statuses = client.resource_statuses.read_team_items_status
    else:
        raise ValueError(f"Unsupported attachment record type: {entity_type}")
    categories = _read_rows(read_categories, team)
    statuses = _read_rows(read_statuses, team)
    tags = _read_rows(client.team_tags.read_team_tags, team)
    return categories, statuses, sorted((row["tag"] for row in tags), key=str.casefold)


def search_records(client, entity_type, text, categories, statuses, tags, offset, limit):
    """Fetch one page, applying the user's normal eLabFTW search scope and permissions."""
    if entity_type == "experiments":
        read_records = client.experiments.read_experiments
    elif entity_type == "items":
        read_records = client.items.read_items
    else:
        raise ValueError(f"Unsupported attachment record type: {entity_type}")

    query = {"limit": limit, "offset": offset}
    if text.strip():
        query["q"] = text.strip()
    if categories:
        query["category"] = ",".join(map(str, categories))
    if statuses:
        query["status"] = ",".join(map(str, statuses))
    if tags:
        query["tags"] = list(tags)

    rows = _read_rows(read_records, **query)
    for row in rows:
        if isinstance(row.get("tags_decoded"), list):
            row["tags"] = [tag["tag"] for tag in row["tags_decoded"]]
        else:
            row["tags"] = row["tags"].split("|") if row.get("tags") else []
    return rows
