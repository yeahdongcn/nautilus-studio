from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Nautilus Studio — AI Film Workshop")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "long_video_studio.app:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )
