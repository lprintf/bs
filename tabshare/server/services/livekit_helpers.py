from livekit import api
from server.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET
import uuid

def generate_publisher_token(tab_id: str) -> str:
    room_name = f"room_{tab_id}"
    pub_id = f"publisher-{uuid.uuid4().hex[:8]}"
    
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity(pub_id) \
        .with_name("TabShare Source") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_publish_data=True,
            can_subscribe=False
        ))
    return token.to_jwt()
