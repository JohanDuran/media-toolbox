"""Toolbox route for ComfyUI: POST /toolbox/exit ends the ComfyUI process so Docker restarts it.

ComfyUI's /free moves models out of VRAM but keeps them in system RAM (13+ GB for FLUX on a 22 GB VM). The image
worker calls this after /free when the gateway hands the GPU to another tool, so all of that RAM comes back.
No nodes; only the route.
"""

import os
import threading

from aiohttp import web
from server import PromptServer


@PromptServer.instance.routes.post("/toolbox/exit")
async def toolbox_exit(request):
    print("[toolbox] exit requested: restarting ComfyUI to return its RAM", flush=True)
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return web.json_response({"exiting": True})


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
