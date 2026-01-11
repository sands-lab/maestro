# Re-export root_agent so packages can do `from coordinator import root_agent`
from .agent import root_agent  # noqa: F401
