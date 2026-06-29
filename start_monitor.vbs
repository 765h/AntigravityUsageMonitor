Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\ssfnn\.gemini\antigravity\scratch\AntigravityUsageMonitor"
WshShell.Run "pythonw.exe monitor.py", 0, False
