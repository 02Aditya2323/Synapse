"""A dict that remembers what was read from it, for adapters that trace a node's real reads.

An edge dropped here is the worst kind of bug: the node gets too little state at run time, and the
firewall cannot see it, because the original and the optimized graph both lost the same input. So
anything that depends on the state as a whole counts as reading every key, and that only ever costs
a missed parallelization. `dict` is written in C, so its reads skip the overrides unless each is listed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class RecordingState(dict):
    def __init__(self, data: Mapping[str, Any]):
        super().__init__(data)
        self.read: set[str] = set()
        self.read_everything = False

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __contains__(self, key):
        self.read.add(key)
        return super().__contains__(key)

    def pop(self, key, *default):
        self.read.add(key)
        return super().pop(key, *default)

    def setdefault(self, key, default=None):
        self.read.add(key)
        return super().setdefault(key, default)

    def __iter__(self):
        self.read_everything = True
        return super().__iter__()

    def __reversed__(self):
        self.read_everything = True
        return super().__reversed__()

    def keys(self):
        self.read_everything = True
        return super().keys()

    def items(self):
        self.read_everything = True
        return super().items()

    def values(self):
        self.read_everything = True
        return super().values()

    def popitem(self):
        self.read_everything = True
        return super().popitem()

    def copy(self):
        self.read_everything = True
        return dict(super().items())

    def __or__(self, other):
        self.read_everything = True
        return super().__or__(other)

    def __ror__(self, other):
        self.read_everything = True
        return super().__ror__(other)

    def __repr__(self):  # also what `str(state)` and `f"{state}"` reach
        self.read_everything = True
        return super().__repr__()

    def __len__(self):  # also what `bool(state)` and `if state:` reach
        self.read_everything = True
        return super().__len__()

    def __eq__(self, other):
        self.read_everything = True
        return super().__eq__(other)

    def __ne__(self, other):
        self.read_everything = True
        return super().__ne__(other)
