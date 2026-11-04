"""One adapter per data source, all behind the same `Source` interface."""
from .base import Source
from .ga4 import GA4Source
from .gsc import GSCSource
from .meta import MetaAdsSource

__all__ = ["Source", "GA4Source", "GSCSource", "MetaAdsSource"]
