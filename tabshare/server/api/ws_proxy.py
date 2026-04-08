import asyncio
import urllib.request
import urllib.error
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, Response
import websockets
from server.config import LIVEKIT_URL

router = APIRouter()

@router.get("/livekit-proxy/rtc/validate")
@router.get("/livekit-proxy/rtc/v1/validate")
def livekit_validate(request: Request):
    query_string = request.url.query
    # Always proxy to /rtc/v1/validate for modern livekit
    target_url = f"{LIVEKIT_URL}/rtc/v1/validate?{query_string}"
    try:
        req = urllib.request.Request(target_url, method="GET")
        with urllib.request.urlopen(req) as response:
            body = response.read()
            return Response(content=body, status_code=response.status, media_type=response.headers.get_content_type())
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code)
    except Exception as e:
        return Response(content=str(e), status_code=500)

@router.websocket("/livekit-proxy/rtc")
@router.websocket("/livekit-proxy/rtc/v1")
async def livekit_proxy(websocket: WebSocket):
    subprotocol_str = websocket.headers.get("sec-websocket-protocol")
    subprotocols = [s.strip() for s in subprotocol_str.split(",")] if subprotocol_str else []
    accepted_protocol = subprotocols[0] if subprotocols else None

    await websocket.accept(subprotocol=accepted_protocol)
    
    query_string = websocket.url.query
    
    base_ws = LIVEKIT_URL.replace("http://", "ws://").replace("https://", "wss://")
    # Always proxy to /rtc/v1 on the target
    target_url = f"{base_ws}/rtc/v1?{query_string}"
    
    try:
        async with websockets.connect(target_url, subprotocols=subprotocols) as client_ws:
            async def forward_to_client():
                try:
                    async for message in client_ws:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except Exception:
                    pass

            async def forward_to_server():
                try:
                    while True:
                        message = await websocket.receive()
                        if "text" in message:
                            await client_ws.send(message["text"])
                        elif "bytes" in message:
                            await client_ws.send(message["bytes"])
                        elif "type" in message and message["type"] == "websocket.disconnect":
                            break
                except Exception:
                    pass

            await asyncio.gather(
                forward_to_client(),
                forward_to_server()
            )
    except Exception as e:
        print("LiveKit Proxy Error:", e)
    finally:
        try:
            await websocket.close()
        except:
            pass
