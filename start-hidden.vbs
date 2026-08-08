' Launches the tracker with no console window. Used by the auto-start task.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here
shell.Run """" & here & "\.venv\Scripts\pythonw.exe"" app.py run", 0, False
