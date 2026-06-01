' Launches the Token Bar with no console window.
Dim sh, fso, here
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
' 0 = hidden window, False = don't wait
sh.Run "pythonw.exe """ & here & "\token_bar.py""", 0, False
