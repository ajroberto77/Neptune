"""Neptune — Iridium Capital Management's quantitative risk intelligence platform.

See CLAUDE.md for the hard invariants that govern this codebase. In short:
three-layer architecture (Quant Engine / Risk Interface / untouchable Fundamental
Layer), the system never auto-executes, net portfolio beta is constrained to
|beta| <= 0.05, and the systematic short book exists only to hedge long-book beta.
"""

__version__ = "0.1.0"
