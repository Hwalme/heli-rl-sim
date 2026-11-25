@echo off
"D:\Program Files\FlightGear 2024.1\bin\fgfs.exe" ^
  --launcher=0 ^
  --fdm=null ^
  --aircraft=bo105 ^
  --airport=PHNL ^
  --fg-root="D:\Program Files\FlightGear 2024.1\downloads\fgdata_2024_1" ^
  --fg-aircraft="D:\Program Files\FlightGear 2024.1\downloads\Aircraft" ^
  --generic=socket,in,100,127.0.0.1,5500,udp,jsbsim_replay ^
  --telnet=socket,in,5,127.0.0.1,5503,tcp ^
  --timeofday=noon ^
  --enable-astro=0 ^
  --prop:/sim/view/configured-width=1024 ^
  --prop:/sim/view/configured-height=768
