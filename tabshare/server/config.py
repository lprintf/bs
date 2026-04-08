import os

CHROME_PATH = os.getenv("CHROME_PATH")
PORT = int(os.getenv("PORT", 8085))
HOST = os.getenv("HOST", "0.0.0.0")
MAX_TABS = int(os.getenv("MAX_TABS", 10))

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "http://localhost:7880")
PUBLIC_LIVEKIT_URL = os.getenv("PUBLIC_LIVEKIT_URL", "http://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
