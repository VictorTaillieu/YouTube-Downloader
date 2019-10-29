import subprocess
import tkinter as tk
from tkinter import filedialog

def cmd(url, audioOnly, outputDir):
    cmd = ["youtube-dl"]
    path = outputDir
    if audioOnly:
        cmd.append("-f m4a")
    cmd.append(url)

    subprocess.call(cmd, cwd=path)
    # open confirmation window

def set_url(url, inputUrl):
    url.set(inputUrl.get())

def set_dir(path):
    folder = filedialog.askdirectory()
    path.set(folder)

top = tk.Tk()
top.title("YouTube downloader")
top.iconbitmap("images/logo.ico")
top.geometry("500x280")
top.resizable(width=False, height=False)
top.attributes("-topmost", True)

image = tk.PhotoImage(file="images/background.png")
background = tk.Label(top, image=image)
background.place(relwidth=1, relheight=1)

frameDir = tk.Frame(top, bg='#e6271d', bd=10)
frameDir.place(relx=0.5, rely=0.1, relwidth=0.75, relheight=0.25, anchor='n')
frameVideo = tk.Frame(top, bg='#e6271d', bd=10)
frameVideo.place(relx=0.5, rely=0.4, relwidth=0.75, relheight=0.5, anchor='n')

path = tk.StringVar()
audio = tk.IntVar()
url = tk.StringVar()
# status = tk.StringVar()

getDir = tk.Button(frameDir, text="Choose directory", command=lambda: set_dir(path))
getDir.place(relwidth=0.3, relheight=1)

scroll = tk.Scrollbar(frameDir, orient="horizontal")
scroll.place(relx=0.35, rely=0.7, relwidth=0.65, relheight=0.3)
outputDir = tk.Entry(frameDir, textvariable=path, state="readonly", xscrollcommand=scroll.set)
outputDir.place(relx=0.35, relwidth=0.65, relheight=0.7)
scroll.config(command=outputDir.xview)

inputAudio = tk.Checkbutton(frameVideo, text="Audio only", variable=audio, onvalue=1, offvalue=0)
inputAudio.place(rely=0.1, relwidth=0.3, relheight=0.3)

# showStatus = tk.Label(frameVideo, textvariable=status)
# showStatus.place(relx=0.5, rely=0.1, relwidth=0.5, relheight=0.3)

inputUrl = tk.Entry(frameVideo, textvariable=url)
inputUrl.place(rely=0.6, relwidth=0.7, relheight=0.3)

getUrl = tk.Button(frameVideo, text="Download", activebackground="#ffa100", command=lambda: cmd(inputUrl.get(), audio.get(), outputDir.get()))
getUrl.place(relx=0.75, rely=0.6, relwidth=0.25, relheight=0.3)

top.mainloop()