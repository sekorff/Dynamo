from .base import Portal
from .ebay import Ebay
from .kleinanzeigen import Kleinanzeigen

REGISTRY: dict[str, type[Portal]] = {"kleinanzeigen": Kleinanzeigen, "ebay": Ebay}
