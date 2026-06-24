"""Adapter registry for managing supplier adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.base import BaseAdapter


class AdapterRegistry:
    """Factory for creating and managing adapter instances."""

    _adapters: dict[str, type[BaseAdapter]] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator to register an adapter class."""
        def wrapper(adapter_cls: type[BaseAdapter]):
            cls._adapters[name] = adapter_cls
            return adapter_cls
        return wrapper

    @classmethod
    def get(cls, name: str) -> type[BaseAdapter] | None:
        return cls._adapters.get(name)

    @classmethod
    def list_adapters(cls) -> list[str]:
        return list(cls._adapters.keys())
