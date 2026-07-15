"""Strategy registry - plugin discovery and instantiation.

Unlike the crypto registry (which activated exactly one profile), this registry
holds many strategies at once: the scanner evaluates every enabled strategy on
every eligible stock. Strategies can be registered explicitly or discovered by
importing a plugin package. A disabled strategy can be registered but not
instantiated for scanning, so a strategy under research does not affect live
scanning until it is enabled.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Dict, List, Type

from algo.core.logging import get_logger
from algo.strategies.base import StrategyProfile

logger = get_logger("strategies.registry")


class StrategyRegistry:
    """A collection of strategy plugin classes keyed by name."""

    def __init__(self) -> None:
        self._classes: Dict[str, Type[StrategyProfile]] = {}

    # ---------------------------------------------------------- registration

    def register(self, cls: Type[StrategyProfile]) -> Type[StrategyProfile]:
        """Register a StrategyProfile subclass. Usable as a decorator."""
        if not (isinstance(cls, type) and issubclass(cls, StrategyProfile)):
            raise TypeError(f"{cls!r} is not a StrategyProfile subclass")
        if getattr(cls, "meta", None) is None:
            raise ValueError(f"{cls.__name__} must define a `meta` StrategyMeta")
        name = cls.meta.name
        if name in self._classes and self._classes[name] is not cls:
            raise ValueError(f"duplicate strategy name '{name}'")
        self._classes[name] = cls
        logger.info("registered strategy '%s' v%s (enabled=%s)",
                    name, cls.meta.version, cls.meta.enabled)
        return cls

    def discover(self, package: str) -> List[str]:
        """Import every module in ``package`` and register the strategies found.

        Returns the names registered from this package. Missing package or zero
        strategies is not an error (Phase 1 ships no strategy plugins).
        """
        try:
            pkg = importlib.import_module(package)
        except ModuleNotFoundError:
            logger.info("strategy package '%s' not present - nothing to discover",
                        package)
            return []
        found: List[str] = []
        for _, modname, _ in pkgutil.iter_modules(pkg.__path__,
                                                  pkg.__name__ + "."):
            module = importlib.import_module(modname)
            for obj in vars(module).values():
                if (isinstance(obj, type) and issubclass(obj, StrategyProfile)
                        and obj is not StrategyProfile
                        and getattr(obj, "meta", None) is not None):
                    self.register(obj)
                    found.append(obj.meta.name)
        logger.info("discovered %d strategy(ies) in '%s'", len(found), package)
        return found

    # ------------------------------------------------------------- accessors

    def get(self, name: str) -> Type[StrategyProfile]:
        if name not in self._classes:
            raise KeyError(
                f"unknown strategy '{name}'. Registered: {self.names()}")
        return self._classes[name]

    def create(self, name: str, *, require_enabled: bool = True,
               **kwargs) -> StrategyProfile:
        """Instantiate a strategy; by default refuse disabled ones."""
        cls = self.get(name)
        if require_enabled and not cls.meta.enabled:
            raise ValueError(
                f"strategy '{name}' is registered but not enabled")
        return cls(**kwargs)

    def names(self) -> List[str]:
        return sorted(self._classes)

    def enabled_names(self) -> List[str]:
        return sorted(n for n, c in self._classes.items() if c.meta.enabled)

    def describe_all(self) -> dict:
        return {n: c.meta.__dict__.copy() for n, c in self._classes.items()}

    def __len__(self) -> int:
        return len(self._classes)

    def __contains__(self, name: str) -> bool:
        return name in self._classes
