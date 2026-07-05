# h3_bier/h3_binary_codebook.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import h5py


class H3BinaryCodebook:
    """
    Loads H3 cell -> binary code mapping from an HDF5 file.
    The file must contain datasets named: res0_cells, res1_cells, ..., res4_cells
    Each dataset contains fields: h3_id, binary_code
    """

    def __init__(self, h5_path: str | Path):
        self.h5_path = Path(h5_path)
        if not self.h5_path.exists():
            raise FileNotFoundError(f"H3 binary codebook not found: {self.h5_path}")

        # Lazy caches: res -> {h3_id_str: binary_str}
        self._enc_cache: Dict[int, Dict[str, str]] = {}
        self._dec_cache: Dict[int, Dict[str, str]] = {}

    def _dataset_name(self, res: int) -> str:
        return f"res{res}_cells"

    def _ensure_loaded(self, res: int) -> None:
        if res in self._enc_cache:
            return

        dname = self._dataset_name(res)
        enc: Dict[str, str] = {}
        dec: Dict[str, str] = {}

        with h5py.File(self.h5_path, "r") as f:
            if dname not in f:
                raise KeyError(f"Dataset '{dname}' not found in {self.h5_path}")
            ds = f[dname]

            # ds fields are bytes like b'80e5fffffffffff'
            for row in ds:
                h3_id = row["h3_id"].decode("ascii")
                bcode = row["binary_code"].decode("ascii")
                enc[h3_id] = bcode
                dec[bcode] = h3_id

        self._enc_cache[res] = enc
        self._dec_cache[res] = dec

    def to_bin(self, h3_id: str, res: int) -> str:
        self._ensure_loaded(res)
        try:
            return self._enc_cache[res][h3_id]
        except KeyError:
            raise KeyError(f"H3 id '{h3_id}' not found in codebook for res={res}")

    def to_h3(self, bin_code: str, res: int) -> str:
        self._ensure_loaded(res)
        try:
            return self._dec_cache[res][bin_code]
        except KeyError:
            raise KeyError(f"Binary code '{bin_code}' not found in codebook for res={res}")

    def tree_to_bin(self, tree_h3: dict, res: int) -> dict:
        """
        Converts a nested dict tree keyed by H3 ids into the same structure keyed by binary codes.
        """
        out = {}
        for k, v in tree_h3.items():
            out[self.to_bin(k, res)] = self.tree_to_bin(v, res) if isinstance(v, dict) else {}
        return out
