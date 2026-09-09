' Lanza el agente sin ventana de consola.
'
' El .exe es una aplicacion de consola a proposito, para que --once, --scan y
' --help sirvan desde la terminal. Para el arranque automatico se lo llama
' desde aca, que lo corre con la ventana oculta (el 0 del Run).

Dim shell, fso, carpeta
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = carpeta
shell.Run """" & carpeta & "\twitch-game-agent.exe""", 0, False
