"""Load an OpenRadioss T01 time-history file as a table.

Prefers an existing CSV (`<stem>.csv` next to the T01, the layout th_to_csv
writes). Otherwise shells out to `th_to_csv_linux64_gf` on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pandas as pd


def load(t01: str) -> pd.DataFrame:
    t01 = os.path.abspath(t01)
    sibling = t01 + ".csv"
    if os.path.exists(sibling):
        df = pd.read_csv(sibling)
        df.columns = [str(c).strip() for c in df.columns]
        return df
    exe = shutil.which("th_to_csv_linux64_gf") or shutil.which("th_to_csv")
    if not exe:
        raise FileNotFoundError("th_to_csv_linux64_gf not on PATH and no sibling CSV")
    td = tempfile.mkdtemp()
    try:
        base = os.path.basename(t01)
        shutil.copy(t01, td)
        subprocess.run([exe, base], cwd=td, capture_output=True, check=False)
        csv_path = os.path.join(td, base + ".csv")
        if not os.path.exists(csv_path):
            raise FileNotFoundError(csv_path)
        df = pd.read_csv(csv_path)
        df.columns = [str(c).strip() for c in df.columns]
        return df
    finally:
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    import sys

    frame = load(sys.argv[1])
    cols = list(frame.columns)
    print(cols[33:45] if len(cols) > 45 else cols)
