# Token Usage Bar

A tiny floating bar that shows how much of your Claude usage limits you've
consumed — the same numbers as Claude Code's `/usage`. By default it **docks
onto the empty left area of the Windows taskbar**; it can also float anywhere.

```
●  ⟳ 1:13:26   5H ●●●●●●●○○○ 68   7D ●○○○○○○○○○ 7
```

> **Unofficial** — not affiliated with, endorsed by, or sponsored by Anthropic.
> "Claude" is a trademark of Anthropic. This tool only *reads* your local usage
> meter via the same private endpoint Claude Code uses.

## What it shows
- **● status dot** — teal when the last sync is fresh (< 60 s), amber when the
  data is going stale, grey before the first sync.
- **⟳ h:mm:ss** — the **primary counter**: time left until the rolling **5-hour**
  limit resets. It ticks down locally every second, so it stays correct and
  visible even during a rate-limit / back-off / lock (it never disappears).
- **5h** — your rolling 5-hour session limit (`five_hour.utilization`), shown as
  a **10-dot meter** (each dot = 10 %) + number.
- **7d** — your 7-day limit (`seven_day.utilization`), same dot meter + number.
- The lit dots and the number use a neon palette: teal (< 60 %), amber
  (60–85 %), red (≥ 85 %); unlit dots stay a dim track colour.

The tray icon tooltip shows the same numbers (and any error message).

## Install (packaged, recommended)
No Python needed — a self-contained `TokenUsageBar.exe` is provided.

1. Right-click **`install.ps1`** → **Run with PowerShell**
   (or: `powershell -ExecutionPolicy Bypass -File install.ps1`).

It copies the exe to `%LOCALAPPDATA%\TokenUsageBar`, enables **start-at-login**
(HKCU `Run` key), and starts it now. No admin rights required. You can toggle
start-at-login any time from the right-click menu → **Start with Windows**.

To remove it later, run **`uninstall.ps1`** the same way.

> The exe reads your Claude OAuth token from `~/.claude/.credentials.json`, so
> Claude Code must have been signed in on this machine at least once.

### Windows SmartScreen / antivirus
The exe is **not code-signed**, so on first run Windows SmartScreen may show
"Windows protected your PC" → click **More info → Run anyway**. Some antivirus
engines also false-positive on PyInstaller executables; the source is here and
each release is scanned on [VirusTotal](https://www.virustotal.com/). If you'd
rather not trust a binary, run from source (below) or build it yourself with
`build.ps1`.

## Run from source (developers)
Needs Python 3 + `pystray` + `pillow` (see Dependencies). Then double-click
**`Start Token Bar.vbs`** (no console window), or from a terminal:
```powershell
pythonw token_bar.py
```
Rebuild the exe with **`build.ps1`**.

## Controls
- **Drag** the bar to move it:
  - *Docked* → slides **horizontally** along the taskbar (vertical stays centred).
  - *Floating* → moves freely; it's auto-clamped so it never hides behind the taskbar.
- **Right-click** the bar (or the tray icon) → **Refresh now** / **Dock /
  Undock taskbar** / **Start with Windows** (toggle) / **Check for updates…** /
  **Hide to tray** / **Quit**. The menu header shows the version, and turns into
  an **update link** when a newer release is published on GitHub.
- **Double-click** the bar → hide to tray.
- **Tray icon** (bottom-right of the taskbar): left-click = show/hide.
- **Auto-dim** — the bar fades when the mouse isn't over it and lights up on
  hover. Idle opacity is higher when docked (so the dark bar stays legible on the
  dark taskbar) than when floating over the desktop.
- Position (floating x/y **and** docked x) is remembered in `.window_pos.json`.

## Docking onto the taskbar
`DOCK_TO_TASKBAR = True` (top of `token_bar.py`) makes the bar sit **on** the
taskbar's empty area. It does this by being a **topmost overlay** positioned over
the taskbar band — *not* by re-parenting into the taskbar.

> Why an overlay and not a child window? On Windows 11 the taskbar is a
> DirectComposition surface that paints *over* any child HWND you `SetParent`
> into `Shell_TrayWnd` — so a docked child is invisible. A topmost window placed
> over the taskbar renders fine, but after a shell event (Start menu, a click)
> the taskbar can paint over it. The key detail: `SetWindowPos(HWND_TOPMOST)`
> does **not** bring it back on Win11 — only `ShowWindow(SW_SHOWNA)` (show, no
> activate) re-raises it. The bar calls that once a second, so it pops back
> within ~1 s with no flicker and no focus theft.

Set `DOCK_TO_TASKBAR = False` to start floating instead, or toggle at runtime via
the right-click menu.

> On Windows 11 new tray icons start in the hidden overflow (the `^` chevron).
> Drag the bars icon onto the taskbar to keep it always visible.

## Dependencies
The packaged `TokenUsageBar.exe` bundles everything — nothing to install.

Only when **running from source**: `pystray` and `pillow` (for the tray icon);
everything else is the Python standard library + tkinter.
```powershell
python -m pip install pystray pillow
```

## Start automatically at login
`install.ps1` enables it (HKCU `Run` key), and the right-click **Start with
Windows** toggle turns it on/off — so it works no matter how you installed
(installer, winget, or a manual download). For the **from-source** setup you can
instead press `Win+R`, type `shell:startup`, Enter, and drop a shortcut to
`Start Token Bar.vbs` there.

## Safety / good-citizen behaviour
Reading `/api/oauth/usage` does **not** consume model tokens or count against
your usage limits; it only reads the meter.
- **Adaptive polling** — 30 s while the numbers are moving, slowing to 5 min when
  idle or hidden to the tray.
- **429 back-off** — honours `Retry-After`, otherwise backs off 30→60→…→300 s.
- **Pauses when the workstation is locked** — no network calls on the lock
  screen; re-probes every 60 s and resumes on unlock.
- **Single instance** — a named mutex prevents a second copy from running (and
  doubling the API calls); launching again just shows an "already running" notice.

## How it works
- Reads your OAuth token from `~/.claude/.credentials.json` (written by Claude Code).
- Polls `https://api.anthropic.com/api/oauth/usage` on the adaptive schedule above.
- Lets **Claude Code own the token lifecycle**: it reads the access token from
  disk and only refreshes *reactively* — if a usage call returns 401/403 it waits
  a short grace period for Claude Code to rotate the shared refresh token, and
  refreshes itself only as a last resort (e.g. Claude Code isn't running). This
  avoids both the widget and Claude Code rotating the one shared refresh token at
  once (which would 403 the loser), while still keeping the bar working on its own.
- Crisp on scaled (125 % etc.) displays — it's DPI-aware, so its coordinates are
  in physical pixels.

## Notes
- This uses the same private endpoint Claude Code uses; Anthropic could change it.
- Your token never leaves your machine except to Anthropic's own servers.
