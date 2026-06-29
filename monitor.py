import json
import logging
import os
import re
import ssl
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import psutil
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item

# ==========================================
# CONFIGURATION & LOGGING
# ==========================================
POLL_INTERVAL = 60
BG = "#1e1e1e"
BG_CARD = "#2a2a2a"
TEXT = "#e8e8e8"
TEXT_DIM = "#888888"
ORANGE = "#e07a30"
BAR_BG = "#3a3a3a"

APP_DIR = Path(os.environ.get('APPDATA', '')) / 'AntigravityUsageMonitor'
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DIR / 'config.json'
LOG_FILE = APP_DIR / 'debug.log'

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Also log to console if not frozen or in test mode
if not getattr(sys, 'frozen', False):
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    logging.getLogger('').addHandler(console)

logging.info("--- Application Started ---")

class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.models_data: Dict[str, Any] = {}
        self.selected_model_name: str = ""
        self.port: Optional[int] = None
        self.proto: str = "http"
        self.csrf_token: Optional[str] = None
        self.last_fetch_time: Optional[datetime] = None
        self.running: bool = True
        self.load_config()

    def load_config(self):
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    self.selected_model_name = cfg.get("selected_model", "")
                logging.info(f"Config loaded. Selected model: {self.selected_model_name}")
        except Exception as e:
            logging.error(f"Error loading config: {e}")

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({"selected_model": self.selected_model_name}, f)
            logging.info("Config saved.")
        except Exception as e:
            logging.error(f"Error saving config: {e}")

state = AppState()

# ==========================================
# BACKEND API LOGIC
# ==========================================
def find_antigravity_process() -> Tuple[Optional[int], Optional[str]]:
    """Returns (PID, csrf_token) by inspecting running processes using psutil."""
    logging.info("Searching for language_server process...")
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = proc.info.get('name', '')
                if not name:
                    continue
                if 'language_server' in name.lower():
                    cmdline = proc.info.get('cmdline', [])
                    if not cmdline:
                        continue
                    full_cmd = " ".join(cmdline)
                    if '--csrf_token' in full_cmd:
                        m_csrf = re.search(r'--csrf_token\s+([^\s]+)', full_cmd)
                        csrf_token = m_csrf.group(1) if m_csrf else None
                        logging.info(f"Found language_server PID: {proc.info['pid']}, CSRF: {csrf_token}")
                        return proc.info['pid'], csrf_token
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        logging.warning("language_server process not found.")
    except Exception as e:
        logging.error(f"Error finding process: {e}")
    return None, None

def find_listening_ports(pid: int) -> list[int]:
    """Find TCP listening ports for a given PID using psutil."""
    ports = []
    logging.info(f"Searching for listening ports for PID {pid}...")
    try:
        proc = psutil.Process(pid)
        for conn in proc.net_connections(kind='tcp'):
            if conn.status == psutil.CONN_LISTEN:
                laddr = conn.laddr
                if laddr and hasattr(laddr, 'port'):
                    ports.append(laddr.port)
        logging.info(f"Found ports: {ports}")
    except Exception as e:
        logging.error(f"Error finding ports: {e}")
    return ports

def discover_api_endpoint() -> bool:
    """Find port, proto and csrf_token. Returns True if successful."""
    pid, csrf = find_antigravity_process()
    if not pid or not csrf:
        return False
    
    ports = find_listening_ports(pid)
    path = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
    data = json.dumps({}).encode('utf-8')
    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "X-Codeium-Csrf-Token": csrf
    }

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for port in ports:
        for proto in ["http", "https"]:
            url = f"{proto}://127.0.0.1:{port}{path}"
            logging.debug(f"Testing endpoint {url} ...")
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, context=ctx, timeout=3) as res:
                    if res.getcode() == 200:
                        response_text = res.read().decode('utf-8')
                        logging.debug(f"Response (first 100 chars): {response_text[:100]}")
                        try:
                            resp = json.loads(response_text)
                            # Require 'userStatus' and some models to confirm it's the correct port
                            user_status = resp.get("userStatus", {})
                            models = user_status.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
                            if not models:
                                models = user_status.get("commandModelConfigs", {}).get("modelConfigs", [])
                            
                            if models:
                                logging.info(f"Successfully discovered API endpoint with data at {url}")
                                with state.lock:
                                    state.port = port
                                    state.proto = proto
                                    state.csrf_token = csrf
                                return True
                            else:
                                user_status_keys = list(user_status.keys()) if isinstance(user_status, dict) else type(user_status)
                                logging.debug(f"Endpoint {url} returned 200 but no models data. userStatus keys: {user_status_keys}")
                        except json.JSONDecodeError:
                            logging.debug(f"Endpoint {url} returned 200 but response is not JSON.")
            except Exception as e:
                logging.debug(f"Failed on {url}: {e}")
    logging.warning("Could not discover working API endpoint.")
    return False

