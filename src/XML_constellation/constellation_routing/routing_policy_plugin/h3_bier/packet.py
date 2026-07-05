# h3_bier/packet.py

from __future__ import annotations

import copy
from typing import Any, Dict, Optional, Set


class H3BIERPacket:
    """
    Packet for H3-BIER (BIER-Star).

    source_cell           — binary cell ID of the forwarding-tree root
    routing_tree          — nested binary dict: all other tree cells + branching
    destination_cells     — binary cell IDs at destination_resolution
    forwarding_resolution — int (metadata)
    destination_resolution— int (metadata)
    group_id              — str (optional)
    """

    def __init__(
        self,
        source_cell_h3: str,
        current_tree_h3: Dict[str, Dict],
        destination_cells_h3: Set[str],
        forwarding_resolution: int,
        destination_resolution: int,
        codebook: Any,
        payload: Any,
        group_id: Optional[str] = None,
    ):
        self.source_cell_h3       = source_cell_h3
        self.current_tree_h3      = current_tree_h3
        self.destination_cells_h3 = set(destination_cells_h3)

        self.forwarding_resolution  = forwarding_resolution
        self.destination_resolution = destination_resolution

        self.payload = payload

        # Tracing (simulation only)
        self.trace = {
            "visited_satellites": [],
            "replication_points": [],
        }

        # Keep codebook reference (needed for deep-copied packets too)
        self._codebook = codebook

        # Build binary wire-format header
        self.header = self._build_binary_header(codebook, group_id)

    # ------------------------------------------------------------------
    # Wire-format header builder
    # ------------------------------------------------------------------

    def _build_binary_header(self, codebook: Any, group_id: Optional[str]) -> dict:

        routing_tree_bin = codebook.tree_to_bin(
            self.current_tree_h3, self.forwarding_resolution
        )
        destination_cells_bin = sorted(
            codebook.to_bin(c, self.destination_resolution)
            for c in self.destination_cells_h3
        )

        hdr = {
            "forwarding_resolution":  self.forwarding_resolution,
            "destination_resolution": self.destination_resolution,
            "source_cell":            codebook.to_bin(
                                          self.source_cell_h3,
                                          self.forwarding_resolution,
                                      ),
            "routing_tree":           routing_tree_bin,
            "destination_cells":      destination_cells_bin,
        }
        if group_id is not None:
            hdr["group_id"] = group_id
        return hdr

    # ------------------------------------------------------------------
    # Header bit-length metric
    # ------------------------------------------------------------------

    def _count_tree_cell_bits(self, tree_bin: dict, bits_per_cell: int) -> int:
        """Count the bits used by cell IDs in the forwarding tree."""
        total = 0
        for key, subtree in tree_bin.items():
            total += bits_per_cell
            if isinstance(subtree, dict) and subtree:
                total += self._count_tree_cell_bits(subtree, bits_per_cell)
        return total

    def forwarding_cell_count(self) -> int:
        """Count the H3 forwarding cells encoded in the packet header."""
        bits_per_fwd = len(self.header.get("source_cell", ""))
        if bits_per_fwd == 0:
            return 0
        tree_bits = self._count_tree_cell_bits(
            self.header.get("routing_tree", {}),
            bits_per_fwd,
        )
        return 1 + (tree_bits // bits_per_fwd)

    def header_bit_length(self) -> int:
        """Estimate the BIER-Star header bits stored in the cell IDs."""
        src_bin      = self.header.get("source_cell", "")
        bits_per_fwd = len(src_bin)
        src_bits     = bits_per_fwd                          # root cell

        routing_tree = self.header.get("routing_tree", {})
        tree_bits    = self._count_tree_cell_bits(routing_tree, bits_per_fwd)

        dst_cells    = self.header.get("destination_cells", [])
        dst_bits     = sum(len(b) for b in dst_cells)
        bits_per_dst = len(dst_cells[0]) if dst_cells else 0

        total = src_bits + tree_bits + dst_bits

        n_fwd = self.forwarding_cell_count()
        print(
            f"[H3BIERPacket] header_bit_length:"
            f"  {n_fwd} fwd cells × {bits_per_fwd} bits = {src_bits + tree_bits}"
            f"  |  {len(dst_cells)} dst cells × {bits_per_dst} bits = {dst_bits}"
            f"  |  total = {total}"
        )
        return total

    # ------------------------------------------------------------------
    # Forwarding helpers used by logic.py
    # ------------------------------------------------------------------

    def get_next_target_cells(self) -> Set[str]:
        """Get the next forwarding cells from the current tree position."""
        return set(self.current_tree_h3.keys())

    def create_replicated_packet_for_branch(
        self, branch_cell_h3: str
    ) -> Optional["H3BIERPacket"]:
        """Copy the packet for one branch of the forwarding tree."""
        if branch_cell_h3 not in self.current_tree_h3:
            return None

        new_packet = copy.deepcopy(self)
        new_packet.current_tree_h3 = self.current_tree_h3[branch_cell_h3]
        new_packet.source_cell_h3  = branch_cell_h3          # branch root

        # Rebuild wire-format header for the pruned subtree.
        new_packet.header = new_packet._build_binary_header(
            new_packet._codebook,
            new_packet.header.get("group_id"),
        )
        return new_packet

    # ------------------------------------------------------------------
    # Trace helpers (simulation only)
    # ------------------------------------------------------------------

    def add_to_trace(self, satellite_id: int) -> None:
        self.trace["visited_satellites"].append(satellite_id)

    def record_replication(self, satellite_id: int) -> None:
        self.trace["replication_points"].append(satellite_id)

    def __repr__(self) -> str:
        return (
            "H3BIERPacket(\n"
            f"  fwd_cells={self.forwarding_cell_count()}"
            f"  dst_cells={len(self.header.get('destination_cells', []))}"
            f"  header_bits={self.header_bit_length()}\n"
            f"  Visited Satellites: {self.trace['visited_satellites']}\n"
            ")"
        )
