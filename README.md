# YouTube Downloader

This program allows you to download YouTube videos, it is based on [yt-dlp](https://github.com/yt-dlp/yt-dlp). The graphical interface is made with Tkinter library.

![Interface](/images/interface.png)

## Prerequisites

This application requires [Python 3.8](https://www.python.org/downloads/). Check your python version using:
````
$ python --version
Python 3.8.0
````
There are no requirements for this program.

**If you don't want to install Python, just download the [release](https://github.com/Vic8t/YouTube-Downloader/releases)**.

## Quickstart

Run the program with:
```
$ python ytdownload.py
```
From the interface, you first choose an output directory. Then, you may select some options like audio only or subtitles. Finally, paste the URL of the video and press download.

If you want to download several videos, paste all URLs in urls.txt on separate lines. Then, press download without writing anything in the URL box.

**Notice: Sometimes yt-dlp is updated. If the download doesn't work, this could be the cause. In this case press update.**
