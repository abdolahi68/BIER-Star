# BIER_TE/BIER_visualization.py
"""
3D globe visualization for BIER-TE forwarding paths.

FIX: terminal nodes (e.g. "terminal_55_1") are no longer misinterpreted as
satellite IDs.  Each node in a forwarding path is classified by its prefix:
  - "satellite_*"  → plotted at the satellite's lat/lon/altitude
  - "terminal_*"   → plotted at the terminal's ground coordinates
  - anything else  → skipped with a warning
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import plotly.graph_objects as go

R_EARTH_KM  = 6371
OCEAN_COLOR = "rgba(60, 90, 180, 1.0)"
GRID_COLOR  = "rgba(128, 128, 128, 0.3)"
GRID_WIDTH  = 1
LABEL_COLOR = "rgba(220, 220, 220, 0.8)"
LABEL_SIZE  = 8
PATH_COLOR  = "rgba(255, 0, 255, 1.0)"
PATH_WIDTH  = 4
TERM_COLOR  = "rgba(255, 165, 0, 1.0)"   # orange for terminal nodes


def latlon_to_xyz(lat, lon, radius):
    lat_rad, lon_rad = np.radians(lat), np.radians(lon)
    x = radius * np.cos(lat_rad) * np.cos(lon_rad)
    y = radius * np.cos(lat_rad) * np.sin(lon_rad)
    z = radius * np.sin(lat_rad)
    return x, y, z


def _node_xyz(
    node_id: str,
    sat_map: Dict,
    terminal_coords: Optional[Dict[str, Tuple[float, float]]],
    t: int,
) -> Optional[Tuple[float, float, float]]:
    """
    Return (x, y, z) for a node.

    Parameters
    ----------
    node_id:
        String such as "satellite_5" or "terminal_55_1".
    sat_map:
        {sat_id (int): satellite_object}  from the constellation.
    terminal_coords:
        {terminal_id: (lat, lon)} for ground terminals.
        If None or the terminal is absent, the terminal is skipped.
    t:
        Timeslot index (1-based).
    """
    if node_id.startswith("satellite_"):
        try:
            sat_id = int(node_id.split("_")[-1])
            sat    = sat_map.get(sat_id)
            if sat is None:
                return None
            return latlon_to_xyz(
                sat.latitude[t - 1],
                sat.longitude[t - 1],
                R_EARTH_KM + sat.altitude[t - 1],
            )
        except (ValueError, IndexError):
            return None

    elif node_id.startswith("terminal_") or node_id.startswith("airplane_"):
        if terminal_coords is None:
            return None
        coords = terminal_coords.get(node_id)
        if coords is None:
            return None
        lat, lon = coords
        # Terminals are on the ground (or at a nominal low altitude)
        return latlon_to_xyz(lat, lon, R_EARTH_KM + 0.01)

    else:
        print(f"[BIER-TE Visualizer] Unknown node type: {node_id!r} — skipped.")
        return None


def plot_bier_te_forwarding_paths(
    sh,
    t: int,
    sat_map: Dict,
    network_edges,
    all_paths: List[List[str]],
    terminal_coords: Optional[Dict[str, Tuple[float, float]]] = None,
    title: str = "BIER-TE Forwarding Paths",
    output_html: str = "bier_te_globe.html",
    show: bool = True,
):
    """
    Plot BIER-TE forwarding paths on a 3D globe.

    Parameters
    ----------
    sat_map:
        {sat_id (int): satellite_object}.
    network_edges:
        Iterable of (node_a, node_b) edge tuples for the ISL grid.
    all_paths:
        List of forwarding paths.  Each path is a list of node ID strings
        (e.g. ["satellite_2", "satellite_5", "satellite_55", "terminal_55_1"]).
    terminal_coords:
        {terminal_id: (lat, lon)}.  Required to plot terminal positions.
        Pass None to skip terminals in the visualization.
    """
    print("[BIER-TE Visualizer] Generating 3D globe plot...")
    fig = go.Figure()

    # --- Earth sphere ---
    u, v = np.mgrid[-np.pi:np.pi:150j, 0:np.pi:75j]
    fig.add_trace(go.Surface(
        x=R_EARTH_KM * np.cos(u) * np.sin(v),
        y=R_EARTH_KM * np.sin(u) * np.sin(v),
        z=R_EARTH_KM * np.cos(v),
        colorscale=[[0, OCEAN_COLOR], [1, OCEAN_COLOR]],
        showscale=False, name="Earth", hoverinfo="none",
    ))

    # --- Satellite positions ---
    sats_x, sats_y, sats_z, sats_labels = [], [], [], []
    for sat_id, sat in sat_map.items():
        x, y, z = latlon_to_xyz(
            sat.latitude[t - 1], sat.longitude[t - 1],
            R_EARTH_KM + sat.altitude[t - 1],
        )
        sats_x.append(x); sats_y.append(y); sats_z.append(z)
        sats_labels.append(str(sat_id))

    fig.add_trace(go.Scatter3d(
        x=sats_x, y=sats_y, z=sats_z,
        mode="markers+text", text=sats_labels,
        textposition="top center",
        textfont=dict(size=LABEL_SIZE, color=LABEL_COLOR),
        marker=dict(size=1.5, color="rgba(200, 200, 200, 0.5)"),
        hoverinfo="text", name="Constellation",
    ))

    # --- ISL grid ---
    grid_x, grid_y, grid_z = [], [], []
    for u_id, v_id in network_edges:
        u_str, v_str = str(u_id), str(v_id)
        if not (u_str.startswith("satellite_") and v_str.startswith("satellite_")):
            continue
        try:
            u_sat = sat_map.get(int(u_str.split("_")[-1]))
            v_sat = sat_map.get(int(v_str.split("_")[-1]))
            if u_sat and v_sat:
                x1, y1, z1 = latlon_to_xyz(u_sat.latitude[t-1], u_sat.longitude[t-1],
                                            R_EARTH_KM + u_sat.altitude[t-1])
                x2, y2, z2 = latlon_to_xyz(v_sat.latitude[t-1], v_sat.longitude[t-1],
                                            R_EARTH_KM + v_sat.altitude[t-1])
                grid_x.extend([x1, x2, None])
                grid_y.extend([y1, y2, None])
                grid_z.extend([z1, z2, None])
        except (ValueError, IndexError):
            continue

    fig.add_trace(go.Scatter3d(
        x=grid_x, y=grid_y, z=grid_z,
        mode="lines", line=dict(width=GRID_WIDTH, color=GRID_COLOR),
        hoverinfo="none", name="ISL Grid",
    ))

    # --- Forwarding paths ---
    for i, path in enumerate(all_paths):
        path_x, path_y, path_z   = [], [], []
        node_colors: List[str]   = []

        for node_id in path:
            node_id = str(node_id)
            xyz = _node_xyz(node_id, sat_map, terminal_coords, t)
            if xyz is None:
                continue
            path_x.append(xyz[0])
            path_y.append(xyz[1])
            path_z.append(xyz[2])
            if node_id.startswith("terminal_") or node_id.startswith("airplane_"):
                node_colors.append(TERM_COLOR)
            else:
                node_colors.append(PATH_COLOR)

        if len(path_x) > 1:
            fig.add_trace(go.Scatter3d(
                x=path_x, y=path_y, z=path_z,
                mode="lines+markers",
                line=dict(width=PATH_WIDTH, color=PATH_COLOR),
                marker=dict(size=3.5, color=node_colors),
                name=f"Path {i + 1}",
            ))

    fig.update_layout(
        title_text=title, title_x=0.5,
        scene=dict(
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            zaxis=dict(visible=False), aspectmode="data",
            bgcolor="rgb(10,10,20)",
        ),
        margin={"r": 0, "t": 40, "l": 0, "b": 0},
        legend=dict(
            yanchor="top", y=0.99, xanchor="left", x=0.01,
            bgcolor="rgba(0,0,0,0.5)", font=dict(color="white"),
        ),
    )

    output_path = Path(output_html)
    fig.write_html(str(output_path))
    print(f"[BIER-TE Visualizer] Saved → {output_path}")
    if show:
        fig.show()
    return str(output_path)
