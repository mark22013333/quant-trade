import numpy as np
import pandas as pd


def sanitize_position(series: pd.Series) -> pd.Series:
    """
    Normalize a position series to -1/0/1 integers.
    Any positive value -> 1, negative -> -1, NaN -> 0.
    """
    if series is None:
        raise ValueError("Position series is required")
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0)
    signed = np.sign(numeric)
    return pd.Series(signed, index=series.index).astype(int)


def add_signal_from_position(df: pd.DataFrame, position_col: str = "position", signal_col: str = "signal") -> pd.DataFrame:
    """
    Ensure signal column exists by diffing position. Signal is normalized to -1/0/1.
    """
    if position_col not in df.columns:
        raise ValueError(f"DataFrame must include '{position_col}'")
    df = df.copy()
    df[position_col] = sanitize_position(df[position_col])
    signal = df[position_col].diff().fillna(0.0)
    if len(signal) > 0:
        signal.iloc[0] = df[position_col].iloc[0]
    df[signal_col] = sanitize_position(signal)
    return df


def add_position_from_signal(df: pd.DataFrame, signal_col: str = "signal", position_col: str = "position") -> pd.DataFrame:
    """
    Ensure position column exists by cumulatively summing signal and clamping to -1/0/1.
    """
    if signal_col not in df.columns:
        raise ValueError(f"DataFrame must include '{signal_col}'")
    df = df.copy()
    signal = sanitize_position(df[signal_col])
    position = signal.cumsum()
    df[position_col] = sanitize_position(position)
    return df
