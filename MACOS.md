# Token Usage Bar trên macOS

Bản này đã được tách lớp platform để chạy trên cả Windows và macOS từ **cùng một
codebase**. Toàn bộ phần phụ thuộc OS nằm trong [`platform_backend.py`](platform_backend.py);
`token_bar.py` không còn dùng `ctypes`/`winreg` trực tiếp.

> ⚠️ **Chưa test trên Mac thật.** Code được viết phòng thủ (mọi lệnh macOS đều bọc
> try/except và degrade an toàn), nhưng cần một người có máy Mac chạy thử lần đầu.
> Phần "Cần kiểm chứng" ở cuối liệt kê những điểm dễ sai nhất.

## Khác biệt so với Windows

| Hạng mục | Windows | macOS |
|---|---|---|
| **Credentials** | `~/.claude/.credentials.json` | Thử **file trước, rồi Keychain** (`security find-generic-password -s "Claude Code-credentials"`) |
| Vị trí cửa sổ | Dock vào taskbar | Không có taskbar → **cửa sổ nổi**, luôn-trên-cùng |
| Tự khởi động | HKCU `Run` key | **LaunchAgent** `~/Library/LaunchAgents/com.tokenusagebar.plist` (có hiệu lực ở lần đăng nhập kế tiếp) |
| State dir | `%LOCALAPPDATA%\TokenUsageBar` | `~/Library/Application Support/TokenUsageBar` |
| Mở link / đăng nhập | `os.startfile`, `cmd /k` | `open`, đăng nhập qua **Terminal.app** (osascript) |
| Tự cập nhật (.exe) | Có | **Không** — menu chỉ mở trang Releases |
| Font | Consolas | Menlo |
| Single instance | Mutex | Khoá file (`flock`) |

## Chạy thử từ source (nhanh nhất, không cần build)

Cần Python 3 có tkinter:

```bash
# Homebrew: cài kèm Tk
brew install python-tk

# Kiểm tra tkinter có sẵn không
python3 -c "import tkinter; print(tkinter.TkVersion)"

# Cài dependency của app
cd "đường/dẫn/tới/self-tool-claude-token-bar"
python3 -m pip install -r requirements.txt

# Chạy
python3 token_bar.py
```

Một thanh nhỏ luôn-trên-cùng sẽ hiện ra; kéo để di chuyển, double-click để ẩn xuống
menu bar (icon ở góc phải trên). Chuột phải vào thanh để mở menu.

**Điều kiện hoạt động:** máy Mac đó phải đã cài Claude Code và đã đăng nhập (để có
credentials). Nếu chưa, thanh sẽ hiện `⚠ setup` / `⚠ sign in` và hướng dẫn cài.

## Build thành .app (đóng gói, không cần Python trên máy người dùng)

```bash
chmod +x build_macos.sh
./build_macos.sh
open dist/TokenUsageBar.app
```

App **chưa ký số (unsigned)**. Lần đầu mở, Gatekeeper có thể chặn:
- Chuột phải vào app → **Open**, hoặc
- Gỡ cờ quarantine: `xattr -dr com.apple.quarantine dist/TokenUsageBar.app`

(Tuỳ chọn) đặt một file `icon.icns` cạnh script để nhúng icon riêng.

## Cần kiểm chứng trên Mac thật

1. **Credentials ở đâu.** Chạy 2 lệnh sau để biết Claude Code lưu kiểu nào:
   ```bash
   ls -l ~/.claude/.credentials.json 2>/dev/null && echo "=> dùng FILE"
   security find-generic-password -s "Claude Code-credentials" -w >/dev/null 2>&1 \
     && echo "=> dùng KEYCHAIN"
   ```
   - Nếu là **Keychain**: lần đầu app đọc, macOS sẽ hỏi quyền truy cập Keychain →
     bấm **Always Allow**. Nếu service name khác `"Claude Code-credentials"`, sửa
     trong `KeychainCredentialStore.__init__` ở [`platform_backend.py`](platform_backend.py).
2. **`overrideredirect` + topmost.** Cửa sổ không viền, luôn-trên-cùng đôi khi cư xử
   lạ trên macOS (ví dụ không nổi trên app fullscreen). Kiểm tra hiển thị thực tế.
3. **Đăng nhập qua Terminal.** Menu → *Sign in to Claude…* phải mở Terminal chạy
   `claude auth login`. Xác nhận lệnh `claude auth login` đúng với phiên bản Claude
   Code bạn đang dùng.
4. **LaunchAgent.** Bật *Open at login*, đăng xuất/đăng nhập lại, kiểm tra app tự chạy.
