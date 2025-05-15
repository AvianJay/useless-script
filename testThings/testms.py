import asyncio
import websockets
import json

async def main():
    uri = "wss://v.myself-bbs.com/ws"
    data = {"tid": "", "vid": "", "id": "AgADSgwAApIS4VQ"}
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps(data))
        response = await ws.recv()
        print(response)

asyncio.run(main())