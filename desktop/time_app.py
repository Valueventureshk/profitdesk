"""EcomVital Time: the desktop app staff run while working (Mac + Windows). Like Hubstaff.

While the person has pressed Start (and not Pause) it sends EcomVital, every minute, whether the keyboard / mouse was
used that minute (counts only, never what was typed) and which app + window title was in front; and once in every
10-minute block, at a random moment, a screenshot of all screens. Nothing is recorded while stopped or paused.
Offline: minutes and screenshots wait on this computer and are sent when the connection is back.

Built into an app with PyInstaller (see .github/workflows/time-app.yml). Run from source: python time_app.py
"""
import io
import json
import os
import platform
import queue
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from collections import Counter
from datetime import datetime, timezone

APP_NAME = "EcomVital Time"
APP_VERSION = "1.0.0"
SERVER = os.environ.get("ECOMVITAL_SERVER", "https://hq.ecomvital.com").rstrip("/")
SYSTEM = platform.system()                    # Darwin | Windows | Linux


# ---------------------------------------------------------------- where it keeps its things

def data_dir() -> str:
    if SYSTEM == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif SYSTEM == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.path.expanduser("~/.config")
    d = os.path.join(base, APP_NAME)
    os.makedirs(os.path.join(d, "pending"), exist_ok=True)
    return d


DATA = data_dir()
CONFIG = os.path.join(DATA, "config.json")
QUEUE = os.path.join(DATA, "minutes.json")
PENDING = os.path.join(DATA, "pending")


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- talking to EcomVital

