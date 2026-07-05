# tests/test_h3_bier_functionality.py
import os
import sys
import math
import unittest
from dataclasses import dataclass
from pathlib import Path
import importlib

import networkx as nx
import h3


# -----------------------------
# Import your project modules
# -----------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# This matches how your StarPerf code imports H3_BIER_Main. :contentReference[oaicite:2]{index=2}
H3_BIER_Main = importlib.import_module(
    "src.XML_constellation.constellation_routing.routing_policy_plugin.h3_bier.main"
)
H3_BIER_logic = importlib.import_module(
    "src.XML_constellation.constellation_routing.routing_policy_plugin.h3_bier.logic"
)


# -----------------------------
# Small helper models
# -----------------------------
@dataclass
class User:
    user_id: str
    latitude: float
    longitude: float


@dataclass
class Satellite:
    id: int
    latitude: list  # list indexed by t-1
    longitude: list
    altitude: list


# -----------------------------
# H3 compatibility helpers (v3 vs v4)
# -----------------------------
def h3_geo_to_cell(lat: float, lon: float, res: int) -> str:
    if hasattr(h3, "geo_to_h3"):
        return h3.geo_to_h3(lat, lon, res)  # h3-py v3.x
    return h3.latlng_to_cell(lat, lon, res)  # h3-py v4.x


def h3_cell_to_parent(cell: str, res: int) -> str:
    if hasattr(h3, "h3_to_parent"):
        return h3.h3_to_parent(cell, res)  # v3.x
    return h3.cell_to_parent(cell, res)  # v4.x


def h3_cell_center(cell: str) -> tuple[float, float]:
    if hasattr(h3, "h3_to_geo"):
        lat, lon = h3.h3_to_geo(cell)  # v3.x
        return float(lat), float(lon)
    lat, lon = h3.cell_to_latlng(cell)  # v4.x
    return float(lat), float(lon)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


# -----------------------------
# Trace utilities
# -----------------------------
def count_branch_nodes(trace_node) -> int:
    """Count how many trace nodes have >1 branches."""
    if trace_node is None:
        return 0
    here = 1 if len(getattr(trace_node, "branches", [])) > 1 else 0
    return here + sum(count_branch_nodes(ch) for ch in trace_node.branches)


# -----------------------------
# Scenario builder (mini constellation)
# -----------------------------
def build_mini_constellation_for_scenario(
    source_latlon: tuple[float, float],
    dest_latlons: list[tuple[float, float]],
    t: int = 1,
    plan_res: int = 0,
    dest_res: int = 1,
    extra_egress_sats: int = 0,
):
    """
    Build a tiny constellation that guarantees:
      - at least one satellite exists in every forwarding cell along the H3 plan
      - receivers' nearest satellites are inside their destination parent cell (so IGMP delivery happens)
    """
    source_user = User("src", source_latlon[0], source_latlon[1])
    dest_users = [User(f"dst{i}", lat, lon) for i, (lat, lon) in enumerate(dest_latlons)]

    # Compute forwarding cells (res0) for source and each destination
    source_cell_dest_res = h3_geo_to_cell(source_user.latitude, source_user.longitude, dest_res)
    source_cell_plan_res = h3_cell_to_parent(source_cell_dest_res, plan_res)

    dest_cells_plan = []
    for u in dest_users:
        c_dest = h3_geo_to_cell(u.latitude, u.longitude, dest_res)
        c_plan = h3_cell_to_parent(c_dest, plan_res)
        dest_cells_plan.append(c_plan)

    # Build union of all path cells in the forwarding resolution
    all_plan_cells = set([source_cell_plan_res])
    for dc in dest_cells_plan:
        for c in H3_BIER_Main.generate_h3_path(source_cell_plan_res, dc):
            all_plan_cells.add(c)

    # Create satellites: 1 per cell center
    sat_map = {}
    next_sat_id = 1
    cell_to_sat_ids = {}

    for cell in sorted(all_plan_cells):
        lat, lon = h3_cell_center(cell)
        sat = Satellite(
            id=next_sat_id,
            latitude=[lat] * t,
            longitude=[lon] * t,
            altitude=[550.0] * t,
        )
        sat_map[sat.id] = sat
        cell_to_sat_ids.setdefault(cell, []).append(sat.id)
        next_sat_id += 1

    # Optionally add extra satellites in each destination cell (to test multi-egress visualization/delivery)
    if extra_egress_sats > 0:
        for dc in set(dest_cells_plan):
            base_lat, base_lon = h3_cell_center(dc)
            for k in range(extra_egress_sats):
                # Tiny jitter; if it escapes the cell, fallback to center
                lat = base_lat + 0.00005 * (k + 1)
                lon = base_lon + 0.00005 * (k + 1)
                if h3_geo_to_cell(lat, lon, plan_res) != dc:
                    lat, lon = base_lat, base_lon

                sat = Satellite(
                    id=next_sat_id,
                    latitude=[lat] * t,
                    longitude=[lon] * t,
                    altitude=[550.0] * t,
                )
                sat_map[sat.id] = sat
                cell_to_sat_ids.setdefault(dc, []).append(sat.id)
                next_sat_id += 1

    # Build a dense graph so dijkstra always finds a path
    G = nx.Graph()
    for sid in sat_map:
        G.add_node(f"satellite_{sid}")

    sat_items = list(sat_map.values())
    for i in range(len(sat_items)):
        for j in range(i + 1, len(sat_items)):
            a, b = sat_items[i], sat_items[j]
            w = haversine_km(a.latitude[t - 1], a.longitude[t - 1], b.latitude[t - 1], b.longitude[t - 1])
            # Ensure strictly positive weights
            G.add_edge(f"satellite_{a.id}", f"satellite_{b.id}", weight=max(w, 0.001))

    return G, sat_map, source_user, dest_users


