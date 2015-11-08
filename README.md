## Vimeo Crawler ##

The purpose of this software is to make backup copies of videos 
and the whole albums, channels or accounts of videos at http://vimeo.com.

The crawler checks the specified URL and processes the specified video,
album, channel or the whole account, trying to locate the highest available
quality file for each video.

For every video found a file is downloaded to the target directory.
For any channel or album encountered, a subfolder is created in the target
directory, with file system links to the files in the target directory.

In default configuration, the program requires Mozilla Firefox.

The program starts a browser window and emulates clicks in it.
Please don't do anything inside that window.
You may minimize it or cover it with another window, if you wish.
The window will be closed automatically after crawling is complete.

The program is best run on Linux.
It also works fine on Windows, but Windows Vista or later is recommended, and NTFS file system.
Otherwise the program would be unable to create file system links to files from channel and album subfolders.

Please don't hesitate to mail me at [vmzakhar@gmail.com](mailto:vmzakhar@gmail.com)
if you find a bug or have an idea of a feature or improvement.
Thank you!

## Installation on Ubuntu ##

  * `sudo apt-get install firefox python-pip`
  * `sudo pip install selenium urlgrabber requests`

## Installation on Windows ##

  * Install Mozilla Firefox: http://mozilla.org
  * Install the latest Python 2.x: http://python.org/download/ (use `Windows x86 MSI Installer` even on 64-bit systems)
  * Install the latest pycurl: http://www.lfd.uci.edu/~gohlke/pythonlibs/#pycurl (use `win32` version for your version of Python)
  * Make sure `C:\Python2x\Scripts` (check the actual path on your system) is in your `PATH`.
  * Run `pip install selenium urlgrabber requests`

## General followup ##

  * Right click and choose "Save Link As..." to download the latest version of `VimeoCrawler.py`:
https://raw.githubusercontent.com/jolaf/vimeo-crawler/master/VimeoCrawler.py
  * Run `python VimeoCrawler.py` for further usage information
 
-- Moved from http://code.google.com/p/vimeo-crawler
