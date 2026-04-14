from __future__ import annotations
import os
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, jsonify, send_file
from logger_config import get_loggers

app = Flask(__name__)
app_log, http_log, ptz_log, privacy_log = get_loggers()

# --- Amcrest camera config from environment ---
CAM_IP      = os.environ["CAM_IP"]
CAM_USER    = os.environ["CAM_USER"]
CAM_PASS    = os.environ["CAM_PASS"]
CAM_CHANNEL = os.environ["CAM_CHANNEL"]
PTZ_SPEED   = int(os.environ["PTZ_SPEED"])

# Browser agent handles anything requiring the camera web UI (e.g. privacy mode)
BROWSER_AGENT_URL = os.environ.get("BROWSER_AGENT_URL", "http://localhost:1123")

# --- Privacy state (in-memory mirror of hardware state) ---
# ACL HOOK: Replace with per-user lookup when ACL is implemented.
_privacy_enabled = False

DIRECTION_MAP = {
    "up":    "Up",
    "down":  "Down",
    "left":  "Left",
    "right": "Right",
}


# ── Browser agent calls ───────────────────────────────────

def _browser_privacy(enable: bool) -> bool:
    """Ask the browser agent to click the privacy on/off button."""
    endpoint = f"{BROWSER_AGENT_URL}/privacy/{'on' if enable else 'off'}"
    try:
        resp = requests.post(endpoint, timeout=10)
        ok = resp.status_code == 200 and resp.json().get("status") == "success"
        if not ok:
            privacy_log.error(f"Browser agent privacy {'on' if enable else 'off'} failed: {resp.text}")
        return ok
    except requests.RequestException as e:
        privacy_log.error(f"Browser agent unreachable: {e}")
        return False


def _browser_get_privacy() -> bool | None:
    """Read current privacy state from the browser agent."""
    try:
        resp = requests.get(f"{BROWSER_AGENT_URL}/privacy/status", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("privacy")
        return None
    except requests.RequestException as e:
        privacy_log.warning(f"Could not read privacy state from browser agent: {e}")
        return None


def _sync_privacy_from_camera():
    """Sync in-memory state from the camera on page load."""
    global _privacy_enabled
    state = _browser_get_privacy()
    if state is None:
        privacy_log.warning("Could not read camera privacy state — keeping current value")
        return
    _privacy_enabled = state
    privacy_log.info(f"Privacy sync: {'ON' if state else 'OFF'}")


# Sync on startup
_sync_privacy_from_camera()


# ── CGI helpers ───────────────────────────────────────────

def is_privacy_on() -> bool:
    # ACL HOOK: Check requesting user's privacy state here when ACL is added.
    return _privacy_enabled


def ptz_command(action: str, code: str) -> bool:
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action={action}&channel={CAM_CHANNEL}"
        f"&code={code}&arg1={PTZ_SPEED}&arg2={PTZ_SPEED}&arg3=0"
    )
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        ok = resp.status_code == 200
        ptz_log.info(f"PTZ {action} {code} -> {resp.status_code}")
        return ok
    except requests.RequestException as e:
        ptz_log.error(f"PTZ error ({action} {code}): {e}")
        return False


def ptz_preset(preset_id: int = 1) -> bool:
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action=start&channel={CAM_CHANNEL}"
        f"&code=GotoPreset&arg1=0&arg2={preset_id}&arg3=0"
    )
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        ok = resp.status_code == 200
        ptz_log.info(f"PTZ GotoPreset {preset_id} -> {resp.status_code}")
        return ok
    except requests.RequestException as e:
        ptz_log.error(f"PTZ preset error: {e}")
        return False


# ── Routes ────────────────────────────────────────────────

@app.route('/')
def index():
    pi_ip = os.environ["PI_IP_TS"]
    _sync_privacy_from_camera()
    http_log.info(f"GET / privacy={'ON' if is_privacy_on() else 'OFF'}")
    return render_template('index.html', pi_ip=pi_ip, privacy=is_privacy_on())


@app.route('/privacy-image')
def privacy_image():
    return send_file('./privacy.png', mimetype='image/png')


@app.route('/api/privacy/status')
def privacy_status():
    return jsonify({"privacy": is_privacy_on()})


@app.route('/api/privacy/on', methods=['POST'])
def privacy_on():
    # ACL HOOK: Check if requesting user has permission to enable privacy mode.
    global _privacy_enabled
    ok = _browser_privacy(True)
    if not ok:
        return jsonify({"status": "error", "message": "Privacy command failed"}), 502
    _privacy_enabled = True
    privacy_log.info("Privacy mode: ON")
    return jsonify({"status": "success", "privacy": True})


@app.route('/api/privacy/off', methods=['POST'])
def privacy_off():
    # ACL HOOK: Check if requesting user has permission to disable privacy mode.
    global _privacy_enabled
    ok = _browser_privacy(False)
    if not ok:
        return jsonify({"status": "error", "message": "Privacy command failed"}), 502
    _privacy_enabled = False
    privacy_log.info("Privacy mode: OFF")
    return jsonify({"status": "success", "privacy": False})


@app.route('/api/move/start/<direction>')
def move_start(direction: str):
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    direction = direction.lower()
    if direction not in DIRECTION_MAP:
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    ok = ptz_command("start", DIRECTION_MAP[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    return jsonify({"status": "success", "action": "start", "direction": direction})


@app.route('/api/move/stop/<direction>')
def move_stop(direction: str):
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    direction = direction.lower()
    if direction not in DIRECTION_MAP:
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    ok = ptz_command("stop", DIRECTION_MAP[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    return jsonify({"status": "success", "action": "stop", "direction": direction})


@app.route('/api/home')
def home_camera():
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    ok = ptz_preset(1)
    if not ok:
        return jsonify({"status": "error", "message": "Home preset failed"}), 502
    ptz_log.info("Homed to preset 1")
    return jsonify({"status": "success", "action": "home"})


if __name__ == "__main__":
    app_log.info("Gouda Gaze starting")
    app.run(host='0.0.0.0', port=1122, debug=False)