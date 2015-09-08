#!/usr/bin/env python3
"""Given the URL of a Mediasite 7.x lecture, create a video using the original
audio and slides. This script is customized to work with Missouri State
University's Mediasite.

Example usage:
    python3 saveslides.py -u http://missouristate.mediasite.com/Mediasite/Play/c03e35ed05754cc582c778a068338f681d

The slides and original video will be downloaded into a sub-directory in the
current directory called "slides". The output video is named "lecture.mp4" and
is placed in the current directory.

Requirements:
    * Python 3.x
    * mencoder (MPlayer's movie encoder)

"""
import argparse
import json
import os
import re
import requests
import shlex
import subprocess


###############################################################################
# CONSTANTS
###############################################################################


# The URL for MSU's Central Authentication Service
CAS_URL = 'https://cas.missouristate.edu'

# Username
USERNAME = ''

# Password
PASSWORD = ''


###############################################################################
# AUTHENTICATION HELPER FUNCTIONS
###############################################################################


def get_cas_session(base_url, url, username, password):
    """Return a session object for the specified URL after authenticaing to the
    Central Authenticaion Service ("CAS").

    Keyword arguments:
    base_url -- base URL of the CAS site (e.g. 'https://cas.missouristate.edu')
    url      -- target URL
    username -- username
    password -- password

    """
    # Create a new session using the Python requests library
    session = requests.session()

    # Try to retrieve the target URL -- the response will be a login page
    response = session.get(url)

    # In order to login, we must first retrieve some parameters for the form
    # The first parameter is the "lt" value
    lt = get_html_value(response.text, 'name="lt" value="')

    # The second parameter is the form's action
    relative_action = get_html_value(response.text, 'name="login" action="')
    action = '{0}/{1}'.format(base_url, relative_action)

    # Create the "payload" for our form -- the values we will be submitting
    payload = {'lt': lt,
               'execution': 'e1s1',
               '_eventId': 'submit',
               'username': username,
               'password': password}

    # POST to the form
    response = session.post(action, payload)

    # Now we have a page asking us to press a continue button since our
    # browser does not support JavaScript ... argh! We have to retrieve some
    # more form parameters
    relay_state = get_html_value(response.text, '"RelayState" value="')
    saml_response = get_html_value(response.text, '"SAMLResponse" value="')
    payload = {'RelayState': relay_state,
               'SAMLResponse': saml_response}

    # For the action parameter, we have to replace the hex character codes with
    # their actual values
    action = get_html_value(response.text, '<form action="')
    action = action.replace('&#x3a;', ':')
    action = action.replace('&#x2f;', '/')

    # POST to the form
    response = session.post(action, payload)

    return session


def get_html_value(html, key):
    """Return the value of the parameter that starts immediately after "key"
    is found in the specified HTML. Note that this is a very basic parsing
    mechanism!

    Keyword arguments:
    html -- HTML document
    key  -- search text

    """
    # Get the starting index for the value
    start_index = html.find(key) + len(key)
    # Find the closing quote at the end of the value
    end_index = start_index + html[start_index:].find('"')
    return html[start_index:end_index]


###############################################################################
# MEDIASITE FUNCTIONS
###############################################################################


def create_temp_dir(dir_name):
    """Create the specified temporary directory and return the absolute path
    for the directory.

    Keyword arguments:
    dir_name -- temporary directory name/path

    """
    temp_path = os.path.abspath(dir_name)
    if not os.path.exists(temp_path):
        os.mkdir(temp_path)
    return temp_path


def download_jpgs(session, slide_info):
    """Download the specified JPEG images to "slide_NNNN.jpg" files. Any files
    that already exist will not be downloaded again.

    Keyword arguments:
    session    -- authenticated session
    slide_info -- list of (url, path) slide information tuples

    """
    print('Downloading {0:d} slide JPEGs ...'.format(len(slide_info)))
    for slide_url, slide_path in slide_info:
        if not os.path.exists(slide_path):
            resp = session.get(slide_url, stream=True)
            if resp.status_code == 200:
                with open(slide_path, 'wb') as f:
                    for chunk in resp:
                        f.write(chunk)


