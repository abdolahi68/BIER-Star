\# BIER-Star: A Stateless Geographic Multicast Protocol for Satellite-Terrestrial Networks



\## Overview



\*\*BIER-Star\*\* is a simulation and evaluation framework for studying stateless geographic multicast routing in satellite-terrestrial networks. The project focuses on multicast forwarding over Low-Earth Orbit (LEO) satellite constellations, where satellite mobility, dynamic coverage, inter-satellite links, and ground connectivity create unique routing challenges.



The core idea of BIER-Star is to support efficient multicast delivery without requiring per-flow forwarding state inside the network. Instead, forwarding decisions are guided by compact bitstring-based and geographic information, enabling scalable multicast communication across satellite and terrestrial network segments.



The framework supports constellation modeling, routing-policy evaluation, satellite-ground connectivity analysis, H3-based geographic cell indexing, and visualization. It includes modules for XML-based and TLE-based constellation generation, multiple routing strategies, BIER-based forwarding, BIER-TE components, YETI-based routing, and performance evaluation metrics such as delay, coverage, bandwidth, hop count, and survivability under satellite failures.



\---



\## Key Features



\* Stateless geographic multicast routing for satellite-terrestrial networks

\* BIER-based forwarding for LEO satellite constellations

\* Support for XML-based and TLE-based constellation generation

\* Evaluation support for Starlink, OneWeb, Telesat, Kuiper, and Boeing constellation models

\* Inter-satellite link construction using configurable connectivity policies

\* Routing plugin architecture for adding and comparing routing methods

\* Support for shortest-path, second-shortest-path, least-hop, BIER, BIER-TE, YETI, and H3-based routing

\* H3 geographic indexing for cell-based multicast and geographic forwarding



\* Airplane mobility dataset support for dynamic satellite-terrestrial scenarios

\* HDF5-based storage for generated constellation and delay data

\---



\## Installation



Python 3.9 or later is recommended.



Clone the repository:



```bash

git clone https://github.com/abdolahi68/BIER-Star.git

cd BIER-Star

```



Create a virtual environment:



```bash

python -m venv .venv

```



Activate the environment on Windows:



```bash

.venv\\Scripts\\activate

```



Activate the environment on Linux/macOS:



```bash

source .venv/bin/activate

```



Install the required dependencies:



```bash

pip install -r requirements.txt

```



The project uses scientific, geographic, satellite-orbit, graph-analysis, and visualization libraries, including packages such as `numpy`, `pandas`, `networkx`, `h5py`, `h3`, `skyfield`, `sgp4`, `matplotlib`, and `plotly`.



\---



\## Quick Start



\### Run the main BIER-Star example



```bash

python BIER-Star\_Running.py

```



This script runs a sample satellite-terrestrial routing experiment. It loads a constellation model, applies a connectivity policy, selects a routing policy, defines source and destination users, and evaluates the resulting path.



\---

\### Inspect HDF5 constellation data



```bash

python kits/get\_h5file\_tree\_structure.py

python kits/get\_h5file\_satellite\_position\_data.py

python kits/get\_h5file\_satellite\_delay\_data.py

```



Large `.h5` files are used by the framework for generated constellation and delay data. If these files are not included in the repository, generate them using the provided scripts or place them manually in the expected `data/` or `config/` directories.



