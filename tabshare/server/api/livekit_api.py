from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import uuid
from livekit import api
from server.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, PUBLIC_LIVEKIT_URL
from server.services.tab_manager import tab_manager

router = APIRouter()

class TokenResponse(BaseModel):
    token: str
    url: str

@router.get("/api/livekit-token/{tab_id}", response_model=TokenResponse)
async def get_livekit_token(tab_id: str):
    """
    Generate a LiveKit JWT token for a Viewer to subscribe to the tab's stream.
    """
    if tab_id not in tab_manager.tabs:
        raise HTTPException(status_code=404, detail="Tab not found")
        
    viewer_id = f"viewer-{uuid.uuid4().hex[:8]}"
    room_name = f"room_{tab_id}"
    
    # We create a token scoped exactly to what a viewer needs:
    # Just joining the room and subscribing, NO publishing allowed.
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity(viewer_id) \
        .with_name(f"Viewer {viewer_id}") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=False,       # viewers just watch
            can_publish_data=True,   # for chatting or data if we ever use livekit data channels
            can_subscribe=True
        ))
    
    return TokenResponse(
        token=token.to_jwt(),
        url=PUBLIC_LIVEKIT_URL
    )
