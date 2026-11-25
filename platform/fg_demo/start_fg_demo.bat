@echo off
rem B1 v2 launcher - FlightGear Bo105 demo with Nasal attitude stabilizer
rem Config file copied to FG root to avoid Chinese path issues

"D:\Program Files\FlightGear 2024.1\bin\fgfs.exe" ^
  --launcher=0 ^
  --aircraft=bo105 ^
  --airport=PHNL ^
  --fg-root="D:\Program Files\FlightGear 2024.1\downloads\fgdata_2024_1" ^
  --fg-aircraft="D:\Program Files\FlightGear 2024.1\downloads\Aircraft" ^
  --config="D:\Program Files\FlightGear 2024.1\demo_stabilizer.xml" ^
  --telnet=socket,in,5,127.0.0.1,5501,tcp ^
  --console=0 ^
  --prop:/devel=1 ^
  --prop:/quickstart=1 ^
  --wind=0@0 ^
  --disable-real-weather-fetch ^
  --disable-terrasync ^
  --fullscreen=0 ^
  --prop:/sim/view/configured-width=1024 ^
  --prop:/sim/view/configured-height=768
