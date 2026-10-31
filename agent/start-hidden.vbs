' Launches the agent without a console window.
'
' The .exe is a console application on purpose, so --once, --scan and --help
' work from a terminal. For the automatic start it is called from here, which
' runs it with the window hidden (the 0 in Run).

Dim shell, fso, folder
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
shell.Run """" & folder & "\twitch-game-agent.exe""", 0, False
