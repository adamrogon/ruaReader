"""Classification rules shared across ingestion modules."""

from .bounce_codes import BounceClass, classify_bounce
from .esp import esp_from_org_name, esp_from_source
from .forwarding import Evaluation, classify_evaluation, looks_like_forwarder

__all__ = [
    "Evaluation",
    "classify_evaluation",
    "looks_like_forwarder",
    "esp_from_org_name",
    "esp_from_source",
    "BounceClass",
    "classify_bounce",
]
