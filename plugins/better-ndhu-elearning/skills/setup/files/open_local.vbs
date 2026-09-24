' BetterElearning: runs open_local.ps1 without flashing a console window.
If WScript.Arguments.Count = 0 Then WScript.Quit
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
url = Replace(WScript.Arguments(0), """", "")
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\open_local.ps1"" """ & url & """", 0, False
