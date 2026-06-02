"""
Token Usage Bar - a tiny always-on-top floating window that shows how much of
your Claude usage limits you've consumed (same numbers as Claude Code's /usage).

Unofficial; not affiliated with or endorsed by Anthropic.

Data source : https://api.anthropic.com/api/oauth/usage  (your OAuth token)
Token store : ~/.claude/.credentials.json  (written by Claude Code)
Deps        : pystray + pillow (tray icon); rest is pure stdlib + tkinter.

Controls:
  - Tray icon (bottom-right of taskbar): left-click = show/hide,
                                          right-click = menu (Refresh / Quit)
  - Drag the window anywhere to move (position is remembered)
  - The window auto-dims when the mouse isn't over it
  - Double-click the window -> hide to tray
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.error
import webbrowser
import tkinter as tk
from tkinter import font as tkfont

try:
    import ctypes
except Exception:          # non-Windows / no ctypes -> features degrade gracefully
    ctypes = None

try:
    import winreg          # for the "Start with Windows" toggle (HKCU Run key)
except ImportError:
    winreg = None

import pystray
from PIL import Image, ImageDraw

HOME = os.path.expanduser("~")
CRED_PATH = os.path.join(HOME, ".claude", ".credentials.json")

APP_NAME = "Token Usage Bar"       # display name (window / tray / dialogs)
APP_SLUG = "TokenUsageBar"         # filesystem / mutex / identifier-safe name
VERSION = "1.0.11"
REPO = "vietnnh-mialala/token-usage-bar"   # GitHub owner/repo for update checks
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"   # HKCU autostart

# Where to persist the saved window position. As a script that's next to the
# source; as a packaged (PyInstaller) exe, __file__ points at a temp extraction
# dir that's wiped each run, so use a stable per-user folder instead.
if getattr(sys, "frozen", False):
    _STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", HOME), APP_SLUG)
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
    except OSError:
        _STATE_DIR = HOME
else:
    _STATE_DIR = os.path.dirname(os.path.abspath(__file__))
POS_PATH = os.path.join(_STATE_DIR, ".window_pos.json")


def _version_tuple(s):
    """'1.2.3' -> (1, 2, 3); non-numeric parts become 0 (for safe comparison)."""
    out = []
    for p in str(s).split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code public client id
BETA_HEADER = "oauth-2025-04-20"

# Adaptive polling: the usage numbers only move when you actually use Claude,
# and Anthropic publishes no rate limit for this internal endpoint, so the
# "most continuous yet polite" strategy is to poll fast while the numbers are
# changing and back off automatically when nothing changes / the widget is idle.
FAST_SECONDS = 30          # floor: when usage is actively changing
SLOW_SECONDS = 300         # ceiling: when nothing has changed for a while
HIDDEN_SECONDS = 300       # when the widget is hidden to the tray
RL_BACKOFF_MAX = 300       # cap on rate-limit back-off so the dot recovers fast
FRESH_SECONDS = 60         # dot is green while the last good sync is this recent
TIMEOUT = 15
REFRESH_GRACE_SECONDS = 8  # wait this long for Claude Code to refresh the shared
                           # token before the widget refreshes it itself
HOVER_ALPHA = 0.95         # opacity when the mouse is over the window
IDLE_ALPHA = 0.22          # idle opacity when floating over the desktop
DOCK_IDLE_ALPHA = 0.6      # idle opacity when docked — higher so the dark bar
                           # stays legible against the dark taskbar (22% vanishes)

# Sit *on* the taskbar (in its empty left area) by re-parenting the bar as a
# child of Shell_TrayWnd. This is the only reliable way to render on top of the
# Windows 11 taskbar, which otherwise wins the z-order. Set False to float.
DOCK_TO_TASKBAR = True

# ---------------------------------------------------------------- credentials


BACKUP_PATH = CRED_PATH + ".bak"   # last-known-good snapshot (written by us)


def _read_creds():
    """Load the credentials, falling back to our last-known-good backup if the
    main file is briefly unreadable (e.g. a sharing violation while Claude Code
    is mid-replace) or corrupt. The fallback is READ-ONLY — we never restore it
    over the main file, so we can't clobber a valid update we just lost the race
    to read."""
    try:
        with open(CRED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        with open(BACKUP_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def _write_creds(data):
    """Durable, atomic write that can never leave a half-written or clobbered
    credentials file:
      - snapshot the current good file to .bak first (recovery point)
      - write to a PID-unique temp so concurrent writers never share a temp
      - flush + fsync so a crash/power-loss can't leave a truncated file
      - os.replace (atomic on Windows) so readers see only the old or new file
    The widget writes here ONLY as a last resort (see _refresh_token), so this
    path is rarely hit and never races Claude Code's normal refresh."""
    try:
        if os.path.exists(CRED_PATH):
            shutil.copy2(CRED_PATH, BACKUP_PATH)
    except OSError:
        pass                       # backup is best-effort, never block the write
    tmp = f"{CRED_PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CRED_PATH)


def _expires_at_ms(oauth):
    v = oauth.get("expiresAt")
    if v is None:
        return 0
    v = int(v)
    return v if v > 1_000_000_000_000 else v * 1000


def _token_from_file():
    """Return (accessToken, expiresAt_ms) from disk, or (None, 0) on any error."""
    try:
        o = _read_creds()["claudeAiOauth"]
        return o.get("accessToken"), _expires_at_ms(o)
    except Exception:
        return None, 0


