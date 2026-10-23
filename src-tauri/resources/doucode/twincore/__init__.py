"""TwinCore Consensus: two preserved backbones joined by a recurrent semantic bus."""
from .spec import BackboneSpec, TwinCoreConfig, default_twincore_config
from .consensus import TwinProposal, TwinBlackboard, consensus_score

__all__ = [
    "BackboneSpec",
    "TwinCoreConfig",
    "default_twincore_config",
    "TwinProposal",
    "TwinBlackboard",
    "consensus_score",
]
