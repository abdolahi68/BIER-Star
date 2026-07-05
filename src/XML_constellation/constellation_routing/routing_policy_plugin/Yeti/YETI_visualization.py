# Yeti 3D globe visualization for forwarding paths.
# Satellite nodes and terminal nodes are resolved by their full node names.

from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go

R_EARTH_KM = 6371
OCEAN_COLOR             = "rgba(60, 90, 180, 1.0)"
ISL_TOPOLOGY_COLOR      = "rgba(128, 128, 128, 0.4)"
ISL_TOPOLOGY_WIDTH      = 0.5
SATELLITE_COLOR         = "rgba(200, 200, 200, 0.8)"
SATELLITE_LABEL_COLOR   = "rgba(220, 220, 220, 0.7)"
SATELLITE_LABEL_SIZE    = 7
TERMINAL_COLOR          = "rgba(255, 200, 50, 1.0)"
TERMINAL_LABEL_COLOR    = "rgba(255, 220, 100, 0.9)"
TERMINAL_LABEL_SIZE     = 8
YETI_PATH_COLOR         = "rgba(255, 0, 255, 1.0)"
YETI_PATH_WIDTH         = 4


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def latlon_to_xyz(lat, lon, radius):
    lat_rad, lon_rad = np.radians(lat), np.radians(lon)
    x = radius * np.cos(lat_rad) * np.cos(lon_rad)
    y = radius * np.cos(lat_rad) * np.sin(lon_rad)
    z = radius * np.sin(lat_rad)
    return x, y, z


# Convert one satellite position to 3D globe coordinates.
def _sat_xyz(sat, t):
    return latlon_to_xyz(
        sat.latitude[t - 1],
        sat.longitude[t - 1],
        R_EARTH_KM + sat.altitude[t - 1],
    )


# Convert one terminal position to ground-level 3D coordinates.
def _terminal_xyz(term, t):
    lat = term.latitude  if not hasattr(term.latitude,  "__getitem__") else term.latitude[t - 1]
    lon = term.longitude if not hasattr(term.longitude, "__getitem__") else term.longitude[t - 1]
    return latlon_to_xyz(lat, lon, R_EARTH_KM)


# ---------------------------------------------------------------------------
# Node resolver  (full name → xyz, no numeric-suffix collision)
# ---------------------------------------------------------------------------

# Resolve satellite, beam, or airplane node names to plot coordinates.
def _resolve_node_xyz(node_id, sat_map, terminal_map, t):
    node_str = str(node_id)

    if node_str.startswith("satellite_"):
        try:
            raw_id = int(node_str.split("_")[-1])
            sat = sat_map.get(raw_id)
            if sat is not None:
                return _sat_xyz(sat, t)
        except (ValueError, IndexError):
            pass
        return None

    if terminal_map:
        term = terminal_map.get(node_str) or terminal_map.get(node_id)
        if term is not None:
            return _terminal_xyz(term, t)

    return None


# ---------------------------------------------------------------------------
# Main plot function
# ---------------------------------------------------------------------------

