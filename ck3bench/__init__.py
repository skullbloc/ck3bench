"""ck3bench: the research harness runner over the ck3agent product.

An experiment is ONE TOML file (`ck3bench/experiments/*.toml`, schema in
`ck3bench.experiment`); the registration's conditions block is generated from
it and the mirror is checked.

The package imports nothing outside itself but the ck3agent wheel, at the
version `pyproject.toml` pins. The cell runner behind `ck3bench run` drives a
research checkout and is not part of the public distribution: without it `run`
refuses `not_implemented`.
"""
from ._version import __version__

__all__ = ["__version__"]