class ApiError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def _request(method, path, token=None, body=None, headers=None, timeout=30):
    h = {"User-Agent": f"{APP_NAME}/{APP_VERSION}", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif isinstance(body, bytes):
        data = body
    req = urllib.request.Request(SERVER + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("error") or f"Error {e.code}"
        except ValueError:
            msg = f"Error {e.code}"
        raise ApiError(msg, e.code)
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise ApiError(f"No connection to EcomVital ({type(e).__name__}).", 0)


def post_shot(token, session, meta: dict, jpeg: bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in {**meta, "session": session or ""}.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="shot.jpg"\r\n'
                 f"Content-Type: image/jpeg\r\n\r\n".encode() + jpeg + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return _request("POST", "/api/time/client/shot", token, b"".join(parts),
                    {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=90)


# ---------------------------------------------------------------- what's in front, and screenshots

def front_window():
    """(app name, window title) of the window in front; ('', '') if it can't tell."""
    try:
        if SYSTEM == "Windows":
            import ctypes
            from ctypes import wintypes
            import psutil
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                app = psutil.Process(pid.value).name().rsplit(".exe", 1)[0]
            except Exception:
                app = ""
            return app, buf.value
        if SYSTEM == "Darwin":
            from AppKit import NSWorkspace
            import Quartz
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            app, pid = str(front.localizedName() or ""), front.processIdentifier()
            title = ""
            wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                                                     Quartz.kCGNullWindowID) or []
            for w in wins:
                if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 1) == 0:
                    title = str(w.get("kCGWindowName") or "")
                    break
            return app, title
    except Exception:
        pass
    return "", ""


def screenshot():
    """(JPEG bytes, screens, width, height) of all screens together, or None."""
    try:
        import mss
        from PIL import Image
        grabber = getattr(mss, "MSS", None) or mss.mss
        with grabber() as sct:
            mon = sct.monitors[0]                 # every screen as one picture
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            screens = max(1, len(sct.monitors) - 1)
        if img.width > 2400:
            img = img.resize((2400, int(img.height * 2400 / img.width)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=55, optimize=True)
        return buf.getvalue(), screens, img.width, img.height
    except Exception:
        return None


# ---------------------------------------------------------------- the tracker (runs in the background)

class Tracker:
    def __init__(self, on_change):
        self.on_change = on_change              # called (from any thread) when the UI should refresh
        cfg = load_json(CONFIG, {})
        self.token, self.name, self.email = cfg.get("token"), cfg.get("name"), cfg.get("email")
        self.running = self.paused = False
        self.session = None
        self.task_id = None
        self.settings = {"block_minutes": 10, "idle_minutes": 5}
        self.today_minutes = 0
        self.session_started = None
        self.last_shot = None
        self.last_error = ""
        self.lock = threading.Lock()
        self.keys = self.clicks = self.moves = 0
        self.last_input = time.time()
        self.samples = Counter()
        self.queue = load_json(QUEUE, [])
        self.next_shot = None
        self.idle_from = None                   # when the current idle stretch began (for the "you were idle" question)
        self.idle_events = queue.Queue()
        self._listeners_started = False

    # -- sign in / out
    def sign_in(self, email, password):
        r = _request("POST", "/api/time/client/login", body={"email": email, "password": password,
                                                            "device": socket.gethostname(), "os": f"{SYSTEM} {platform.release()}",
                                                            "version": APP_VERSION})
        self.token, self.name, self.email = r["token"], r.get("name"), r.get("email")
        save_json(CONFIG, {"token": self.token, "name": self.name, "email": self.email})
        self.refresh_config()

    def sign_out(self):
        self.stop()
        self.token = None
        save_json(CONFIG, {})

    def refresh_config(self):
        try:
            c = _request("GET", "/api/time/client/config", self.token)
            self.settings.update({k: c[k] for k in ("block_minutes", "idle_minutes") if k in c})
            self.today_minutes = (c.get("today") or {}).get("minutes", 0)
            self.tasks = c.get("tasks") or []
            self.latest = c.get("latest") or ""
            self.name = c.get("name") or self.name
            self.last_error = ""
            return c
        except ApiError as e:
            if e.status == 401:
                self.token = None
                save_json(CONFIG, {})
            self.last_error = str(e)
            return None

    # -- start / pause / stop
    def start(self):
        if not self._listeners_started:
            self._start_listeners()
        try:
            self.session = _request("POST", "/api/time/client/start", self.token, {"at": utc_now().isoformat()})["session"]
        except ApiError as e:
            self.session = None
            self.last_error = str(e) + " Tracking anyway; it's sent when the connection is back."
        self.running, self.paused = True, False
        self.session_started = time.time()
        self.last_input = time.time()
        self._plan_shot()
        self.on_change()

    def pause(self):
        self.paused = not self.paused
        if not self.paused:
            self.last_input = time.time()
            self._plan_shot()
        self.on_change()

    def stop(self):
        if self.running and self.session:
            try:
                _request("POST", "/api/time/client/stop", self.token, {"session": self.session})
            except ApiError:
                pass
        self.running = self.paused = False
        self.flush()
        self.on_change()

    # -- input (counts only)
    def _start_listeners(self):
        self._listeners_started = True
        try:
            from pynput import keyboard, mouse

            def bump(kind):
                with self.lock:
                    setattr(self, kind, getattr(self, kind) + 1)
                    self.last_input = time.time()
            last_move = [0.0]

            def on_move(x, y):
                t = time.time()
                if t - last_move[0] > 0.5:       # moves come in floods: one count per half second is plenty
                    last_move[0] = t
                    bump("moves")
            keyboard.Listener(on_press=lambda k: bump("keys"), daemon=True).start()
            mouse.Listener(on_move=on_move, on_click=lambda *a: bump("clicks") if a[-1] else None,
                           on_scroll=lambda *a: bump("moves"), daemon=True).start()
        except Exception as e:
            self.last_error = f"Can't watch the keyboard / mouse ({type(e).__name__}): allow it in the computer's privacy settings."

    # -- the loops
    def run_forever(self):
        threading.Thread(target=self._sampler, daemon=True).start()
        threading.Thread(target=self._minute_loop, daemon=True).start()
        threading.Thread(target=self._shot_loop, daemon=True).start()

    def _sampler(self):
        while True:
            if self.running and not self.paused:
                app, title = front_window()
                with self.lock:
                    self.samples[(app, title)] += 1
            time.sleep(5)

    def _minute_loop(self):
        while True:
            now = time.time()
            time.sleep(60 - now % 60 + 0.2)       # just after each minute ends
            end = datetime.fromtimestamp(time.time() - 1, timezone.utc).replace(second=0, microsecond=0)
            with self.lock:
                keys, clicks, moves, samples = self.keys, self.clicks, self.moves, self.samples
                self.keys = self.clicks = self.moves = 0
                self.samples = Counter()
            if self.running and not self.paused:
                (app, title), _n = samples.most_common(1)[0] if samples else (("", ""), 0)
                active = (keys + clicks + moves) > 0
                self.queue.append({"minute": end.isoformat(), "active": active, "keys": keys, "clicks": clicks, "moves": moves,
                                   "app": app, "title": title, "task_id": self.task_id})
                self.today_minutes += 1
                idle_for = time.time() - self.last_input
                if idle_for >= self.settings["idle_minutes"] * 60 and not self.idle_from:
                    self.idle_from = self.last_input
                elif idle_for < 60 and self.idle_from:
                    start, self.idle_from = self.idle_from, None
                    if time.time() - start >= self.settings["idle_minutes"] * 60:
                        self.idle_events.put(start)  # the window asks: keep or remove the idle time?
                save_json(QUEUE, self.queue[-5000:])
            self.flush()
            self.on_change()

    def flush(self):
        """Send waiting minutes and screenshots."""
        if not self.token:
            return
        if self.queue:
            batch = self.queue[:500]
            try:
                r = _request("POST", "/api/time/client/minutes", self.token, {"session": self.session, "rows": batch})
                self.queue = self.queue[len(batch):]
                save_json(QUEUE, self.queue)
                self.today_minutes = (r.get("today") or {}).get("minutes", self.today_minutes)
                self.last_error = ""
            except ApiError as e:
                self.last_error = str(e)
                if e.status == 401:
                    self.token = None
                return
        for name in sorted(os.listdir(PENDING)):
            if not name.endswith(".jpg"):
                continue
            path = os.path.join(PENDING, name)
            meta = load_json(path[:-4] + ".json", {})
            try:
                with open(path, "rb") as fh:
                    post_shot(self.token, meta.pop("session", self.session), meta, fh.read())
                os.remove(path)
                if os.path.exists(path[:-4] + ".json"):
                    os.remove(path[:-4] + ".json")
            except ApiError as e:
                self.last_error = str(e)
                return
            except OSError:
                pass

    def _plan_shot(self):
        """A random moment inside the current 10-minute block (or the next one if this one's moment has passed)."""
        block = self.settings.get("block_minutes", 10) * 60
        now = time.time()
        start = now - now % block
        at = start + random.uniform(15, block - 15)
        if at <= now + 5:
            at = start + block + random.uniform(15, block - 15)
        self.next_shot = at

    def _shot_loop(self):
        while True:
            time.sleep(2)
            if not (self.running and not self.paused and self.next_shot) or time.time() < self.next_shot:
                continue
            taken = utc_now()
            got = screenshot()
            app, title = front_window()
            self._plan_shot()
            if not got:
                self.last_error = "Couldn't take a screenshot: allow Screen Recording for EcomVital Time in the computer's privacy settings."
                continue
            jpeg, screens, w, h = got
            stamp = taken.strftime("%Y%m%d%H%M%S")
            path = os.path.join(PENDING, f"{stamp}.jpg")
            with open(path, "wb") as fh:
                fh.write(jpeg)
            save_json(path[:-4] + ".json", {"taken_at": taken.isoformat(), "screens": screens, "width": w, "height": h,
                                            "app": app, "title": title[:290], "session": self.session})
            self.last_shot = datetime.now()
            self.flush()
            self.on_change()

    def discard_idle(self, start_ts: float, end_ts: float):
        """The person chose to remove the idle stretch: those minutes (and their screenshots) are taken back."""
        a = datetime.fromtimestamp(start_ts, timezone.utc).isoformat()
        b = datetime.fromtimestamp(end_ts, timezone.utc).isoformat()
        self.flush()
        try:
            r = _request("POST", "/api/time/client/discard", self.token, {"from": a, "to": b})
            self.today_minutes = (r.get("today") or {}).get("minutes", self.today_minutes)
        except ApiError as e:
            self.last_error = str(e)
        self.on_change()


# ---------------------------------------------------------------- the window

def main():
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("380x470")
    root.minsize(340, 430)
    events = queue.Queue()
    tracker = Tracker(lambda: events.put("refresh"))
    tracker.tasks, tracker.latest = [], ""

    style = ttk.Style()
    try:
        style.theme_use("clam" if SYSTEM != "Darwin" else "aqua")
    except tk.TclError:
        pass

    login = ttk.Frame(root, padding=24)
    mainf = ttk.Frame(root, padding=20)

    # sign in
    ttk.Label(login, text=APP_NAME, font=("Helvetica", 18, "bold")).pack(anchor="w")
    ttk.Label(login, text="Sign in with your EcomVital login", foreground="#64748B").pack(anchor="w", pady=(2, 16))
    ttk.Label(login, text="Email").pack(anchor="w")
    email = ttk.Entry(login, width=34)
    email.pack(fill="x", pady=(2, 10))
    ttk.Label(login, text="Password").pack(anchor="w")
    password = ttk.Entry(login, width=34, show="•")
    password.pack(fill="x", pady=(2, 14))
    login_msg = ttk.Label(login, text="", foreground="#B3402A", wraplength=300)

    def do_login(*_):
        login_msg.config(text="Signing in…")
        root.update_idletasks()
        try:
            tracker.sign_in(email.get().strip(), password.get())
            password.delete(0, "end")
            show_main()
        except ApiError as e:
            login_msg.config(text=str(e))
    ttk.Button(login, text="Sign in", command=do_login).pack(fill="x")
    login_msg.pack(anchor="w", pady=10)
    password.bind("<Return>", do_login)
    if SYSTEM == "Darwin":
        ttk.Label(login, text="First time on a Mac: allow Screen Recording and Input Monitoring / Accessibility for "
                  "EcomVital Time in System Settings → Privacy & Security, then reopen it.",
                  foreground="#64748B", wraplength=320, justify="left").pack(anchor="w", pady=(16, 0))

    # main
    hello = ttk.Label(mainf, text="", font=("Helvetica", 13, "bold"))
    hello.pack(anchor="w")
    clock_l = ttk.Label(mainf, text="0:00", font=("Helvetica", 36, "bold"))
    clock_l.pack(anchor="w", pady=(10, 0))
    ttk.Label(mainf, text="worked today", foreground="#64748B").pack(anchor="w")
    status = ttk.Label(mainf, text="", font=("Helvetica", 12))
    status.pack(anchor="w", pady=(10, 4))
    ttk.Label(mainf, text="Working on (optional)").pack(anchor="w", pady=(8, 2))
    task = ttk.Combobox(mainf, state="readonly", width=40)
    task.pack(fill="x")
    btns = ttk.Frame(mainf)
    btns.pack(fill="x", pady=14)
    start_b = ttk.Button(btns, text="Start", command=lambda: tracker.start())
    pause_b = ttk.Button(btns, text="Pause", command=lambda: tracker.pause())
    stop_b = ttk.Button(btns, text="Stop", command=lambda: tracker.stop())
    for b in (start_b, pause_b, stop_b):
        b.pack(side="left", expand=True, fill="x", padx=2)
    shot_l = ttk.Label(mainf, text="", foreground="#64748B")
    shot_l.pack(anchor="w")
    err_l = ttk.Label(mainf, text="", foreground="#B3402A", wraplength=320, justify="left")
    err_l.pack(anchor="w", pady=(6, 0))
    upd_l = ttk.Label(mainf, text="", foreground="#2563EB", cursor="hand2")
    upd_l.pack(anchor="w", pady=(6, 0))
    upd_l.bind("<Button-1>", lambda e: webbrowser.open(SERVER + "/time?tab=app"))
    foot = ttk.Frame(mainf)
    foot.pack(side="bottom", fill="x")
    ttk.Button(foot, text="Open EcomVital", command=lambda: webbrowser.open(SERVER + "/time")).pack(side="left")

    def do_signout():
        if messagebox.askyesno(APP_NAME, "Sign out? Tracking stops."):
            tracker.sign_out()
            show_login()
    ttk.Button(foot, text="Sign out", command=do_signout).pack(side="right")

    def on_task(*_):
        i = task.current()
        tracker.task_id = tracker.tasks[i - 1]["id"] if i > 0 and i - 1 < len(tracker.tasks) else None
    task.bind("<<ComboboxSelected>>", on_task)

    def fill_tasks():
        task["values"] = ["(no task)"] + [f"#{t['id']} {t['title']}" for t in tracker.tasks]
        if task.current() < 0:
            task.current(0)

    def show_login():
        mainf.pack_forget()
        login.pack(fill="both", expand=True)

    def show_main():
        login.pack_forget()
        mainf.pack(fill="both", expand=True)
        fill_tasks()
        refresh()

    def refresh():
        hello.config(text=f"Hi {tracker.name or ''}")
        m = tracker.today_minutes
        clock_l.config(text=f"{m // 60}:{m % 60:02d}")
        if tracker.running and tracker.paused:
            status.config(text="⏸  Paused: nothing recorded", foreground="#B45309")
        elif tracker.running:
            status.config(text="●  Tracking: screenshots and activity on", foreground="#0F9D74")
        else:
            status.config(text="Not tracking", foreground="#64748B")
        start_b.state(["disabled"] if tracker.running else ["!disabled"])
        pause_b.state(["!disabled"] if tracker.running else ["disabled"])
        pause_b.config(text="Resume" if tracker.paused else "Pause")
        stop_b.state(["!disabled"] if tracker.running else ["disabled"])
        shot_l.config(text=f"Last screenshot {tracker.last_shot.strftime('%H:%M')}" if tracker.last_shot else "")
        err_l.config(text=tracker.last_error or "")
        upd_l.config(text=f"A newer version ({tracker.latest}) is out: click to download" if tracker.latest and tracker.latest != APP_VERSION else "")
        root.title(f"{'● ' if tracker.running and not tracker.paused else ''}{APP_NAME}")

    def pump():
        try:
            while True:
                events.get_nowait()
                refresh()
        except queue.Empty:
            pass
        try:
            start = tracker.idle_events.get_nowait()
            mins = int((time.time() - start) // 60)
            keep = messagebox.askyesno(APP_NAME, f"You were away for about {mins} minutes (no keyboard or mouse since "
                                                 f"{datetime.fromtimestamp(start).strftime('%H:%M')}).\n\nKeep that time as worked?\n"
                                                 "Yes = keep it · No = remove it", icon="question")
            if not keep:
                threading.Thread(target=tracker.discard_idle, args=(start, time.time() - 60), daemon=True).start()
        except queue.Empty:
            pass
        root.after(500, pump)

    def on_close():
        if tracker.running:
            if not messagebox.askyesno(APP_NAME, "Stop tracking and close EcomVital Time?"):
                return
            tracker.stop()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    tracker.run_forever()
    if tracker.token and tracker.refresh_config() is not None and tracker.token:
        show_main()
    else:
        show_login()
    root.after(500, pump)

    def every_5min():
        if tracker.token:
            threading.Thread(target=lambda: (tracker.refresh_config(), events.put("refresh")), daemon=True).start()
        root.after(300000, every_5min)
    root.after(300000, every_5min)
    root.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:                 # used by the build: the app starts, takes one picture, and exits
        print(json.dumps({"version": APP_VERSION, "front": front_window(), "shot": bool(screenshot())}))
        sys.exit(0)
    main()