def _refresh_token(prev_access=None):
    """Exchange the refresh token for a new access token and persist it.

    Race-aware + defers to Claude Code: the widget shares ONE rotating refresh
    token with Claude Code, and rotating it invalidates the old one. Claude Code
    is the *primary* refresher, so we let it win:
      1. if the disk already holds a fresh, different access token, use it;
      2. otherwise wait a short, PID-staggered grace period and re-check — this
         lets Claude Code's refresh land first and avoids two widget polls hitting
         the endpoint in lockstep;
      3. only if nobody refreshed it do we refresh ourselves (last resort).
    This makes the original 403 (both refreshing at once, loser fails) essentially
    impossible in normal use, where Claude Code keeps the token fresh."""
    def _fresh_disk_token():
        a, exp = _token_from_file()
        if a and a != prev_access and exp - int(time.time() * 1000) > 120_000:
            return a
        return None

    hit = _fresh_disk_token()
    if hit:
        return hit                    # (1) someone already refreshed -> no race
    # (2) give Claude Code a head start (runs on a worker thread, never the UI),
    # staggered by PID so concurrent instances don't re-check in lockstep
    time.sleep(REFRESH_GRACE_SECONDS + (os.getpid() % 5))
    hit = _fresh_disk_token()
    if hit:
        return hit

    # (3) last resort: token still stale and nobody fixed it (Claude Code likely
    # not running) -> refresh ourselves, reading the latest refresh token from disk
    o = _read_creds()["claudeAiOauth"]
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": o["refreshToken"],   # latest token from disk
        "client_id": CLIENT_ID,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            tok = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # the token endpoint rejecting the grant means the refresh token itself
        # is dead (revoked / rotated away) -> a fresh sign-in is the only fix
        if e.code in (400, 401, 403):
            raise NeedsLogin() from e
        raise

    data = _read_creds()
    o = data["claudeAiOauth"]
    o["accessToken"] = tok["access_token"]
    if tok.get("refresh_token"):
        o["refreshToken"] = tok["refresh_token"]
    if tok.get("expires_in"):
        o["expiresAt"] = int(time.time() * 1000) + int(tok["expires_in"]) * 1000
    _write_creds(data)
    return o["accessToken"]


def _get_access_token():
    """Return the on-disk access token without proactively refreshing.

    Claude Code owns the token lifecycle and refreshes it before expiry, so the
    widget just reads. If the token is nonetheless stale, the usage call returns
    401/403 and fetch_usage() refreshes reactively — and only as a last resort
    (see _refresh_token). Not refreshing here removes the pre-emptive refresh that
    used to race Claude Code around expiry."""
    return _read_creds()["claudeAiOauth"]["accessToken"]


class RateLimited(Exception):
    """Raised on HTTP 429; carries the suggested wait time in seconds."""
    def __init__(self, retry_after):
        super().__init__("rate limited")
        self.retry_after = retry_after


class NeedsLogin(Exception):
    """Raised when the stored refresh token is no longer valid, so the only way
    forward is a fresh sign-in (the widget cannot do OAuth itself)."""


def _friendly_error(e):
    """Turn a fetch_usage exception into a short, human-readable status line for
    the tooltip — never the raw 'The read operation timed out' / stack noise."""
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return "sign-in expired — open Claude Code, then Refresh"
        if e.code == 429:
            return "rate limited — waiting to retry"
        if 500 <= e.code < 600:
            return f"server busy ({e.code}) — retrying"
        return f"server error {e.code} — retrying"
    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
        return "network slow — retrying"
    if isinstance(e, urllib.error.URLError):
        return "no connection — retrying"
    if isinstance(e, KeyError):
        return "credentials incomplete — re-login Claude Code"
    if isinstance(e, json.JSONDecodeError):
        return "unexpected response — retrying"
    return (str(e) or e.__class__.__name__)[:60]


def _sha256_file(path):
    """Streaming SHA-256 of a file, lowercase hex."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- usage fetch


def _fetch_usage_once(token):
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def fetch_usage():
    """Return (five_hour_pct, seven_day_pct, reset_iso) or raises."""
    token = _get_access_token()
    try:
        data = _fetch_usage_once(token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            try:
                token = _refresh_token(prev_access=token)
            except Exception:
                # our own refresh failed (e.g. Claude Code already rotated the
                # shared refresh token). Re-read the file once — Claude Code may
                # have just written a fresh token — and try that before giving up,
                # so we recover in this same poll instead of waiting out a backoff.
                disk_access, _ = _token_from_file()
                if not disk_access or disk_access == token:
                    raise
                token = disk_access
            data = _fetch_usage_once(token)
        elif e.code == 429:
            ra = e.headers.get("Retry-After") if e.headers else None
            try:
                ra = int(ra)
            except (TypeError, ValueError):
                ra = None
            raise RateLimited(ra)
        else:
            raise

    fh = (data.get("five_hour") or {}).get("utilization") or 0.0
    sd = (data.get("seven_day") or {}).get("utilization") or 0.0
    reset = (data.get("five_hour") or {}).get("resets_at") or ""
    return float(fh), float(sd), reset


def _reset_compact(iso):
    """Time left until the 5h limit resets as 'h:mm:ss' (or 'reset' once due)."""
    if not iso:
        return "—"
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(iso)
        secs = int((t - datetime.now(timezone.utc)).total_seconds())
        if secs <= 0:
            return "reset"
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f"{h}:{m:02d}:{s:02d}"
    except Exception:
        return "—"


# ---------------------------------------------------------------- colors / icon

# Glass / minimal palette (neon accent)
BG = "#0e1422"        # translucent glass navy
BORDER = "#26334d"    # subtle hairline border
FG = "#e8edf6"
SUB = "#6c7a93"       # muted labels
BAR_BG = "#1b2334"    # bar track

DOT_LIVE = "#21e6c1"  # green-teal: synced within FRESH_SECONDS
DOT_WARN = "#ffb020"  # amber: no successful sync for over FRESH_SECONDS
DOT_IDLE = SUB        # grey: not synced yet (startup)
DOT_ERR  = "#ff5466"  # red: sign-in needed (refresh token dead) -> action required


def bar_color(pct):
    if pct >= 85:
        return "#ff3b6b"   # neon red
    if pct >= 60:
        return "#ffb020"   # neon amber
    return "#21e6c1"       # neon teal


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_tray_image(fh, sd):
    """A 64x64 icon: two vertical bars (5h, weekly) filled by usage %."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=14,
                        fill=(14, 20, 34, 255))
    for pct, x in ((fh, 16), (sd, 38)):
        col = _hex_rgb(bar_color(pct)) + (255,)
        full_h = size - 24
        h = int(full_h * min(pct, 100) / 100)
        # track
        d.rounded_rectangle([x, 12, x + 10, size - 12], radius=4,
                            fill=(27, 35, 52, 255))
        if h > 0:
            d.rounded_rectangle([x, size - 12 - h, x + 10, size - 12],
                                radius=4, fill=col)
    return img


