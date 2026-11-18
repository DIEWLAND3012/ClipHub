"""The one place the app version lives (the window, the boot screen, updates and CI read it here)."""
__version__ = "2.2.0"


def short() -> str:
    """'v2.2' for the sidebar."""
    return "v" + ".".join(__version__.split(".")[:2])
