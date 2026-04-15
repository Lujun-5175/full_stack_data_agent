import importlib.metadata

try:
    # The _distribution_ name (in pyproject.toml) must be used, not the package name.
    __version__ = importlib.metadata.version("edaplot-vl")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0"  # Fallback for development mode