# -----------------------------
# Tests
# -----------------------------
class TestH3BIERFunction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Disable visualization in unit tests (no GUI / no plot popups)
        if hasattr(H3_BIER_Main, "bier_visualizer"):
            H3_BIER_Main.bier_visualizer.plot_h3_bier_trace = lambda **kwargs: None

        # Help main.py find the H5 codebook quickly (optional).
        candidate = REPO_ROOT / "data" / "h3_cells_binary_res0-4.h5"
        if candidate.exists():
            os.environ["H3_CODEBOOK_H5"] = str(candidate)

    def test_single_destination_end_to_end(self):
        G, sat_map, src, dsts = build_mini_constellation_for_scenario(
            source_latlon=(0.0, 0.0),
            dest_latlons=[(10.0, 10.0)],
            t=1,
            extra_egress_sats=0,
        )

        packet, trace = H3_BIER_Main.H3_BIER_Function(G, sat_map, src, dsts, sh=0, t=1)
        self.assertIsNotNone(packet)
        self.assertIsNotNone(trace)

        # Packet should carry group_id + cell fields (binary in header, H3 plain in object).
        self.assertIn("group_id", packet.header)
        self.assertIn("source_cell", packet.header)
        self.assertIn("forwarding_cells", packet.header)
        self.assertIn("destination_cells", packet.header)

        self.assertTrue(len(packet.header["forwarding_cells"]) >= 1)
        self.assertTrue(len(packet.header["destination_cells"]) >= 1)

    def test_two_destinations_should_branch(self):
        # Two far destinations usually create a branching cell tree.
        G, sat_map, src, dsts = build_mini_constellation_for_scenario(
            source_latlon=(0.0, 0.0),
            dest_latlons=[(35.0, 10.0), (-35.0, 80.0)],
            t=1,
            extra_egress_sats=0,
        )

        packet, trace = H3_BIER_Main.H3_BIER_Function(G, sat_map, src, dsts, sh=0, t=1)
        self.assertIsNotNone(packet)
        self.assertIsNotNone(trace)

        # We expect at least one branching point in the trace tree for most such cases.
        branches = count_branch_nodes(trace)
        self.assertGreaterEqual(branches, 1)

    def test_igmp_delivery_is_called_in_last_cell(self):
        # Spy on deliver_igmp_in_cell calls.
        calls = []

        def spy_deliver_igmp_in_cell(*args, **kwargs):
            calls.append((args, kwargs))
            # Do not print / do not require real delivery logic for this unit test
            return None

        original_deliver = getattr(H3_BIER_logic, "deliver_igmp_in_cell")
        H3_BIER_logic.deliver_igmp_in_cell = spy_deliver_igmp_in_cell
        try:
            G, sat_map, src, dsts = build_mini_constellation_for_scenario(
                source_latlon=(0.0, 0.0),
                dest_latlons=[(10.0, 10.0), (10.1, 10.1)],
                t=1,
                extra_egress_sats=2,
            )

            packet, trace = H3_BIER_Main.H3_BIER_Function(G, sat_map, src, dsts, sh=0, t=1)
            self.assertIsNotNone(packet)
            self.assertIsNotNone(trace)

            # Should call IGMP delivery at least once when reaching a destination cell leaf.
            self.assertGreaterEqual(len(calls), 1)

            # Check group id passed
            group_id = packet.header.get("group_id", None)
            self.assertIsNotNone(group_id)
            # Some call should reference this group_id
            self.assertTrue(any(kwargs.get("group_id") == group_id for _, kwargs in calls))

        finally:
            H3_BIER_logic.deliver_igmp_in_cell = original_deliver

    def test_no_satellites_returns_none(self):
        G = nx.Graph()
        packet, trace = H3_BIER_Main.H3_BIER_Function(G, {}, User("src", 0, 0), [User("dst0", 10, 10)], sh=0, t=1)
        self.assertIsNone(packet)
        self.assertIsNone(trace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