def download_manifest(session, mediasite_url, temp_path):
    """Download the Mediasite manifest from the specified URL to "manifest.js"
    and return the manifest as a dictionary.

    Keyword arguments:
    session   -- authenticated session
    url       -- target Mediasite URL
    temp_path -- temporary file path

    """
    request_url = re.sub('/Play/.*$',
                         '/PlayerService/PlayerService.svc/json/GetPlayerOptions',
                         mediasite_url, flags=re.IGNORECASE)
    headers = {'Content-Type': 'application/json', 'Accept': 'text/plain'}
    payload = {'getPlayerOptionsRequest':
               {'ResourceId': os.path.basename(mediasite_url),
                'QueryString': '',
                'UseScreenReader': False}}
    resp = session.post(request_url, data=json.dumps(payload), headers=headers)
    manifest_filepath = os.path.join(temp_path, 'manifest.js')
    with open(manifest_filepath, 'w') as f:
        f.write(resp.text)
    # Return the manifest dictionary
    result = {}
    with open(manifest_filepath) as f:
        result = json.loads(f.read())['d']
    return result


def download_video(session, video_url, video_path):
    """Download the video at the specified URL to the "original.mp4" file. If
    the file already exists it will not be downloaded.

    Keyword arguments:
    session    -- authenticated session
    video_url  -- video URL
    video_path -- destination path

    """
    if os.path.exists(video_path):
        print('Video already exists ... not downloading.')
        return
    print('Downloading {0}'.format(video_url))
    resp = session.get(video_url, stream=True)
    if resp.status_code == 200:
        with open(video_path, 'wb') as f:
            for chunk in resp:
                f.write(chunk)


def get_duration_ms(manifest):
    """Return the duration time from the specified manifest.

    Keyword arguments:
    manifest -- manifest dictionary

    """
    return manifest['Presentation']['Duration']


def get_slide_base_url(manifest):
    """Return the slide base URL from the specified manifest.

    Keyword arguments:
    manifest -- manifest dictionary

    """
    return manifest['Presentation']['Streams'][0]['SlideBaseUrl']


def get_slide_info(manifest, temp_path):
    """Return all of the basic information from the specified manifest,
    including transition times and slide image URLs. The result is a tuple of
    the form (video URL, total duration, transition times,
    [(slideN url, local slideN path), ...]).

    Keyword arguments:
    manifest  -- manifest dictionary
    temp_path -- temporary file path

    """
    video_url = get_video_url(manifest)
    trans_times_ms = get_transition_times_ms(manifest)
    num_slides = len(trans_times_ms)
    duration_ms = get_duration_ms(manifest)
    trans_times_ms.append(duration_ms)
    slide_base_url = get_slide_base_url(manifest)
    ticket_id = get_slide_playback_ticket_id(manifest)
    slide_info = []
    for index in range(1, num_slides + 1):
        relative = 'slide_{0:04d}.jpg?playbackTicket={1:s}'.format(
            index, ticket_id)
        slide_n_url = os.path.join(slide_base_url, relative)
        relative = 'slide_{0:04d}.jpg'.format(index)
        slide_n_path = os.path.join(temp_path, relative)
        slide_info.append((slide_n_url, slide_n_path))
    return (video_url, duration_ms, trans_times_ms, slide_info)


def get_slide_playback_ticket_id(manifest):
    """Return the SlidePlaybackTicketId from the specified manifest.

    Keyword arguments:
    manifest -- manifest dictionary

    """
    return manifest['Presentation']['Streams'][0]['SlidePlaybackTicketId']


def get_transition_times_ms(manifest):
    """Return the transition times from the specified manifest.

    Keyword arguments:
    manifest -- manifest dictionary

    """
    transition_times_ms = []
    slides = manifest['Presentation']['Streams'][0]['Slides']
    for slide in slides:
        transition_times_ms.append(slide['Time'])
    return transition_times_ms


def get_video_url(manifest):
    """Return the video URL from the specified manifest.

    Keyword arguments:
    manifest -- manifest dictionary

    """
    return manifest['Presentation']['Streams'][1]['VideoUrls'][0]['Location']


