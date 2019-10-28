import subprocess
import tkinter as tk

def cmd(url, audioOnly):
    cmd = ["youtube-dl"]
    path = "C:/Users/vic8t/Desktop"
    if audioOnly:
        cmd.append("-f m4a")
    cmd.append(url)

    subprocess.call(cmd, cwd=path)

def set_url(url, inputUrl):
    url.set(inputUrl.get())

top = tk.Tk()
top.title("YouTube downloader")

url = tk.StringVar()
audio = tk.IntVar()

inputAudio = tk.Checkbutton(top, text = "Audio only", variable = audio, onvalue = 1, offvalue = 0)
inputAudio.grid(row = 0, column = 1)

inputUrl = tk.Entry(top, textvariable = url)
inputUrl.grid(row = 1, column = 0)

getUrl = tk.Button(top, text = "Download", command = lambda: cmd(inputUrl.get(), audio.get()))
getUrl.grid(row = 1, column = 1)

top.mainloop()