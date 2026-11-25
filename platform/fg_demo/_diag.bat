@echo off
echo PWD=%CD%
echo PROBE: %~f0
echo EXE passed:
echo %1& rem NOT REAL LAUNCH: probe args via Set-Process style
rem printing every arg after exe:
eset x=0
:loop
shift
if '%~1'=='' goto end
echo  arg[%x%]: '%~1'
set /a x+=1
goto loop
:end
