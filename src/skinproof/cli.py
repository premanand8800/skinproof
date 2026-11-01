import argparse


def main() -> None:
    ap = argparse.ArgumentParser(prog="skinproof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--port", type=int, default=8100)
    s.add_argument("--host", default="127.0.0.1")
    sub.add_parser("balance", help="print YouCam unit balance")
    a = ap.parse_args()
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("skinproof.web:app_factory", factory=True, host=a.host, port=a.port)
    else:
        from .youcam import YouCamClient
        print("units:", YouCamClient().balance())
