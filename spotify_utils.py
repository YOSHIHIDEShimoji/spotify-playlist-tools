import os
import socket
import subprocess


def free_redirect_port() -> None:
    """Kill any stale process holding the Spotify OAuth redirect port."""
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "")
    try:
        port = int(redirect_uri.rsplit(":", 1)[-1].split("/")[0])
    except (ValueError, IndexError):
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return
    result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
    for pid_str in result.stdout.strip().splitlines():
        try:
            os.kill(int(pid_str), 15)  # SIGTERM
        except (ValueError, ProcessLookupError):
            pass
