saveslides
==========

Save slides from Missouri State University's Mediasite.

## Description

Given the URL of a Mediasite 7.x lecture, create a video using the original
audio and slides. This script is customized to work with Missouri State
University's Mediasite.

Example usage:
    python3 saveslides.py -u http://missouristate.mediasite.com/Mediasite/Play/c03e35ed05754cc582c778a068338f681d

The slides and original video will be downloaded into a sub-directory in the
current directory called "slides". The output video is named "lecture.mp4" and
is placed in the current directory.

## Requirements
* Python 3.x
* mencoder (MPlayer's movie encoder)
