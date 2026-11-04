"""The common interface every source adapter implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Dict, List

from ..http import Transport

Rows = Dict[str, List[dict]]  # table name -> rows ready for the database


class Source(ABC):
    name: str = ""
    label: str = ""

    def __init__(self, transport: Transport):
        self.t = transport

    @property
    def calls(self) -> int:
        return self.t.calls

    @abstractmethod
    def fetch(self, start: date, end: date) -> Rows:
        """Pull every row for start..end (inclusive). Must raise on any error, never return partial data silently."""

    @staticmethod
    def total_rows(rows: Rows) -> int:
        return sum(len(v) for v in rows.values())
