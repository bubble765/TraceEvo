"""Source-specific adapters that preserve origin and licensing metadata."""

from .pmc import PMCOpenAccessAdapter
from .who_manifest import WHOManifestAdapter

__all__ = ["PMCOpenAccessAdapter", "WHOManifestAdapter"]
