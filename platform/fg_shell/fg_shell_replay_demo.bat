@echo off
rem B-Shell detached continuous replay demo (run via WMI so it survives the session).
rem FG = display shell (fdm=null); JSBSim = physics authority (acceptance-grade trajectory).
rem VISUAL STAND-IN: rendered model is bo105, physics is AH-1S (repaired).
"C:\Python313\python.exe" "C:\Users\ADMINI~1\AppData\Local\Temp\fg_shell_proto\run_replay_demo.py" ^
  >> "C:\Users\ADMINI~1\AppData\Local\Temp\fg_shell_proto\demo_replay.log" 2>&1