def fetch_quota_data() -> bool:
    """Fetch quota data and update state. Returns True if successful."""
    with state.lock:
        if not state.port or not state.csrf_token:
            return False
        url = f"{state.proto}://127.0.0.1:{state.port}/exa.language_server_pb.LanguageServerService/GetUserStatus"
        csrf = state.csrf_token
    
    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "X-Codeium-Csrf-Token": csrf
    }
    data = json.dumps({}).encode('utf-8')
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        logging.debug(f"Fetching data from {url}")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, context=ctx, timeout=5) as res:
            response_text = res.read().decode('utf-8')
            resp = json.loads(response_text)
            
            user_status = resp.get("userStatus", {})
            models = user_status.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
            if not models:
                models = user_status.get("commandModelConfigs", {}).get("modelConfigs", [])
                
            new_models_data = {}
            for m in models:
                label = m.get("label")
                quota_info = m.get("quotaInfo")
                if label and quota_info:
                    if "Gemini" in label:
                        label = "Gemini"
                        if label in new_models_data:
                            continue
                    new_models_data[label] = {
                        "remainingFraction": quota_info.get("remainingFraction", 0),
                        "resetTime": quota_info.get("resetTime")
                    }
            
            logging.info(f"Successfully fetched {len(new_models_data)} models data.")
            with state.lock:
                state.models_data = new_models_data
                state.last_fetch_time = datetime.now(timezone.utc)
                # auto-select if empty
                if not state.selected_model_name and new_models_data:
                    prefs = ["Gemini 3.1 Pro (High)", "Gemini 3.1 Pro (Low)", "Claude Sonnet 4.6 (Thinking)"]
                    for p in prefs:
                        if p in new_models_data:
                            state.selected_model_name = p
                            state.save_config()
                            break
                    if not state.selected_model_name:
                        state.selected_model_name = list(new_models_data.keys())[0]
                        state.save_config()
            return True
    except Exception as e:
        logging.error(f"Fetch failed: {e}")
        # Reset port so it tries to discover again
        with state.lock:
            state.port = None
            state.csrf_token = None
        return False

def backend_worker():
    while state.running:
        if not state.port:
            if discover_api_endpoint():
                fetch_quota_data()
        else:
            fetch_quota_data()
        
        for _ in range(POLL_INTERVAL):
            if not state.running:
                break
            time.sleep(1)

# ==========================================
# TEST MODE (Closed-Loop Debugging)
# ==========================================
def test_backend_mode():
    logging.info("Running in --test-backend mode")
    print("Testing backend logic...")
    if discover_api_endpoint():
        print(f"Discovered Endpoint: {state.proto}://127.0.0.1:{state.port}")
        if fetch_quota_data():
            print(f"Successfully fetched {len(state.models_data)} models.")
            for name, data in state.models_data.items():
                print(f"  {name}: {data['remainingFraction']*100:.1f}%")
            sys.exit(0)
        else:
            print("Endpoint discovered, but failed to fetch data.")
            sys.exit(1)
    else:
        print("Failed to discover endpoint.")
        sys.exit(1)

# ==========================================
# GUI: PYSTRAY & ICON
# ==========================================
# (Omitted GUI implementations below are unchanged from earlier, just adding logging on errors)
def _get_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/bahnschrift.ttf", size)
        font.set_variation_by_name("Bold Condensed")
        return font
    except Exception:
        pass
    for name in ["ariblk.ttf", "segoeuib.ttf", "arialbd.ttf"]:
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()

