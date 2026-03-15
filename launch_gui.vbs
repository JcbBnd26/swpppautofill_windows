Set FSO = CreateObject("Scripting.FileSystemObject")
strDir = FSO.GetParentFolderName(WScript.ScriptFullName)

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = strDir
WshShell.Run """" & strDir & "\.venv\Scripts\pythonw.exe"" """ & strDir & "\launch_gui.pyw""", 0, False
