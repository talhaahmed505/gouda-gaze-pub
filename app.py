import os
import time
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# --- Amcrest camera config from environment ---
CAM_IP       = os.getenv("CAM_IP")
CAM_USER     = os.getenv("CAM_USER")
CAM_PASS     = os.getenv("CAM_PASS")
CAM_CHANNEL  = os.getenv("CAM_CHANNEL")   # 0 = first channel
PTZ_SPEED    = int(os.environ["PTZ_SPEED"])
PTZ_DURATION = float(os.environ["PTZ_DURATION"])

# Amcrest CGI direction codes
DIRECTION_MAP = {
    "up":    "Up",
    "down":  "Down",
    "left":  "Left",
    "right": "Right",
}

def ptz_command(action: str, code: str) -> bool:
    """
    Send a single PTZ CGI command to the Amcrest camera.
    action: "start" or "stop"
    code:   "Up", "Down", "Left", "Right"
    Returns True on success, False on failure.
    """
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action={action}&channel={CAM_CHANNEL}"
        f"&code={code}&arg1={PTZ_SPEED}&arg2={PTZ_SPEED}&arg3=0"
    )
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        return resp.status_code == 200
    except requests.RequestException as e:
        print(f"PTZ error ({action} {code}): {e}")
        return False


@app.route('/')
def index():
    pi_ip = os.getenv("PI_IP_TS", "localhost")
    return render_template('index.html', pi_ip=pi_ip)


@app.route('/api/move/<direction>')
def move_camera(direction: str):
    direction = direction.lower()

    if direction not in DIRECTION_MAP:
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400

    code = DIRECTION_MAP[direction]

    # Start moving
    ok = ptz_command("start", code)
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502

    # Hold for the configured duration then stop
    time.sleep(PTZ_DURATION)
    ptz_command("stop", code)

    print(f"PTZ: moved {direction} for {PTZ_DURATION}s")
    return jsonify({"status": "success", "direction": direction})


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=1122, debug=False)