# Draw the Yeti forwarding paths on a 3D globe and save the HTML plot.
def plot_yeti_forwarding_paths(
    sh,
    t,
    sat_map,
    network_edges,
    all_paths,
    title: str = "Yeti Shortest-Path Multicast Visualization",
    output_html: str = "yeti_baseline_globe.html",
    show: bool = True,
    terminal_map: dict | None = None,
):
    print("[Yeti Visualizer] Generating 3D plot...")
    fig = go.Figure()

    # ------------------------------------------------------------------
    # Layer 1 — Earth sphere
    # ------------------------------------------------------------------
    u, v = np.mgrid[-np.pi:np.pi:150j, 0:np.pi:75j]
    x_e = R_EARTH_KM * np.cos(u) * np.sin(v)
    y_e = R_EARTH_KM * np.sin(u) * np.sin(v)
    z_e = R_EARTH_KM * np.cos(v)
    fig.add_trace(go.Surface(
        x=x_e, y=y_e, z=z_e,
        colorscale=[[0, OCEAN_COLOR], [1, OCEAN_COLOR]],
        showscale=False,
        name="Earth",
        hoverinfo="none",
    ))

    # ------------------------------------------------------------------
    # Layer 2 — ISL topology grid (satellite-to-satellite edges only)
    # ------------------------------------------------------------------
    isl_x, isl_y, isl_z = [], [], []
    for u_id, v_id in network_edges:
        try:
            u_str, v_str = str(u_id), str(v_id)
            if not (u_str.startswith("satellite_") and v_str.startswith("satellite_")):
                continue
            u_sat = sat_map.get(int(u_str.split("_")[-1]))
            v_sat = sat_map.get(int(v_str.split("_")[-1]))
            if u_sat and v_sat:
                ux, uy, uz = _sat_xyz(u_sat, t)
                vx, vy, vz = _sat_xyz(v_sat, t)
                isl_x.extend([ux, vx, None])
                isl_y.extend([uy, vy, None])
                isl_z.extend([uz, vz, None])
        except (ValueError, IndexError):
            continue

    fig.add_trace(go.Scatter3d(
        x=isl_x, y=isl_y, z=isl_z,
        mode="lines",
        line=dict(width=ISL_TOPOLOGY_WIDTH, color=ISL_TOPOLOGY_COLOR),
        name="ISL Topology",
        hoverinfo="none",
    ))

    # ------------------------------------------------------------------
    # Layer 3 — Satellite constellation (markers + ID labels)
    # ------------------------------------------------------------------
    sats_x, sats_y, sats_z, sats_text = [], [], [], []
    for sat_id, sat in sat_map.items():
        x, y, z = _sat_xyz(sat, t)
        sats_x.append(x)
        sats_y.append(y)
        sats_z.append(z)
        sats_text.append(f"satellite_{sat_id}")

    fig.add_trace(go.Scatter3d(
        x=sats_x, y=sats_y, z=sats_z,
        text=sats_text,
        mode="markers+text",
        marker=dict(size=2, color=SATELLITE_COLOR),
        textfont=dict(size=SATELLITE_LABEL_SIZE, color=SATELLITE_LABEL_COLOR),
        name="Constellation",
        hoverinfo="text",
    ))

    # ------------------------------------------------------------------
    # Layer 4 — Beam / terminal endpoints (markers + labels, ground level)
    # ------------------------------------------------------------------
    if terminal_map:
        term_x, term_y, term_z, term_text = [], [], [], []
        for node_name, term in terminal_map.items():
            x, y, z = _terminal_xyz(term, t)
            term_x.append(x)
            term_y.append(y)
            term_z.append(z)
            term_text.append(str(node_name))

        fig.add_trace(go.Scatter3d(
            x=term_x, y=term_y, z=term_z,
            text=term_text,
            mode="markers+text",
            marker=dict(size=5, color=TERMINAL_COLOR, symbol="diamond"),
            textfont=dict(size=TERMINAL_LABEL_SIZE, color=TERMINAL_LABEL_COLOR),
            name="Beam / Terminal Endpoints",
            hoverinfo="text",
        ))

    # ------------------------------------------------------------------
    # Layer 5 — Yeti forwarding paths
    # ------------------------------------------------------------------
    for i, path_node_ids in enumerate(all_paths):
        path_x, path_y, path_z = [], [], []
        for node_id in path_node_ids:
            xyz = _resolve_node_xyz(node_id, sat_map, terminal_map, t)
            if xyz is not None:
                path_x.append(xyz[0])
                path_y.append(xyz[1])
                path_z.append(xyz[2])

        if len(path_x) > 1:
            fig.add_trace(go.Scatter3d(
                x=path_x, y=path_y, z=path_z,
                mode="lines+markers",
                line=dict(width=YETI_PATH_WIDTH, color=YETI_PATH_COLOR),
                marker=dict(size=3, color=YETI_PATH_COLOR),
                name=f"Yeti Path {i + 1}",
            ))

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    fig.update_layout(
        title_text=title,
        title_x=0.5,
        scene=dict(
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            aspectmode="cube",
            bgcolor="rgb(10,10,20)",
        ),
        margin={"r": 0, "t": 40, "l": 0, "b": 0},
        legend=dict(
            yanchor="top", y=0.99,
            xanchor="left", x=0.01,
            bgcolor="rgba(0,0,0,0.5)",
            font=dict(color="white"),
        ),
    )

    output_path = Path(output_html)
    fig.write_html(str(output_path))
    print(f"[Yeti Visualizer] HTML saved -> {output_path}")
    if show:
        fig.show()

    return str(output_path)
