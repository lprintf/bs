import os

CHROME_PATH = os.getenv("CHROME_PATH")
PORT = int(os.getenv("PORT", 8085))
HOST = os.getenv("HOST", "0.0.0.0")
MAX_TABS = int(os.getenv("MAX_TABS", 10))

SFU_URL = os.getenv("SFU_INTERNAL_URL", "http://127.0.0.1:8088")

# Browser fingerprint defaults
BROWSER_DATA_DIR = os.getenv("BROWSER_DATA_DIR", "/data/browsers")
DEFAULT_LOCALE = os.getenv("DEFAULT_LOCALE", "en-US")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Singapore")
