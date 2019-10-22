import subprocess
import os

cmd = ["youtube-dl"]
path = "C:/Users/vic8t/Desktop"
audio_only = 1
if audio_only:
    cmd.append("-f m4a")
cmd.append("https://www.youtube.com/watch?v=8BUig7mcFsw")

subprocess.call(cmd, cwd=path)