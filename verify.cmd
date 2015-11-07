@echo off
for %%f in (.\*) do echo "%%f" & ffmpeg -v error -i "%%f" -f null -
