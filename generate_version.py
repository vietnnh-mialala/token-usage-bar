"""Write version_info.txt (Windows version resource) for the exe, reading the
single source of truth: the VERSION constant in token_bar.py."""
import re

src = open("token_bar.py", encoding="utf-8").read()
ver = re.search(r'VERSION\s*=\s*"([\d.]+)"', src).group(1)
parts = (ver.split(".") + ["0", "0", "0", "0"])[:4]
tup = ", ".join(parts)          # e.g. 1, 0, 0, 0
dot = ".".join(parts)           # e.g. 1.0.0.0

content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tup}), prodvers=({tup}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Token Usage Bar (unofficial)'),
      StringStruct('FileDescription', 'Claude usage meter for the taskbar'),
      StringStruct('FileVersion', '{dot}'),
      StringStruct('InternalName', 'TokenUsageBar'),
      StringStruct('OriginalFilename', 'TokenUsageBar.exe'),
      StringStruct('ProductName', 'Token Usage Bar'),
      StringStruct('ProductVersion', '{dot}'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
open("version_info.txt", "w", encoding="utf-8").write(content)
print("wrote version_info.txt for v" + ver)