def _fit_font(text: str, max_px: int) -> ImageFont.FreeTypeFont:
    lo, hi = 8, max_px
    best = _get_font(lo)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        font = _get_font(mid)
        bb = font.getbbox(text)
        if bb[2] - bb[0] <= max_px and bb[3] - bb[1] <= max_px:
            lo, best = mid, font
        else:
            hi = mid - 1
    return best

def make_icon() -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    with state.lock:
        model = state.selected_model_name
        data = state.models_data.get(model)
        
    if not data:
        draw.rounded_rectangle([0, 0, size, size], radius=12, fill=(100, 100, 100))
        draw.text((size // 2, size // 2), "—", fill="black",
                  font=_fit_font("—", size - 10), anchor="mm")
        return img

    ratio = data.get("remainingFraction", 0.0)
    pct = min(int(ratio * 100), 100)
    
    if pct > 50:
        bg_color = (80, 210, 100)
    elif pct > 20:
        bg_color = (255, 185, 50)
    else:
        bg_color = (240, 70, 60)

    draw.rounded_rectangle([0, 0, size, size], radius=12, fill=bg_color)
    text = str(pct)
    draw.text((size // 2, size // 2), text, fill="black",
              font=_fit_font(text, size - 8), anchor="mm")
    return img

_popup_ref = [None]
_close_popup = [False]

def _fmt_resets_in(reset_at_str: str) -> str:
    if not reset_at_str:
        return "—"
    try:
        reset_at = datetime.fromisoformat(reset_at_str.replace('Z', '+00:00'))
        total_mins = int((reset_at - datetime.now(timezone.utc)).total_seconds() / 60)
        if total_mins <= 0:
            return "まもなくリセット"
        h, m = divmod(total_mins, 60)
        return f"Resets in {h}h {m}m" if h else f"Resets in {m}m"
    except Exception:
        return "—"

def _fmt_updated(fetched_at: datetime) -> str:
    if not fetched_at:
        return ""
    mins = int((datetime.now(timezone.utc) - fetched_at).total_seconds() / 60)
    return f"Updated {mins}m ago"

def show_popup():
    if _popup_ref[0] is not None:
        _close_popup[0] = True
        return

    _close_popup[0] = False
    with state.lock:
        models_data = dict(state.models_data)
        fetched_at = state.last_fetch_time

    win = tk.Tk()
    win.title("")
    win.configure(bg=BG)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.overrideredirect(True)

    content = tk.Frame(win, bg=BG)
    content.pack()

    header = tk.Frame(content, bg=BG)
    header.pack(fill="x", padx=16, pady=(14, 10))
    tk.Label(header, text="Model Quota", bg=BG, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(side="left")
    tk.Label(header, text=_fmt_updated(fetched_at), bg=BG, fg=TEXT_DIM, font=("Segoe UI", 8)).pack(side="right")
    tk.Frame(content, bg=BAR_BG, height=1).pack(fill="x", padx=16, pady=(0, 10))

    if not models_data:
        tk.Label(content, text="Waiting for data or Antigravity not running...", bg=BG, fg=TEXT_DIM, font=("Segoe UI", 9)).pack(padx=16, pady=20)
    else:
        sorted_models = sorted(models_data.items(), key=lambda x: x[0])
        for model_name, data in sorted_models[:8]:
            ratio = data.get("remainingFraction", 0.0)
            row = tk.Frame(content, bg=BG)
            row.pack(fill="x", padx=16, pady=(0, 2))
            
            tk.Label(row, text=model_name, bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=_fmt_resets_in(data.get("resetTime")), bg=BG, fg=TEXT_DIM, font=("Segoe UI", 8)).pack(side="right")
            
            bar_frame = tk.Frame(content, bg=BG)
            bar_frame.pack(fill="x", padx=16, pady=(2, 0))
            canvas = tk.Canvas(bar_frame, height=6, bg=BG, highlightthickness=0)
            canvas.pack(fill="x")
            
            def make_draw(c, r):
                def _draw(e=None):
                    w = c.winfo_width()
                    if w < 2: return
                    c.delete("all")
                    c.create_rectangle(0, 0, w, 6, fill=BAR_BG, outline="")
                    fw = int(w * min(r, 1.0))
                    if fw > 0:
                        color = "#50d264" if r > 0.5 else ("#ffb932" if r > 0.2 else "#f0463c")
                        c.create_rectangle(0, 0, fw, 6, fill=color, outline="")
                return _draw
            
            draw_fn = make_draw(canvas, ratio)
            canvas.bind("<Configure>", draw_fn)
            canvas.after(50, draw_fn)
            
            tk.Label(content, text=f"{int(ratio * 100)}%", bg=BG, fg=TEXT_DIM, font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(2, 10))

    tk.Frame(content, bg=BAR_BG, height=1).pack(fill="x", padx=16, pady=(8, 8))
    tk.Button(content, text="✕ Close", command=win.destroy,
              bg=BG, fg=TEXT_DIM, relief="flat", font=("Segoe UI", 8),
              activebackground=BG_CARD, cursor="hand2", bd=0).pack(pady=(0, 10))

    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry(f"{w}x{h}+{sw - w - 16}+{sh - h - 56}")

    def _check_close():
        if _close_popup[0]:
            win.destroy()
            return
        win.after(100, _check_close)

    win.bind("<Escape>", lambda e: win.destroy())
    win.after(100, _check_close)
    _popup_ref[0] = win
    win.mainloop()
    _popup_ref[0] = None

# ==========================================
# MAIN EXECUTION
# ==========================================
def run_tray():
    logging.info("Starting pystray icon...")
    tray_icon = None

    def on_quit(icon, item):
        logging.info("Quit requested by user.")
        state.running = False
        icon.stop()

    def set_selected_model(icon, item):
        logging.info(f"User selected model: {item.text}")
        with state.lock:
            state.selected_model_name = item.text
            state.save_config()
        update_tray_icon()

    def on_click_default(icon, item):
        logging.debug("User clicked default action (show popup).")
        threading.Thread(target=show_popup, daemon=True).start()

    def build_menu():
        with state.lock:
            models = list(state.models_data.keys())
            sel = state.selected_model_name
            
        menu_items = [item("詳細を表示 (Details)", on_click_default, default=True), pystray.Menu.SEPARATOR]
        for m in sorted(models):
            menu_items.append(item(m, set_selected_model, checked=lambda item, m=m, s=sel: item.text == s, radio=True))
            
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(item("終了 (Quit)", on_quit))
        return pystray.Menu(*menu_items)

    def update_tray_icon():
        if not tray_icon: return
        tray_icon.icon = make_icon()
        
        with state.lock:
            sel = state.selected_model_name
            data = state.models_data.get(sel, {})
            pct = int(data.get("remainingFraction", 0.0) * 100)
            time_str = _fmt_resets_in(data.get("resetTime", ""))
            
        if not sel:
            tray_icon.title = "Antigravity Quota (Waiting...)"
        else:
            tray_icon.title = f"[{sel}] {pct}%\n{time_str}"
            
        tray_icon.menu = build_menu()

    def tray_update_loop():
        while state.running:
            try:
                update_tray_icon()
            except Exception as e:
                logging.error(f"Error in tray update loop: {e}")
            time.sleep(5)

    tray_icon = pystray.Icon("ag_usage", make_icon(), title="Antigravity Quota", menu=build_menu())
    
    threading.Thread(target=backend_worker, daemon=True).start()
    threading.Thread(target=tray_update_loop, daemon=True).start()
    
    tray_icon.run()

if __name__ == '__main__':
    if "--test-backend" in sys.argv:
        test_backend_mode()
        sys.exit(0)

    # Single instance check
    import ctypes
    _MUTEX_NAME = "Global\\AntigravityUsageMonitorMutex"
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:
        logging.warning("Another instance is already running. Exiting.")
        sys.exit(0)
        
    try:
        run_tray()
    except Exception as e:
        logging.critical(f"Fatal error in main loop: {e}", exc_info=True)
