Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Projects\swpppautofill_windows"
WshShell.Run """C:\Projects\swpppautofill_windows\.venv\Scripts\pythonw.exe"" ""C:\Projects\swpppautofill_windows\launch_gui.pyw""", 0, False
