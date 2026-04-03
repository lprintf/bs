import os

CHROME_PATH = os.getenv("CHROME_PATH")
PORT = int(os.getenv("PORT", 8085))
HOST = os.getenv("HOST", "0.0.0.0")
MAX_TABS = int(os.getenv("MAX_TABS", 10))
