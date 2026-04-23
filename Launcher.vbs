Set UAC = CreateObject("Shell.Application")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strPath = objFSO.GetParentFolderName(WScript.ScriptFullName)
UAC.ShellExecute strPath & "\Run_Bot.bat", "", "", "runas", 0