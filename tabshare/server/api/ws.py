from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from server.services.tab_manager import tab_manager
from server.services.cdp_bridge import CDPBridge

router = APIRouter()

@router.websocket("/ws/tab/{tab_id}")
async def websocket_endpoint(websocket: WebSocket, tab_id: str):
    await websocket.accept()
    
    page = tab_manager.get_tab(tab_id)
    if not page:
        await websocket.send_json({"error": "Tab not found"})
        await websocket.close()
        return

    bridge = CDPBridge(page, websocket)
    await bridge.start()
