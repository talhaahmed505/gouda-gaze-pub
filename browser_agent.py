"""
gouda-browser: Playwright-based browser agent that controls the Amcrest camera
web UI for features not available in the CGI/RPC2 API.

Exposes a simple HTTP API on port 1123 consumed by the main gouda-gaze app.
Keeps a persistent authenticated browser session so each action is fast.
"""
import os
import threading
import time
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright, Page, Browser, Playwright

app = Flask(__name__)

CAM_IP   = os.environ["CAM_IP"]
CAM_USER = os.environ["CAM_USER"]
CAM_PASS = os.environ["CAM_PASS"]
CAM_URL  = f"http://{CAM_IP}"

# ── Browser session state ─────────────────────────────────

_pw: Playwright   = None
_browser: Browser = None
_page: Page       = None
_session_lock     = threading.Lock()
_ready            = False


def _dismiss_any_dialog(page: Page):
    """Handle any alert/confirm dialogs that may appear."""
    try:
        page.on("dialog", lambda d: d.accept())
    except Exception:
        pass


def _login(page: Page):
    """Log into the camera web UI."""
    page.goto(CAM_URL, wait_until="networkidle", timeout=15000)

    # Fill credentials
    page.fill("#login_user", CAM_USER)
    page.fill("#login_psw", CAM_PASS)

    # Handle any post-login dialogs (EULA, P2P notice, etc.)
    page.on("dialog", lambda d: d.accept())

    # Click login
    page.click("#b_login")

    # Wait for the main UI to appear — the preview tab is the landmark
    page.wait_for_selector(".u-tab.main", timeout=15000)
    print("[browser] Logged in successfully")


def _ensure_ptz_panel_visible(page: Page):
    """Make sure we're on the preview page with PTZ controls visible."""
    # The privacy button lives in the main preview panel
    try:
        page.wait_for_selector("#ptz_control_privacy_mask", timeout=5000)
    except Exception:
        # Navigate to preview tab if needed
        try:
            page.click("li[data-for='preview']")
            page.wait_for_selector("#ptz_control_privacy_mask", timeout=8000)
        except Exception as e:
            print(f"[browser] Could not find PTZ panel: {e}")
            raise


def _init_session():
    """Launch browser, log in, navigate to PTZ page. Called once at startup."""
    global _pw, _browser, _page, _ready

    print("[browser] Initialising Playwright session...")
    _pw      = sync_playwright().start()
    _browser = _pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    _page = _browser.new_page()
    _dismiss_any_dialog(_page)

    try:
        _login(_page)
        _ensure_ptz_panel_visible(_page)
        _ready = True
        print("[browser] Session ready")
    except Exception as e:
        print(f"[browser] Session init failed: {e}")
        _ready = False


def _recover_session():
    """Re-login if the session has expired."""
    global _ready
    print("[browser] Recovering session...")
    try:
        _login(_page)
        _ensure_ptz_panel_visible(_page)
        _ready = True
        print("[browser] Session recovered")
    except Exception as e:
        print(f"[browser] Session recovery failed: {e}")
        _ready = False


def _click_privacy(enable: bool) -> tuple[bool, str]:
    """
    Click the privacy on or off button in the camera web UI.
    Returns (success, message).
    """
    with _session_lock:
        if not _ready:
            return False, "Browser session not ready"

        selector = "#onOpenMask" if enable else "#onCloseMask"
        action   = "ON" if enable else "OFF"

        try:
            _ensure_ptz_panel_visible(_page)

            # Set up dialog handler before clicking — the camera shows an alert
            dialogs = []
            _page.once("dialog", lambda d: (dialogs.append(d.message), d.accept()))

            # Force-click since the inactive button has fn-hide class
            _page.evaluate(f"""
                document.querySelector('{selector}').click();
            """)

            # Brief wait for the dialog to appear and be dismissed
            time.sleep(0.5)

            print(f"[browser] Privacy {action} clicked. Dialog: {dialogs}")
            return True, f"Privacy {action}"

        except Exception as e:
            print(f"[browser] Click privacy {action} failed: {e}")
            # Try to recover the session for next call
            try:
                _recover_session()
            except Exception:
                pass
            return False, str(e)


def _get_privacy_state() -> bool | None:
    """
    Read current privacy state from the DOM.
    #onOpenMask visible  = privacy OFF (button to turn ON is showing)
    #onCloseMask visible = privacy ON  (button to turn OFF is showing)
    Returns True if privacy is on, False if off, None on error.
    """
    with _session_lock:
        if not _ready:
            return None
        try:
            _ensure_ptz_panel_visible(_page)
            # fn-hide class means hidden; if onCloseMask does NOT have fn-hide, privacy is on
            close_hidden = _page.evaluate("""
                document.querySelector('#onCloseMask').classList.contains('fn-hide')
            """)
            return not close_hidden
        except Exception as e:
            print(f"[browser] get_privacy_state error: {e}")
            return None


# ── Start session in background thread ───────────────────

threading.Thread(target=_init_session, daemon=True).start()


# ── Routes ────────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({"ready": _ready})


@app.route('/privacy/on', methods=['POST'])
def privacy_on():
    ok, msg = _click_privacy(True)
    if not ok:
        return jsonify({"status": "error", "message": msg}), 502
    return jsonify({"status": "success", "privacy": True})


@app.route('/privacy/off', methods=['POST'])
def privacy_off():
    ok, msg = _click_privacy(False)
    if not ok:
        return jsonify({"status": "error", "message": msg}), 502
    return jsonify({"status": "success", "privacy": False})


@app.route('/privacy/status')
def privacy_state():
    state = _get_privacy_state()
    if state is None:
        return jsonify({"status": "error", "message": "Could not read state"}), 502
    return jsonify({"status": "success", "privacy": state})


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=1123, debug=False)