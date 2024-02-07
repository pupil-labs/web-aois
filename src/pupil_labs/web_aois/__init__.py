"""Top-level entry-point for the pupil_labs_web_aois package"""

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:
    from importlib_metadata import PackageNotFoundError, version

try:
    __version__ = version("pupil_labs.web_aois")
except PackageNotFoundError:
    # package is not installed
    pass

__all__ = ["__version__"]
