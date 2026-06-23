"""macOS smoke test — runs on a real macOS GitHub Actions runner.

Verifies the platform layer without needing a GUI, Claude Code, or any
credentials: the backend selection, the credential store's graceful degradation
when nothing is signed in, LaunchAgent plist generation, and that importing the
app module doesn't start a GUI or crash. Exits non-zero on any failure.
"""
import os
import sys
import tempfile

# import from the repo root regardless of where CI invokes us from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_backend as pb

b = pb.get_backend()
assert b.name == "macos", f"expected macos backend, got {b.name!r}"
assert b.supports_dock is False, "macOS has no taskbar to dock onto"
assert b.supports_self_update is False, "no in-place .exe self-update on macOS"
assert b.mono_font == "Menlo", f"unexpected font {b.mono_font!r}"
print("backend OK:", b.name)

store = b.make_credential_store()
assert isinstance(store, pb.MacCredentialStore), type(store)
# No creds on CI -> exists() is False and read() raises cleanly (never crashes).
print("store exists:", store.exists())
try:
    store.read()
    print("store.read returned creds (unexpected on CI, but not a failure)")
except (FileNotFoundError, OSError) as e:
    print("store.read raised cleanly:", type(e).__name__)

# Keychain probe must not raise even when the item is absent.
assert pb.KeychainCredentialStore().exists() in (True, False)
print("keychain probe OK")

# LaunchAgent plist generation -> temp path so we don't touch ~/Library.
tmp = os.path.join(tempfile.gettempdir(), "com.tokenusagebar.plist")
b._agent_path = lambda slug: tmp
b.set_autostart("TokenUsageBar", True, script_path="/tmp/token_bar.py")
assert os.path.exists(tmp), "plist not written"
content = open(tmp).read()
assert "<key>RunAtLoad</key>" in content and "com.tokenusagebar" in content, content
b.set_autostart("TokenUsageBar", False)
assert not os.path.exists(tmp), "plist not removed on disable"
print("autostart plist OK")

# Importing the app module must not start a GUI or crash.
import token_bar  # noqa: E402
assert token_bar.BACKEND.name == "macos"
print("token_bar import OK; VERSION", token_bar.VERSION)

print("ALL SMOKE CHECKS PASSED")
