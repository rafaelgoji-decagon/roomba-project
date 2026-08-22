"""Run Roomba Deck with its Raspberry Pi terminal console."""

import uvicorn

from terminal_ui import banner

HOST = "0.0.0.0"
PORT = 8000

if __name__ == "__main__":
    banner(HOST, PORT)
    uvicorn.run("app:app", host=HOST, port=PORT, access_log=False, log_level="warning")
