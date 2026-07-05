# BIER_TE/__init__.py
#
# User terminals are BIER-TE router nodes (BFRs/BFERs) in the forwarding domain.
# build_te_tables assigns:
#   - forwarding BPs to ISL edges and satellite-terminal links
#   - local_decap() BPs to destination-capable nodes (terminals, and optionally satellites)

from .bier_te_logic import (
    build_te_tables,
    ingress_process_te,
)

__all__ = [
    "build_te_tables",
    "ingress_process_te",
]