# ---------------------------------------------------------------- platform

_SINGLE_INSTANCE_MUTEX = None  # kept alive for the whole process lifetime


def acquire_single_instance(wait_seconds=0):
    """Return True if we are the only instance, False if one is already running.

    Uses a named Windows mutex (per-user session). On any platform without
    ctypes this is a no-op that always returns True.

    wait_seconds > 0 retries until the existing instance exits — used right after
    a self-update relaunch, where the old exe is still shutting down and would
    otherwise make the freshly-installed copy think a duplicate is running.
    """
    global _SINGLE_INSTANCE_MUTEX
    if ctypes is None or not hasattr(ctypes, "windll"):
        return True
    ERROR_ALREADY_EXISTS = 183
    k32 = ctypes.windll.kernel32
    deadline = time.time() + max(0, wait_seconds)
    while True:
        mutex = k32.CreateMutexW(None, False, APP_SLUG + "_singleton")
        if k32.GetLastError() != ERROR_ALREADY_EXISTS:
            _SINGLE_INSTANCE_MUTEX = mutex
            return True
        if mutex:
            k32.CloseHandle(mutex)         # release the duplicate handle
        if time.time() >= deadline:
            return False
        time.sleep(0.3)


def is_session_locked():
    """True when the workstation is locked (secure desktop is active).

    When locked, the user process can no longer open the *input* desktop, so
    OpenInputDesktop fails - a reliable, notification-free lock probe.
    """
    if ctypes is None or not hasattr(ctypes, "windll"):
        return False
    try:
        DESKTOP_READOBJECTS = 0x0001
        user32 = ctypes.windll.user32
        h = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not h:
            return True
        user32.CloseDesktop(h)
        return False
    except Exception:
        return False


def enable_dpi_awareness():
    """Tell Windows we paint at native resolution, so it stops bitmap-stretching
    (and blurring) the window on a scaled display. Must run before Tk() exists."""
    if ctypes is None or not hasattr(ctypes, "windll"):
        return
    try:
        # PROCESS_SYSTEM_DPI_AWARE (1) — crisp at the system scale factor
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# SetWindowPos / ShowWindow flags for re-asserting the bar's topmost z-order
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SW_SHOWNA = 8         # show in current state without activating (re-raises z)
GA_ROOT = 2


def assert_topmost(hwnd):
    """Re-raise the bar above the Windows 11 taskbar, without stealing focus.

    The Win11 taskbar is a DirectComposition surface that, after a shell event
    (Start menu, a click), can end up painted *over* our topmost overlay — and
    SetWindowPos(HWND_TOPMOST) does NOT bring it back (verified). ShowWindow with
    SW_SHOWNA (show, no-activate) re-inserts the window at the top of the z-order
    and DOES recover it, with no flicker and no focus theft. We then re-stamp the
    topmost flag so the state stays consistent."""
    if ctypes is None or not hasattr(ctypes, "windll") or not hwnd:
        return
    try:
        u = ctypes.windll.user32
        u.ShowWindow(hwnd, SW_SHOWNA)
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def work_area():
    """Primary-monitor work area (left, top, right, bottom) excluding the
    taskbar, or None if unavailable. Used to keep the window on-screen."""
    if ctypes is None or not hasattr(ctypes, "windll"):
        return None
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        r = RECT()
        SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- UI


