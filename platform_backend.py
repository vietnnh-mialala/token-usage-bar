"""Platform abstraction for Token Usage Bar.

All OS-specific behaviour lives here so token_bar.py stays platform-agnostic:
  - where credentials are stored (file vs macOS Keychain),
  - autostart (Windows Run key vs macOS LaunchAgent),
  - window z-order / single-instance / lock detection,
  - shell integration (open URL, sign-in flow, dialogs).

get_backend() returns the right implementation for the current OS. Anything we
can't do on a platform degrades to a safe no-op, so the widget still runs.

Unofficial; not affiliated with or endorsed by Anthropic.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser

HOME = os.path.expanduser("~")

# Default path Claude Code uses on Windows/Linux. macOS may use the Keychain
# instead (handled by MacCredentialStore), so this is only the *file* location.
DEFAULT_CRED_PATH = os.path.join(HOME, ".claude", ".credentials.json")


def _ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return HOME
    return path


# ---------------------------------------------------------------- credentials


class CredentialStore:
    """Reads/writes Claude Code's OAuth credentials (the {"claudeAiOauth": {...}}
    document). Subclasses back it with a file or the macOS Keychain."""

    def read(self):
        raise NotImplementedError

    def write(self, data):
        raise NotImplementedError

    def exists(self):
        try:
            self.read()
            return True
        except Exception:
            return False

    def describe(self):
        return self.__class__.__name__


class FileCredentialStore(CredentialStore):
    """Plain-file store with an atomic, crash-safe write and a last-known-good
    backup — the behaviour the Windows build has always used."""

    def __init__(self, path=DEFAULT_CRED_PATH):
        self.path = path
        self.backup = path + ".bak"

    def read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # main file briefly unreadable (sharing violation mid-replace) or
            # corrupt -> fall back to our read-only backup. Never restored over
            # the main file, so we can't clobber an update we lost the race to read.
            with open(self.backup, "r", encoding="utf-8") as f:
                return json.load(f)

    def write(self, data):
        try:
            if os.path.exists(self.path):
                shutil.copy2(self.path, self.backup)   # recovery point
        except OSError:
            pass                       # backup is best-effort, never block write
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)     # atomic: readers see old or new, never half

    def exists(self):
        return os.path.exists(self.path) or os.path.exists(self.backup)

    def describe(self):
        return self.path


class KeychainCredentialStore(CredentialStore):
    """Reads Claude Code's credentials from the macOS login Keychain.

    On macOS, Claude Code may store the same JSON it would otherwise write to
    ~/.claude/.credentials.json as a generic-password item instead. We shell out
    to /usr/bin/security to read/write it. The account name isn't guaranteed, so
    reads try with the login user first, then without an account filter."""

    def __init__(self, service="Claude Code-credentials", account=None):
        self.service = service
        self.account = account or os.environ.get("USER") or ""

    def _run(self, args):
        return subprocess.run(["/usr/bin/security", *args],
                              capture_output=True, text=True, timeout=10)

    def _read_raw(self, with_account):
        args = ["find-generic-password", "-s", self.service, "-w"]
        if with_account and self.account:
            args[1:1] = ["-a", self.account]
        try:
            out = self._run(args)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    def read(self):
        raw = self._read_raw(with_account=True)
        if raw is None:
            raw = self._read_raw(with_account=False)
        if not raw:
            raise FileNotFoundError("no Claude Code credentials in Keychain")
        return json.loads(raw)

    def write(self, data):
        payload = json.dumps(data)
        args = ["add-generic-password", "-U", "-s", self.service, "-w", payload]
        if self.account:
            args[2:2] = ["-a", self.account]
        try:
            out = self._run(args)
        except (OSError, subprocess.SubprocessError) as e:
            raise OSError(f"keychain write failed: {e}")
        if out.returncode != 0:
            raise OSError(f"keychain write failed: {out.stderr.strip()}")

    def exists(self):
        return (self._read_raw(with_account=True) is not None
                or self._read_raw(with_account=False) is not None)

    def describe(self):
        return f"macOS Keychain ({self.service})"


class MacCredentialStore(CredentialStore):
    """macOS store that works whether Claude Code wrote a file or used the
    Keychain — checked on every read so it keeps working after a later sign-in,
    no matter which backend Claude Code chose."""

    def __init__(self):
        self.file = FileCredentialStore(DEFAULT_CRED_PATH)
        self.keychain = KeychainCredentialStore()

    def read(self):
        last = None
        for store in (self.file, self.keychain):   # file first (cheap)
            try:
                return store.read()
            except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
                last = e
        raise last or FileNotFoundError("no Claude Code credentials found")

    def write(self, data):
        # update wherever the creds currently live; prefer the file if present,
        # otherwise write to the Keychain (where Claude Code put them)
        if self.file.exists():
            self.file.write(data)
        else:
            self.keychain.write(data)

    def exists(self):
        return self.file.exists() or self.keychain.exists()

    def describe(self):
        return "file or macOS Keychain"


# ---------------------------------------------------------------- single instance

_LOCK_HANDLE = None   # kept alive for the whole process so the flock persists


def _flock_single_instance(lock_path):
    """POSIX advisory-lock single-instance check. True if we got the lock."""
    global _LOCK_HANDLE
    try:
        import fcntl
    except Exception:
        return True                    # no fcntl -> can't lock, don't block
    try:
        f = open(lock_path, "a+")
    except OSError:
        return True
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False                   # another instance holds the lock
    _LOCK_HANDLE = f
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
    except OSError:
        pass
    return True


# ---------------------------------------------------------------- backends


class _Backend:
    """Generic POSIX backend; also the base class for Windows/macOS. Every method
    is a safe default so an unknown platform still runs (floating window, no
    autostart, no taskbar dock)."""

    name = "generic"
    mono_font = "TkFixedFont"          # Tk's guaranteed monospace family
    supports_dock = False              # overlay onto a taskbar (Windows only)
    supports_self_update = False       # in-place exe swap (Windows only)
    autostart_label = "Start at login"

    # ---- paths / storage
    def state_dir(self, slug):
        return _ensure_dir(os.path.join(HOME, ".config", slug))

    def make_credential_store(self):
        return FileCredentialStore(DEFAULT_CRED_PATH)

    # ---- display / window
    def enable_hidpi(self):
        pass

    def acquire_single_instance(self, slug, wait_seconds=0):
        lock_path = os.path.join(self.state_dir(slug), slug + ".lock")
        deadline = time.time() + max(0, wait_seconds)
        while True:
            if _flock_single_instance(lock_path):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.3)

    def is_session_locked(self):
        return False

    def work_area(self, root):
        return None                    # None -> caller falls back to full screen

    def assert_topmost(self, hwnd):
        pass                           # Tk's -topmost handles it off-Windows

    def root_hwnd(self, root):
        return None

    # ---- shell integration
    def open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def alert(self, title, text):
        sys.stderr.write(f"{title}: {text}\n")

    def confirm(self, title, text):
        return False

    def find_claude_cli(self):
        p = shutil.which("claude")
        return p if p and os.path.exists(p) else None

    def launch_sign_in(self, claude_path):
        return False

    # ---- autostart
    def autostart_enabled(self, slug):
        return False

    def set_autostart(self, slug, on, script_path=None, app_name=None):
        pass


class WindowsBackend(_Backend):
    name = "windows"
    mono_font = "Consolas"
    supports_dock = True
    supports_self_update = True
    autostart_label = "Start with Windows"

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _mutex = None                      # kept alive for the process lifetime

    # SetWindowPos / ShowWindow flags for re-asserting the bar's z-order
    _HWND_TOPMOST = -1
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010
    _SW_SHOWNA = 8
    _GA_ROOT = 2

    def __init__(self):
        try:
            import ctypes
            self._ctypes = ctypes if hasattr(ctypes, "windll") else None
        except Exception:
            self._ctypes = None
        try:
            import winreg
            self._winreg = winreg
        except ImportError:
            self._winreg = None

    def state_dir(self, slug):
        base = os.environ.get("LOCALAPPDATA", HOME)
        return _ensure_dir(os.path.join(base, slug))

    def enable_hidpi(self):
        if self._ctypes is None:
            return
        try:
            self._ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                self._ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def acquire_single_instance(self, slug, wait_seconds=0):
        if self._ctypes is None:
            return True
        ERROR_ALREADY_EXISTS = 183
        k32 = self._ctypes.windll.kernel32
        deadline = time.time() + max(0, wait_seconds)
        while True:
            mutex = k32.CreateMutexW(None, False, slug + "_singleton")
            if k32.GetLastError() != ERROR_ALREADY_EXISTS:
                WindowsBackend._mutex = mutex
                return True
            if mutex:
                k32.CloseHandle(mutex)
            if time.time() >= deadline:
                return False
            time.sleep(0.3)

    def is_session_locked(self):
        if self._ctypes is None:
            return False
        try:
            DESKTOP_READOBJECTS = 0x0001
            user32 = self._ctypes.windll.user32
            h = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
            if not h:
                return True
            user32.CloseDesktop(h)
            return False
        except Exception:
            return False

    def work_area(self, root):
        if self._ctypes is None:
            return None
        try:
            ctypes = self._ctypes

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

    def assert_topmost(self, hwnd):
        if self._ctypes is None or not hwnd:
            return
        try:
            u = self._ctypes.windll.user32
            u.ShowWindow(hwnd, self._SW_SHOWNA)
            u.SetWindowPos(hwnd, self._HWND_TOPMOST, 0, 0, 0, 0,
                           self._SWP_NOMOVE | self._SWP_NOSIZE
                           | self._SWP_NOACTIVATE)
        except Exception:
            pass

    def root_hwnd(self, root):
        if self._ctypes is None:
            return None
        try:
            h = root.winfo_id()
            return self._ctypes.windll.user32.GetAncestor(h, self._GA_ROOT) or h
        except Exception:
            return None

    def open_url(self, url):
        # os.startfile first: webbrowser.open() can hard-crash a --noconsole
        # PyInstaller build (native abort in ucrtbase that Python can't catch).
        try:
            os.startfile(url)
        except Exception:
            super().open_url(url)

    def alert(self, title, text):
        if self._ctypes is None:
            return super().alert(title, text)
        try:
            self._ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
        except Exception:
            super().alert(title, text)

    def confirm(self, title, text):
        if self._ctypes is None:
            return False
        try:
            r = self._ctypes.windll.user32.MessageBoxW(0, text, title, 0x4 | 0x40)
            return r == 6              # IDYES
        except Exception:
            return False

    def find_claude_cli(self):
        p = shutil.which("claude")
        if p and os.path.exists(p):
            return p
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        for cand in (os.path.join(HOME, ".local", "bin", "claude.exe"),
                     os.path.join(appdata, "npm", "claude.cmd") if appdata else "",
                     os.path.join(local, "Programs", "claude", "claude.exe")
                     if local else ""):
            if cand and os.path.exists(cand):
                return cand
        return None

    def launch_sign_in(self, claude_path):
        if not claude_path:
            return False
        try:
            subprocess.Popen(["cmd", "/c", "start", "",
                              "cmd", "/k", claude_path, "auth", "login"],
                             creationflags=getattr(subprocess,
                                                   "CREATE_NO_WINDOW", 0))
            return True
        except Exception:
            return False

    # ---- autostart via HKCU Run key (no admin needed)
    def _autostart_target(self, script_path):
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        return f'"{pyw}" "{script_path}"'

    def autostart_enabled(self, slug):
        if self._winreg is None:
            return False
        try:
            with self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER,
                                      self.RUN_KEY) as k:
                self._winreg.QueryValueEx(k, slug)
            return True
        except OSError:
            return False

    def set_autostart(self, slug, on, script_path=None, app_name=None):
        if self._winreg is None:
            return
        winreg = self._winreg
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as k:
                if on:
                    winreg.SetValueEx(k, slug, 0, winreg.REG_SZ,
                                      self._autostart_target(script_path))
                else:
                    try:
                        winreg.DeleteValue(k, slug)
                    except FileNotFoundError:
                        pass
        except OSError:
            pass


class MacBackend(_Backend):
    name = "macos"
    mono_font = "Menlo"
    supports_dock = False              # macOS has no taskbar -> float
    supports_self_update = False       # .exe self-update doesn't apply
    autostart_label = "Open at login"

    def state_dir(self, slug):
        return _ensure_dir(os.path.join(HOME, "Library", "Application Support",
                                        slug))

    def make_credential_store(self):
        return MacCredentialStore()

    def open_url(self, url):
        try:
            subprocess.Popen(["open", url])
        except Exception:
            super().open_url(url)

    def _osascript(self, script, capture=False):
        return subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=30)

    def alert(self, title, text):
        try:
            self._osascript(
                f'display dialog {json.dumps(text)} with title '
                f'{json.dumps(title)} buttons {{"OK"}} default button "OK"')
        except Exception:
            super().alert(title, text)

    def confirm(self, title, text):
        try:
            out = self._osascript(
                f'display dialog {json.dumps(text)} with title '
                f'{json.dumps(title)} buttons {{"No", "Yes"}} '
                f'default button "Yes"')
            return out.returncode == 0 and "Yes" in (out.stdout or "")
        except Exception:
            return False

    def find_claude_cli(self):
        p = shutil.which("claude")
        if p and os.path.exists(p):
            return p
        for cand in (os.path.join(HOME, ".local", "bin", "claude"),
                     os.path.join(HOME, ".claude", "local", "claude"),
                     "/opt/homebrew/bin/claude",
                     "/usr/local/bin/claude"):
            if os.path.exists(cand):
                return cand
        return None

    def launch_sign_in(self, claude_path):
        import shlex
        cmd = claude_path or "claude"
        shell_cmd = f"{shlex.quote(cmd)} auth login"
        try:
            self._osascript(
                f'tell application "Terminal"\n'
                f'  do script {json.dumps(shell_cmd)}\n'
                f'  activate\n'
                f'end tell')
            return True
        except Exception:
            return False

    # ---- autostart via a LaunchAgent plist (takes effect at next login)
    def _agent_path(self, slug):
        return os.path.join(HOME, "Library", "LaunchAgents",
                            f"com.{slug.lower()}.plist")

    def autostart_enabled(self, slug):
        return os.path.exists(self._agent_path(slug))

    def set_autostart(self, slug, on, script_path=None, app_name=None):
        path = self._agent_path(slug)
        if not on:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        if getattr(sys, "frozen", False):
            program_args = [sys.executable]
        else:
            program_args = [sys.executable, script_path or ""]
        args_xml = "".join(
            f"        <string>{a}</string>\n" for a in program_args if a)
        plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            '<dict>\n'
            '    <key>Label</key>\n'
            f'    <string>com.{slug.lower()}</string>\n'
            '    <key>ProgramArguments</key>\n'
            '    <array>\n'
            f'{args_xml}'
            '    </array>\n'
            '    <key>RunAtLoad</key>\n'
            '    <true/>\n'
            '</dict>\n'
            '</plist>\n')
        try:
            _ensure_dir(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as f:
                f.write(plist)
        except OSError:
            pass


def get_backend():
    if sys.platform.startswith("win"):
        return WindowsBackend()
    if sys.platform == "darwin":
        return MacBackend()
    return _Backend()
