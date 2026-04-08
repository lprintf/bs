from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from server.services.tab_manager import tab_manager
from server.services.webrtc_bridge import WebRTCBridge

router = APIRouter()

@router.websocket("/ws/tab/{tab_id}")
async def websocket_endpoint(websocket: WebSocket, tab_id: str):
    await websocket.accept()
    
    page = tab_manager.get_tab(tab_id)
    if not page:
        await websocket.send_json({"error": "Tab not found"})
        await websocket.close()
        return

    bridge = WebRTCBridge(page, websocket, tab_id)
    await bridge.start()
