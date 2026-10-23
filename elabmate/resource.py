# -*- coding: utf-8 -*-
"""High-level wrapper for eLabFTW resources/items."""

from .exceptions import InvalidCategory
from .experiment import ElabExperiment


class ElabResource(ElabExperiment):
    """Represents an eLabFTW resource while sharing entity helpers."""

    entity_type = "items"

    def _load(self):
        if self.ID is None:
            from .exceptions import DeletedExperiment

            raise DeletedExperiment()
        item = self.api.items.get_item(self.ID)
        self._cache = item.to_dict()
        return self._cache

    def _sync(self, updates: dict):
        self.api.items.patch_item(self.ID, body=updates)
        self._load()

    def _category_values(self) -> dict[str, int]:
        # The generated client version used by elabmate has no endpoint for
        # enumerating resource categories. Numeric category IDs still work.
        return {}

    @property
    def category(self):
        title = self._data.get("category_title")
        if title:
            return title
        value = self._data.get("category")
        return str(value) if value is not None else None

    @category.setter
    def category(self, category_id: int):
        if not isinstance(category_id, int):
            raise InvalidCategory(str(category_id))
        self._sync({"category": category_id})

    def _status_values(self) -> dict[str, int]:
        return self.api.resource_status_dict

    def __repr__(self):
        return f"Resource title: {self.title}\n    ID: {self.ID}\n    category: {self.category}"
