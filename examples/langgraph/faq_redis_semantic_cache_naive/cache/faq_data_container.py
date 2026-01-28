"""Dataset loader used by the semantic cache benchmark."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd


class FAQDataContainer:
    """Loads FAQ and evaluation query dataframes from disk."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        base_dir = data_dir or Path(__file__).resolve().parent.parent / "data"
        if not base_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {base_dir}")

        faq_path = base_dir / "faq_pairs.csv"
        test_path = base_dir / "test_queries.csv"

        if not faq_path.exists() or not test_path.exists():
            raise FileNotFoundError(
                "Missing dataset files. Expected faq_pairs.csv "
                "and test_queries.csv in the data directory."
            )

        self.faq_df = pd.read_csv(faq_path)
        self.test_df = pd.read_csv(test_path)

    def __repr__(self) -> str:  # pragma: no cover - convenience only
        return (
            f"FAQDataContainer(faq_rows={len(self.faq_df)}, "
            f"test_rows={len(self.test_df)})"
        )