class TokenBar:
    def __init__(self, root):
        self.root = root
        self.icon = None          # set by main() after creation
        self.hidden = False
        self._last = (0.0, 0.0)
        self._fetching = False    # guard against overlapping fetches
        self._alpha = HOVER_ALPHA  # current window opacity (avoid redundant sets)
        self._tray_pct = None     # last (fh,sd) drawn into the tray icon
        self._last_ok = None      # time.monotonic() of the last successful sync
        self._dot_color = None    # current dot fill (avoid redundant redraws)
        self._needs_login = False  # True when the refresh token is dead (sign in)
        self._docked = False      # True while overlaying the taskbar
        self._dock_screen_y = 0   # screen y (centred in the taskbar band) when docked
        self._update_ver = None   # set when a newer release is found on GitHub

        # scale factor for the current display (1.0 @ 96dpi, 1.25 @ 120dpi …),
        # so pixel-sized graphics keep their physical size now that we paint
        # at native resolution instead of being stretched by Windows
        try:
            dpi = root.winfo_fpixels("1i")
            root.tk.call("tk", "scaling", dpi / 72.0)   # crisp point-sized fonts
        except tk.TclError:
            dpi = 96.0
        self.scale = max(1.0, dpi / 96.0)

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", HOVER_ALPHA)
        except tk.TclError:
            pass
        root.configure(bg=BORDER)

        self._load_pos()

        # 1px border frame -> glass panel inside
        border = tk.Frame(root, bg=BORDER)
        border.pack(fill="both", expand=True, padx=1, pady=1)
        pad = tk.Frame(border, bg=BG, padx=self._s(10), pady=self._s(6))
        pad.pack(fill="both", expand=True)

        # one monospace face (Consolas) for everything — the countdown look the
        # user likes; labels/countdown regular, usage numbers bold for emphasis
        label_font = tkfont.Font(family="Consolas", size=9)
        num_font = tkfont.Font(family="Consolas", size=9, weight="bold")
        count_font = tkfont.Font(family="Consolas", size=9)
        # one common line height -> every element is this tall and pack() centres
        # them all on the same line (fixes the "some high, some low" stagger)
        self._line_h = num_font.metrics("linespace")

        # single compact line:  ● ⟳5h-left · 5h ●●●○○ NN · 7d ●○○○○ NN
        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x")

        # live indicator dot — green when fetch ok, amber/red/grey otherwise
        dd = self._s(8)                                  # dot diameter
        cw = dd + self._s(2)                             # +margin so it isn't clipped
        cx, cy = cw / 2, self._line_h / 2
        self.dot = tk.Canvas(row, width=cw, height=self._line_h, bg=BG,
                             highlightthickness=0)
        self.dot.pack(side="left", padx=(0, self._s(6)))
        self._dot_id = self.dot.create_oval(cx - dd / 2, cy - dd / 2,
                                            cx + dd / 2, cy + dd / 2,
                                            fill=DOT_IDLE, width=0)

        # primary counter: h:mm:ss left until the 5h limit resets (ticks locally)
        self.reset_lbl = tk.Label(row, text="⟳ —", fg=FG, bg=BG,
                                  font=count_font)
        self.reset_lbl.pack(side="left", padx=(0, self._s(10)))

        self._dot_n = 10                       # segments in the dot meter
        self._dot_r = self.scale * 1.6         # small segment radius
        self._dot_gap = self.scale * 5.0       # tight centre-to-centre spacing
        self._dot_pad = self.scale * 1.0       # edge margin so end dots aren't clipped
        meter_w = int(round((self._dot_n - 1) * self._dot_gap
                            + 2 * self._dot_r + 2 * self._dot_pad))
        self.rows = {}
        for key, label in (("5h", "5H"), ("7d", "7D")):
            tk.Label(row, text=label, fg=SUB, bg=BG,
                     font=label_font).pack(side="left", padx=(0, self._s(4)))
            canvas = tk.Canvas(row, width=meter_w, height=self._line_h,
                               bg=BG, highlightthickness=0)
            canvas.pack(side="left", padx=(0, self._s(6)))
            self._draw_dots(canvas, 0)      # dim track until the first sync lands
            val = tk.Label(row, text="--", fg=FG, bg=BG, font=num_font,
                           width=3, anchor="center")
            val.pack(side="left", padx=(0, self._s(9)))
            self.rows[key] = (val, canvas)

        for w in (root, border, pad, row):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Double-Button-1>", lambda e: self.hide())
            w.bind("<Button-3>", self._menu)

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label=f"{APP_NAME} v{VERSION}", state="disabled")
        self.menu.add_separator()
        self.menu.add_command(label="Refresh now", command=self.refresh_async)
        self.menu.add_command(label="Dock / Undock taskbar",
                              command=self.toggle_dock)
        self.menu.add_command(label="Hide to tray", command=self.hide)
        self._autostart_var = tk.BooleanVar(value=self._autostart_enabled())
        self.menu.add_checkbutton(
            label="Start with Windows", variable=self._autostart_var,
            command=lambda: self._set_autostart(self._autostart_var.get()))
        self.menu.add_command(label="🔑 Sign in to Claude…",
                              command=self._sign_in)
        self.menu.add_command(label="Check for updates…",
                              command=self._open_releases)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.quit)

        self._moved = False
        self._dragging = False
        self._timer = None
        self._backoff = FAST_SECONDS
        self._interval = FAST_SECONDS
        self._prev = None
        self._reset_iso = None      # last known 5h reset time (absolute)
        self.root.update_idletasks()
        self._clamp_pos()           # never restore off-screen / behind taskbar
        if DOCK_TO_TASKBAR:
            # defer until the window is fully realized/mapped before positioning
            self.root.after(200, self._dock)
        # first run of the packaged exe -> default to launching at login
        if getattr(sys, "frozen", False) and winreg is not None:
            marker = os.path.join(_STATE_DIR, ".autostart_init")
            if not os.path.exists(marker):
                self._set_autostart(True)
                try:
                    open(marker, "w").close()
                except OSError:
                    pass

        self.refresh_async()
        self._dim_tick()
        self._tick_clock()          # 1 s heartbeat: countdown + freshness + topmost
        self.root.after(3000, self._check_update)   # one-shot GitHub update check

    def _s(self, px):
        """Scale a pixel measurement for the current display DPI."""
        return int(round(px * self.scale))

    def _hwnd(self):
        """Native top-level window handle, or None off-Windows / before map."""
        if ctypes is None or not hasattr(ctypes, "windll"):
            return None
        try:
            h = self.root.winfo_id()
            return ctypes.windll.user32.GetAncestor(h, GA_ROOT) or h
        except Exception:
            return None

    # ---- dock onto the taskbar (topmost overlay over its empty area) ------
    # NB: re-parenting into Shell_TrayWnd is *invisible* on Windows 11 — the
    # taskbar is a DirectComposition surface that paints over child HWNDs. A
    # plain topmost window placed over the taskbar's empty area renders fine;
    # _tick_clock keeps re-asserting topmost so it recovers within ~1s if the
    # shell (Start menu, a click) briefly covers it.
    def _taskbar_band(self):
        """(top, height) in screen px of the taskbar strip below the work area,
        or None if it can't be determined."""
        wa = work_area()
        if not wa:
            return None
        top = wa[3]                       # work-area bottom == top of the taskbar
        height = self.root.winfo_screenheight() - top
        return (top, height) if height > 0 else None

    def _dock(self):
        """Sit as a topmost overlay over the taskbar's empty left area."""
        band = self._taskbar_band()
        if band is None:
            return
        tb_top, tb_h = band
        self.root.update_idletasks()
        h = max(self.root.winfo_height(), self.root.winfo_reqheight())
        self._dock_screen_y = tb_top + max(0, (tb_h - h) // 2)   # centre in band
        self._docked = True
        try:
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", HOVER_ALPHA)   # _dim_tick fades when idle
            self._alpha = HOVER_ALPHA
        except tk.TclError:
            pass
        self.root.geometry(f"+{int(self._dock_x)}+{int(self._dock_screen_y)}")
        assert_topmost(self._hwnd())

    def _undock(self):
        """Return to a floating topmost window above the taskbar."""
        self._docked = False
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.root.geometry(f"+{self._fx}+{self._fy}")
        self.root.update_idletasks()
        self._clamp_pos()
        assert_topmost(self._hwnd())

    def toggle_dock(self):
        self.root.after(0, self._undock if self._docked else self._dock)

    # ---- update check (GitHub Releases) -----------------------------------
    def _open_releases(self):
        # Open the releases page in the default browser. Use os.startfile
        # (ShellExecute) first: webbrowser.open() can hard-crash a --noconsole
        # PyInstaller build (native abort 0xc0000409 in ucrtbase, which a Python
        # try/except cannot catch), taking the whole widget down.
        url = f"https://github.com/{REPO}/releases/latest"
        try:
            os.startfile(url)              # Windows-native, safe in no-console builds
        except Exception:
            try:
                webbrowser.open(url)       # fallback for non-Windows / odd setups
            except Exception:
                pass

    # ---- one-click self-update (download + verify + swap + relaunch) ------
    def _do_update(self):
        """Triggered by the 'Update available' link. Download the newer exe and
        swap it in. Falls back to just opening the page when not runnable as a
        self-update (running from source, or no known newer version)."""
        if not getattr(sys, "frozen", False) or not self._update_ver:
            self._open_releases()
            return
        if getattr(self, "_updating", False):
            return                          # already in progress
        self._updating = True
        threading.Thread(target=self._update_install_worker, daemon=True).start()

    def _fetch_text(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": APP_SLUG})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")

    def _update_install_worker(self):
        tag = self._update_ver
        cur = sys.executable                       # ...\TokenUsageBar.exe
        exe_dir = os.path.dirname(cur)
        new_tmp = os.path.join(exe_dir, APP_SLUG + ".new.exe")
        old = os.path.join(exe_dir, APP_SLUG + ".old.exe")
        base = f"https://github.com/{REPO}/releases/download/v{tag}"
        try:
            self.root.after(0, self._error, f"downloading v{tag}…")
            # 1. download the new exe to a temp file in the same folder
            req = urllib.request.Request(f"{base}/{APP_SLUG}.exe",
                                         headers={"User-Agent": APP_SLUG})
            with urllib.request.urlopen(req, timeout=120) as r, \
                    open(new_tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            # 2. verify the published SHA-256 before trusting the binary
            self.root.after(0, self._error, f"verifying v{tag}…")
            want = self._fetch_text(f"{base}/{APP_SLUG}.exe.sha256").split()[0].strip().lower()
            got = _sha256_file(new_tmp).lower()
            if want and got != want:
                raise ValueError("checksum mismatch — aborting update")
            # 3. swap: rename the running exe aside (Windows allows this), then
            #    move the verified new exe into its place
            if os.path.exists(old):
                try:
                    os.remove(old)
                except OSError:
                    pass
            os.replace(cur, old)
            os.replace(new_tmp, cur)
            # 4. relaunch the new exe (it waits for us to exit via --updated),
            #    then quit this instance
            # A PyInstaller --onefile process exports _PYI_PARENT_PROCESS_LEVEL /
            # _PYI_APPLICATION_HOME_DIR / _PYI_ARCHIVE_FILE (and on older
            # versions _MEIPASS2 / TCL_LIBRARY). A spawned child inherits them,
            # so its bootloader REUSES our about-to-be-deleted _MEI dir instead
            # of extracting its own -> "Can't find a usable init.tcl" crash.
            # Strip every PyInstaller/Tcl marker so the new exe starts clean.
            env = os.environ.copy()
            for k in list(env):
                if (k.startswith("_PYI") or k.startswith("_MEIPASS")
                        or k in ("TCL_LIBRARY", "TK_LIBRARY", "TKPATH")):
                    del env[k]
            subprocess.Popen(
                [cur, "--updated"], env=env,
                creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                               | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                close_fds=True)
            self.root.after(0, self.quit)
        except Exception as e:
            # restore the original exe if we renamed it but didn't finish, and
            # fall back to opening the download page so the user isn't stuck
            try:
                if not os.path.exists(cur) and os.path.exists(old):
                    os.replace(old, cur)
            except OSError:
                pass
            try:
                if os.path.exists(new_tmp):
                    os.remove(new_tmp)
            except OSError:
                pass
            self._updating = False
            self.root.after(0, self._error, "update failed — opening page")
            self.root.after(1200, self._open_releases)

    def _check_update(self):
        """Ask GitHub for the latest release tag in a background thread."""
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": APP_SLUG})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                tag = (json.loads(r.read()).get("tag_name") or "").lstrip("vV")
            if tag and _version_tuple(tag) > _version_tuple(VERSION):
                self.root.after(0, self._on_update_available, tag)
        except Exception:
            pass            # offline / rate-limited / no release yet -> ignore

    def _on_update_available(self, tag):
        self._update_ver = tag
        try:                # turn the version header into a one-click installer
            self.menu.entryconfig(0, label=f"⬆ Update to v{tag} (click to install)",
                                  state="normal", command=self._do_update)
        except tk.TclError:
            pass
        if self.icon is not None:
            try:
                self.icon.title = f"{APP_NAME} — update v{tag} available"
            except Exception:
                pass

    # ---- start with Windows (HKCU Run key, no admin needed) ---------------
    def _autostart_target(self):
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'                    # the packaged exe
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        return f'"{pyw}" "{os.path.abspath(__file__)}"'     # source mode

    def _autostart_enabled(self):
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_SLUG)
            return True
        except OSError:
            return False

    def _set_autostart(self, on):
        if winreg is None:
            return
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                if on:
                    winreg.SetValueEx(k, APP_SLUG, 0, winreg.REG_SZ,
                                      self._autostart_target())
                else:
                    try:
                        winreg.DeleteValue(k, APP_SLUG)
                    except FileNotFoundError:
                        pass
        except OSError:
            pass
        if hasattr(self, "_autostart_var"):
            self._autostart_var.set(self._autostart_enabled())

    # ---- usage refresh
    def refresh_async(self):
        if self._fetching:           # a fetch is already in flight -> coalesce
            return
        if self._timer is not None:
            self.root.after_cancel(self._timer)
            self._timer = None
        self._fetching = True
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        if is_session_locked():
            self.root.after(0, self._on_locked)
            return
        try:
            fh, sd, reset = fetch_usage()
            self.root.after(0, self._on_success, fh, sd, reset)
        except RateLimited as e:
            self.root.after(0, self._on_rate_limit, e.retry_after)
        except NeedsLogin:
            self.root.after(0, self._on_needs_login)
        except FileNotFoundError:
            self.root.after(0, self._on_needs_login)   # no creds file -> sign in
        except Exception as e:
            self.root.after(0, self._on_error, _friendly_error(e), SLOW_SECONDS)

    def _on_success(self, fh, sd, reset):
        self._backoff = FAST_SECONDS
        # adaptive cadence: speed up when the numbers move, slow down when idle
        changed = self._prev is None or (round(fh, 1), round(sd, 1)) != self._prev
        self._prev = (round(fh, 1), round(sd, 1))
        if changed:
            self._interval = FAST_SECONDS
        else:
            self._interval = min(int(self._interval * 1.5), SLOW_SECONDS)
        self._render(fh, sd, reset)
        wait = HIDDEN_SECONDS if self.hidden else self._interval
        self._schedule_next(wait)

    def _on_rate_limit(self, retry_after):
        # honour Retry-After if given, else exponential back-off up to 10 min
        if retry_after and retry_after > 0:
            wait = retry_after
        else:
            self._backoff = min(self._backoff * 2, RL_BACKOFF_MAX)
            wait = self._backoff
        # the dot is freshness-driven now: a brief rate-limit stays green as long
        # as the last good sync is < FRESH_SECONDS old, then ages to amber
        self._update_dot()
        self._schedule_next(wait)

    def _on_error(self, msg, wait):
        self._error(msg)
        self._schedule_next(wait)

    def _on_needs_login(self):
        """Refresh token is dead -> make it loud and one-click to fix."""
        first = not self._needs_login
        self._needs_login = True
        self._update_dot()                 # dot -> red
        self._update_reset_label()         # countdown -> "⚠ sign in"
        if self.icon is not None:
            try:
                self.icon.title = (f"{APP_NAME} — sign in needed "
                                   f"(right-click ▸ Sign in to Claude)")
                if first:                  # toast once per transition, not every poll
                    self.icon.notify(
                        "Sign-in expired. Right-click the bar → "
                        "Sign in to Claude.", APP_NAME)
            except Exception:
                pass
        # keep polling so we recover automatically once the user signs in
        self._schedule_next(60)

    def _find_claude_cli(self):
        """Locate the Claude Code CLI, or None. shutil.which alone is unreliable
        for a detached GUI process whose PATH may differ, so also probe the usual
        per-user install locations."""
        p = shutil.which("claude")
        if p and os.path.exists(p):
            return p
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        for cand in (os.path.join(HOME, ".local", "bin", "claude.exe"),
                     os.path.join(appdata, "npm", "claude.cmd") if appdata else "",
                     os.path.join(local, "Programs", "claude", "claude.exe") if local else ""):
            if cand and os.path.exists(cand):
                return cand
        return None

    def _sign_in(self):
        """Open Claude's sign-in flow (claude auth login) in a console window.
        The widget can't do OAuth itself, but it can launch the real flow so the
        user never has to know the command. If the Claude Code CLI isn't on this
        PC, say so clearly instead of letting cmd emit a cryptic 'path not found'."""
        claude = self._find_claude_cli()
        if claude:
            try:
                subprocess.Popen(["cmd", "/c", "start", "",
                                  "cmd", "/k", claude, "auth", "login"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return
            except Exception:
                pass
        # CLI missing (or launch failed) -> clear guidance, not a shell error
        if ctypes is not None and hasattr(ctypes, "windll"):
            msg = ("Claude Code isn't installed (or wasn't found) on this PC.\n\n"
                   "Token Usage Bar shows the usage from Claude Code, so install "
                   "Claude Code and sign in there, then click Refresh.\n\n"
                   "Open the Claude Code website now?")
            try:
                r = ctypes.windll.user32.MessageBoxW(0, msg, APP_NAME, 0x4 | 0x40)
                if r == 6:                              # IDYES
                    os.startfile("https://claude.com/product/claude-code")
            except Exception:
                pass

    def _on_locked(self):
        # workstation locked -> pause network calls, re-probe every 60s
        self._update_dot()
        self._schedule_next(60)

    def _schedule_next(self, secs):
        # common tail of every fetch outcome -> the fetch is now done
        self._fetching = False
        if self._timer is not None:
            self.root.after_cancel(self._timer)
        self._timer = self.root.after(int(secs * 1000), self.refresh_async)

    def _draw_dots(self, canvas, pct):
        """Segmented dot meter: N dots, the first round(pct/100*N) filled in the
        threshold colour, the rest left as the dim track colour."""
        canvas.delete("all")
        n, r, gap = self._dot_n, self._dot_r, self._dot_gap
        cy = self._line_h / 2
        filled = int(round(min(pct, 100) / 100.0 * n))
        color = bar_color(pct)
        for i in range(n):
            cx = self._dot_pad + r + i * gap
            c = color if i < filled else BAR_BG
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=c, width=0)

    def _render(self, fh, sd, reset):
        self._needs_login = False          # a good sync clears the sign-in state
        self._last = (fh, sd)
        for key, pct in (("5h", fh), ("7d", sd)):
            val, canvas = self.rows[key]
            val.config(text=f"{pct:.0f}", fg=bar_color(pct))
            self._draw_dots(canvas, pct)
        # keep the reset countdown (persisted + ticked locally) and mark live
        self._reset_iso = reset or self._reset_iso
        self._update_reset_label()
        self._last_ok = time.monotonic()   # freshness clock -> drives the dot
        self._update_dot()
        if self.icon is not None:
            try:
                pct = (round(fh), round(sd))
                if pct != self._tray_pct:    # only redraw the icon when it moves
                    self.icon.icon = make_tray_image(fh, sd)
                    self._tray_pct = pct
                self.icon.title = (f"{APP_NAME} — 5h {fh:.0f}%"
                                   f"  •  7d {sd:.0f}%")
            except Exception:
                pass

    def _error(self, msg):
        # dot is freshness-driven (ages to amber); surface detail in the tooltip
        self._update_dot()
        if self.icon is not None:
            try:
                self.icon.title = f"{APP_NAME} — {msg}"
            except Exception:
                pass

    # ---- 5h-reset countdown (local, network-independent)
    def _update_reset_label(self):
        if self._needs_login:
            self.reset_lbl.config(text="⚠ sign in", fg=DOT_ERR)
        else:
            self.reset_lbl.config(text="⟳ " + _reset_compact(self._reset_iso),
                                  fg=FG)

    # ---- 1 s heartbeat: countdown + sync freshness + topmost recovery
    def _tick_clock(self):
        self._update_reset_label()      # the 5h countdown ticks every second
        self._update_dot()              # re-evaluate sync freshness
        if not self.hidden:
            assert_topmost(self._hwnd())   # recover fast if the shell covers us
        self.root.after(1000, self._tick_clock)

    # ---- status dot: green while synced within FRESH_SECONDS, else amber
    def _update_dot(self):
        if self._needs_login:
            color = DOT_ERR         # red: sign-in required (overrides freshness)
        elif self._last_ok is None:
            color = DOT_IDLE        # grey until the very first sync lands
        elif time.monotonic() - self._last_ok < FRESH_SECONDS:
            color = DOT_LIVE
        else:
            color = DOT_WARN
        if color != self._dot_color:
            self._dot_color = color
            self.dot.itemconfig(self._dot_id, fill=color)

    # ---- auto-dim based on mouse proximity
    def _dim_tick(self):
        # proximity dimming runs in both modes — winfo_rootx/y give absolute
        # screen coords even when the bar is a child of the taskbar
        if not self.hidden and not self._dragging:
            try:
                px, py = self.root.winfo_pointerxy()
                x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
                w, h = self.root.winfo_width(), self.root.winfo_height()
                over = x <= px <= x + w and y <= py <= y + h
                idle = DOCK_IDLE_ALPHA if self._docked else IDLE_ALPHA
                target = HOVER_ALPHA if over else idle
                if target != self._alpha:        # only touch Win32 on change
                    self.root.attributes("-alpha", target)
                    self._alpha = target
            except tk.TclError:
                pass
        self.root.after(250, self._dim_tick)

    # ---- show / hide
    def hide(self):
        self.hidden = True
        self.root.withdraw()

    def show(self):
        self.hidden = False
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._interval = FAST_SECONDS
        self.refresh_async()

    def toggle(self):
        self.root.after(0, self.show if self.hidden else self.hide)

    # ---- dragging
    def _drag_start(self, e):
        self._ox, self._oy = e.x, e.y
        self._moved = False
        self._dragging = True

    def _drag(self, e):
        self._moved = True
        x = self.root.winfo_pointerx() - self._ox
        if self._docked:
            # slide horizontally along the taskbar; y stays centred in the band
            self.root.geometry(f"+{int(x)}+{int(self._dock_screen_y)}")
        else:
            y = self.root.winfo_pointery() - self._oy
            self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, e):
        self._dragging = False
        if self._moved:
            if not self._docked:
                self._clamp_pos()   # never park a floating bar on the taskbar
            self._save_pos()

    def _menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    # ---- position persistence
    def _load_pos(self):
        self._fx, self._fy = 40, 40   # floating position (screen coords)
        self._dock_x = 8              # x offset within the taskbar when docked
        try:
            with open(POS_PATH) as f:
                p = json.load(f)
            self._fx = p.get("x", self._fx)
            self._fy = p.get("y", self._fy)
            self._dock_x = p.get("dock_x", self._dock_x)
        except Exception:
            pass
        self.root.geometry(f"+{self._fx}+{self._fy}")

    def _clamp_pos(self):
        """Pull the window fully inside the work area (above the taskbar) so a
        stale saved position can never leave it invisible."""
        try:
            # winfo_width/height can be 1 before the window is mapped; fall back
            # to the requested size so an early clamp still uses real dimensions
            w = max(self.root.winfo_width(), self.root.winfo_reqwidth())
            h = max(self.root.winfo_height(), self.root.winfo_reqheight())
            x, y = self.root.winfo_x(), self.root.winfo_y()
            wa = work_area()
            if wa:
                left, top, right, bottom = wa
            else:
                left, top = 0, 0
                right, bottom = (self.root.winfo_screenwidth(),
                                 self.root.winfo_screenheight())
            x = min(max(x, left), max(left, right - w))
            y = min(max(y, top), max(top, bottom - h))
            self.root.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _save_pos(self):
        try:
            if self._docked:
                self._dock_x = self.root.winfo_x()   # parent-relative when child
            else:
                self._fx, self._fy = self.root.winfo_x(), self.root.winfo_y()
            with open(POS_PATH, "w") as f:
                json.dump({"x": self._fx, "y": self._fy,
                           "dock_x": self._dock_x}, f)
        except Exception:
            pass

    def quit(self):
        self._save_pos()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        self.root.destroy()


def _cleanup_after_update():
    """Remove the previous exe left behind by a self-update swap (best-effort)."""
    if not getattr(sys, "frozen", False):
        return
    old = os.path.join(os.path.dirname(sys.executable), APP_SLUG + ".old.exe")
    for _ in range(10):
        if not os.path.exists(old):
            return
        try:
            os.remove(old)
            return
        except OSError:
            time.sleep(0.3)            # the old process may still be exiting


def main():
    enable_dpi_awareness()         # crisp text on scaled (125% etc) displays
    # after a self-update relaunch (--updated) the old copy is still shutting
    # down, so wait briefly for its single-instance mutex to free up
    updated = "--updated" in sys.argv
    if not acquire_single_instance(8 if updated else 0):
        # another copy is already running -> don't add load on the API
        try:
            if ctypes and hasattr(ctypes, "windll"):
                ctypes.windll.user32.MessageBoxW(
                    0, f"{APP_NAME} is already running.",
                    APP_NAME, 0x40)
        except Exception:
            pass
        sys.exit(0)

    if updated:
        _cleanup_after_update()    # delete the previous exe the swap left behind

    root = tk.Tk()
    root.title(APP_NAME)
    app = TokenBar(root)

    # tray icon (runs its own message loop in a background thread)
    menu = pystray.Menu(
        pystray.MenuItem(f"{APP_NAME} v{VERSION}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Show / Hide", lambda: app.toggle(), default=True),
        pystray.MenuItem("Refresh now",
                         lambda: root.after(0, app.refresh_async)),
        pystray.MenuItem("Dock / Undock taskbar", lambda: app.toggle_dock()),
        pystray.MenuItem("Start with Windows",
                         lambda: app._set_autostart(not app._autostart_enabled()),
                         checked=lambda item: app._autostart_enabled()),
        pystray.MenuItem("Sign in to Claude…", lambda: app._sign_in()),
        pystray.MenuItem("Check for updates…", lambda: app._open_releases()),
        pystray.MenuItem("Quit", lambda: root.after(0, app.quit)),
    )
    icon = pystray.Icon("token_usage_bar", make_tray_image(0, 0),
                        APP_NAME, menu)
    app.icon = icon
    threading.Thread(target=icon.run, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    main()