def write_jpgs_list(trans_times_ms, slide_info, slides_fps, file_path):
    """Write the "jpg_frames.txt" file.

    Keyword arguments:
    trans_times_ms -- list of transition times in milliseconds
    slide_info     -- list of (url, path) slide information tuples
    slides_fps     -- slides frames-per-second
    file_path      -- destination file path

    """
    # Build list of slide JPGs
    list_jpgs = [info[1] for info in slide_info]
    list_jpgs.insert(0, list_jpgs[0])
    list_jpgs.append(list_jpgs[-1])
    # Index into list_jpgs
    jpg_idx = 0
    # Number of milliseconds elapsed
    ms = 0
    # The length of time to show each frame
    frame_duration = 1000.0 / slides_fps
    # Create the output list
    output_lines = []
    for trans_time in trans_times_ms:
        while ms <= trans_time:
            output_lines.append(list_jpgs[jpg_idx])
            ms += frame_duration
        # Advance the frame.
        jpg_idx += 1
    with open(file_path, 'w') as fh:
        lines_str = '\n'.join(output_lines)
        fh.write(lines_str)


###############################################################################
# MENCODER FUNCTIONS
###############################################################################


def create_output_video(mencoder_path, original_video_path, jpg_frames_path,
                        duration_ms, slides_fps, vf_dimensions, output_file):
    """Create the output video.

    Keyword arguments:
    mencoder_path       -- path to the mencoder program
    original_video_path -- path to the original video
    jpg_frames_path     -- path to the jpeg frames file
    duration_ms         -- video length in milliseconds
    slides_fps          -- frames-per-second for slides
    vf_dimensions       -- video file dimensions
    output_file         -- output file path

    """
    mf_fps = '-mf fps=%d:type=jpg' % slides_fps
    cmd = ' '.join([mencoder_path,
                    'mf://@"%s"' % jpg_frames_path,
                    mf_fps,
                    vf_dimensions,
                    '-ovc lavc',
                    '-oac mp3lame',
                    '-audiofile "%s"' % original_video_path,
                    '-o "%s"' % output_file])
    print('Building slides video (%d min): %s' % (duration_ms//1000//60, cmd))
    args = shlex.split(cmd)
    p = subprocess.Popen(args)
    p.wait()
    print()


###############################################################################
# MAIN PROGRAM
###############################################################################


def parse_arguments():
    """Return the command-line arguments passed to the program."""
    parser = argparse.ArgumentParser(
        description='Create a slideshow video from a Mediasite lecture.')
    parser.add_argument('--mencoder', action='store', dest='mencoder_loc',
                        default='mencoder',
                        help='Custom location of mencoder (default "mencoder")')
    parser.add_argument('-u', '--url', action='store', dest='url',
                        default=None,
                        help='URL of the presentation.')
    parser.add_argument('-o', '--output_file', action='store',
                        dest='output_file', default='./lecture.mp4',
                        help='Filename for the resulting video')
    parser.add_argument('-t', '--temp_dir', action='store', dest='temp_dir',
                        default='./slides',
                        help='Temporary directory for storing downloaded slides and video (default ./slides)')
    parser.add_argument('--slides_fps', action='store', type=float,
                        default='5',
                        help='Frames per second in the video (default 5)')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dim1024x768', action='store_true')
    group.add_argument('--dim800x600', action='store_true')
    group.add_argument('--dim640x480', action='store_true')

    args = parser.parse_args()
    return args


def run():
    """Run the program."""
    args = parse_arguments()
    temp_path = create_temp_dir(args.temp_dir)

    session = get_cas_session(CAS_URL, args.url, USERNAME, PASSWORD)

    manifest = download_manifest(session, args.url, temp_path)
    original_video_file_path = os.path.join(temp_path, 'original.mp4')
    jpg_frames_file_path = os.path.join(temp_path, 'jpg_frames.txt')

    video_url, duration_ms, trans_times_ms, slide_info = get_slide_info(
            manifest, temp_path)

    download_video(session, video_url, original_video_file_path)
    download_jpgs(session, slide_info)
    write_jpgs_list(trans_times_ms, slide_info, args.slides_fps,
                    jpg_frames_file_path)

    if args.dim1024x768:
        vf_dimensions = '-vf scale=1024:768'
    elif args.dim800x600:
        vf_dimensions = '-vf scale=800:600'
    else:
        vf_dimensions = '-vf scale=640:480'
    output_file = os.path.abspath(args.output_file)
    create_output_video(args.mencoder_loc, original_video_file_path,
                        jpg_frames_file_path, duration_ms, args.slides_fps,
                        vf_dimensions, output_file)


if __name__ == '__main__':
    run()

