from .config import Settings
from .factory import create_app


app = create_app(Settings.from_env())
