# Chunk Norris
#
# A Python script to do chunked encoding using an AV1 or x265 CLI encoder.
#
# Set common parameters in default_params and add/edit the presets as needed.

import argparse
import configparser
import copy
import json
import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import warnings
import ffmpeg
import numpy as np
import io
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
from datetime import datetime
from tqdm import tqdm

warnings.filterwarnings("ignore", message="The value of the smallest subnormal.*")
interrupted = False
active_processes = []
process_list_lock = threading.Lock()
butter_scores_pass1 = []
butter_scores_pass2 = []
chunk_cvvdp_scores = []
average_qadjust_pass1 = 0.0
avg_bitrate = 0.0
correction_factor = 0.0
metrics_plugin = 0

logger = logging.getLogger("chunknorris")
logger.propagate = False
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Buffer in memory until file is known
buffer = io.StringIO()
buffer_handler = logging.StreamHandler(buffer)
buffer_handler.setFormatter(formatter)
logger.addHandler(buffer_handler)
logger.info("Process started.")


def silence_worker():
    devnull = open(os.devnull, "w")
    os.dup2(devnull.fileno(), 1)  # stdout
    os.dup2(devnull.fileno(), 2)  # stderr


def analyze_butteraugli_chunk_wrapper(args):
    return analyze_butteraugli_chunk(*args)


def analyze_ssimu2_chunk_wrapper(args):
    return analyze_ssimu2_chunk(*args)


def analyze_cvvdp_chunk_wrapper(args):
    return analyze_cvvdp_chunk(*args)


def get_video_props(video_path):
    probe = ffmpeg.probe(video_path, v='error')
    video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
    if video_stream:
        video_width = int(video_stream['width'])
        video_height = int(video_stream['height'])
        video_length = int(video_stream['nb_frames'])
        video_framerate = str(video_stream['r_frame_rate'])
        num, denom = map(int, video_framerate.split('/'))
        fr = float(num/denom)
        video_framerate = int(math.ceil(num/denom))
        try:
            detected_video_transfer = str(video_stream['color_transfer'])
            transfer_map = {
                "bt709": 1,
                "bt470m": 4,
                "bt470bg": 5,
                "smpte170m": 6,
                "smpte240m": 7,
                "linear": 8,
                "log100": 9,
                "log_sqrt": 10,
                "iec61966-2-4": 11,
                "bt1361e": 12,
                "srgb": 13,
                "bt2020-10": 14,
                "bt2020-12": 15,
                "smpte2084": 16,
                "smpte428": 17,
                "arib-std-b67": 18
            }
            video_transfer = transfer_map.get(detected_video_transfer.lower())
            if not isinstance(video_transfer, int):
                raise ValueError(f"Invalid or unknown transfer characteristics {detected_video_transfer}, please check the transfer_map conversion table in the script.")
            print(f"Setting transfer characteristics based on autodetection to {detected_video_transfer}.")
            logger.info(f"Detected transfer characteristics is \"{detected_video_transfer}\".")
        except Exception as e:
            logger.info(f"Could not detect the source transfer characteristics. Exception code {e}")
            if video_width > 1920:
                video_transfer = 16
                print("Setting transfer characteristics manually to \"smpte2084\" (HDR).")
                logger.info("Setting transfer characteristics manually to \"smpte2084\" (HDR).")
            else:
                video_transfer = 1
                print("Setting transfer characteristics manually to \"bt709\" (SDR).")
                logger.info("Setting transfer characteristics manually to \"bt709\" (SDR).")

        try:
            detected_video_primaries = str(video_stream['color_primaries'])
            primaries_map = {
                "bt709": 1,
                "bt470m": 4,
                "bt470bg": 5,
                "smpte170m": 6,
                "smpte240m": 7,
                "film": 8,
                "bt2020": 9,
                "smpte431": 11,
                "smpte432": 12,
                "ebu3213": 13
            }
            video_primaries = primaries_map.get(detected_video_primaries.lower())
            if not isinstance(video_primaries, int):
                raise ValueError(f"Invalid or unknown color primaries {detected_video_primaries}, please check the primaries_map conversion table in the script.")
            print(f"Setting color primaries based on autodetection to {detected_video_primaries}.")
            logger.info(f"Detected color primaries is \"{detected_video_primaries}\".")
        except Exception as e:
            logger.info(f"Could not detect the source color primaries characteristics. Exception code {e}")
            if video_width > 1920:
                video_primaries = 9
                print("Setting color primaries to \"bt2020\" (HDR).")
                logger.info("Setting color primaries manually to \"bt2020\" (HDR).")
            else:
                video_primaries = 1
                print("Setting color primaries manually to \"bt709\" (SDR).")
                logger.info("Setting color primaries manually to \"bt709\" (SDR).")

        try:
            detected_video_matrix = str(video_stream['color_space'])
            matrix_map = {
                "bt709": 1,
                "fcc": 4,
                "bt470bg": 5,
                "smpte170m": 6,
                "smpte240m": 7,
                "ycgco": 8,
                "bt2020nc": 9
            }
            video_matrix = matrix_map.get(detected_video_matrix.lower())
            if not isinstance(video_matrix, int):
                raise ValueError(f"Invalid or unknown colorspace {detected_video_matrix}, please check the matrix_map conversion table in the script.")
            print(f"Setting colorspace based on autodetection to {detected_video_matrix}.")
            logger.info(f"Detected colorspace is \"{detected_video_matrix}\".")
        except Exception as e:
            logger.info(f"Could not detect the source colorspace. Exception code {e}")
            if video_transfer == 16:
                video_matrix = 9
                print("Setting colorspace manually to \"bt2020nc\" (HDR).")
                logger.info("Setting colorspace manually to \"bt2020nc\" (HDR).")
            else:
                video_matrix = 1
                print("Setting colorspace manually to \"bt709\" (SDR).")
                logger.info("Setting colorspace manually to \"bt709\" (SDR).")

        try:
            detected_chroma_location = str(video_stream['chroma_location'])
            chroma_location_map = {
                "left": 0,
                "center": 1,
                "topleft": 2
            }
            chroma_location = chroma_location_map.get(detected_chroma_location.lower())
            if not isinstance(chroma_location, int):
                raise ValueError(f"Invalid or unknown colorspace {detected_chroma_location}, please check the chroma_location_map conversion table in the script.")
            print(f"Setting chroma location based on autodetection to {detected_chroma_location}.")
            logger.info(f"Detected chroma location is \"{detected_chroma_location}\".")
        except Exception as e:
            logger.info(f"Could not detect the source chroma location. Exception code {e}")
            if video_transfer == 16:
                chroma_location = 2
                print("Setting chroma location manually to \"top left (2)\" (HDR).")
                logger.info("Setting chroma location manually to \"top left (2)\" (HDR).")
            else:
                chroma_location = 0
                print("Setting chroma location manually to \"left (0)\" (SDR).")
                logger.info("Setting chroma location manually to \"left (0)\" (SDR).")

        return video_width, video_height, video_length, video_transfer, video_matrix, video_primaries, video_framerate, fr, chroma_location
    else:
        print("No video stream found in the input video.")
        sys.exit(1)


# Function to clean up a folder
def clean_folder(folder):
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
        if os.path.isfile(item_path):
            os.unlink(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)


def clean_files(folder, pattern):
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
        if os.path.isfile(item_path) and item.startswith(pattern):
            os.unlink(item_path)


# Define a function to extract sections from the baseline grain table file
def extract_sections(filename):
    sections = []
    current_section = []
    section_number = 0  # Track the current section number

    with open(filename, 'r') as file:
        for line in file:
            if line.startswith('E'):
                # If we encounter a line starting with 'E', it's the start of a new section
                current_section = [line]
                section_number += 1  # Increment section number
                # print(f"Processing section {section_number}...")
            elif current_section:
                # If we are in a section, add the line to the current section
                current_section.append(line)

            if len(current_section) == 8:  # Each section has 8 lines, including the header
                sections.append(current_section)
                # length = timestamp_difference(current_section)
                # print (length)
                current_section = []

    if not sections:
        print("No valid sections found in the file.")
    return sections


# Define a function to calculate the timestamp difference for a section in the baseline grain table
def timestamp_difference(section):
    start_timestamp = int(section[0].split()[1])
    end_timestamp = int(section[0].split()[2])
    return end_timestamp - start_timestamp


def create_scxvid_file(scene_change_csv, scd_tonemap, encode_script, cudasynth, downscale_scd, scd_script, scene_change_file_path):
    with open(encode_script, 'r') as file:
        lines = file.readlines()
    source = lines[0]
    if scd_tonemap != 0 and cudasynth and "dgsource" in source.lower():
        source = source.replace(".dgi\",", ".dgi\",h2s_enable=1,")
        source = source.replace(".dgi\")", ".dgi\",h2s_enable=1)")
    cropping = next((line for line in lines if line.strip().lower().startswith('crop(')), None)

    with open(scd_script, 'w') as scd_file:
        # Write the first line content to the new file
        scd_file.write(source)
        if cropping:
            scd_file.write('\n')
            scd_file.write(cropping)
            scd_file.write('\n')
        if downscale_scd > 1:
            scd_file.write('\n')
            scd_file.write('try {\n')
            scd_file.write(f'Spline36ResizeMT(width()/{downscale_scd},height()/{downscale_scd})\n')
            scd_file.write('}\n')
            scd_file.write('catch(err) {\n')
            scd_file.write(f'Spline36Resize(width()/{downscale_scd},height()/{downscale_scd})\n')
            scd_file.write('}\n')
        if scd_tonemap != 0 and not cudasynth:
            scd_file.write('\nConvertBits(16).DGHDRtoSDR(gamma=1/2.4)\n')
        scd_file.write('ConvertBits(8)\n')
        scd_file.write(f'SCXvid(log="{scene_change_csv}")')

    scene_change_command = [
        "ffmpeg",
        "-i", scd_script,
        "-loglevel", "warning",
        "-an", "-f", "null", "NUL"
    ]

    start_time = datetime.now()
    print("Detecting scene changes using SCXviD.\n")
    logger.info("Detecting scene changes using SCXviD.")
    scd_process = subprocess.Popen(scene_change_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    scd_process.communicate()
    if scd_process.returncode != 0:
        logger.error(f"Error in scene change detection phase, return code: {scd_process.returncode}")
        print("Error in scene change detection phase, return code:", scd_process.returncode)
        sys.exit(1)
    end_time = datetime.now()
    scd_time = end_time - start_time
    logger.info(f"Scene change detection done, duration {scd_time}.")

    print("Converting logfile to QP file format.\n")

    # Read the input file
    with open(scene_change_csv, "r") as file:
        scdxvid_data = file.readlines()

    # Initialize variables
    output_lines = []
    current_line_number = -3  # The actual data starts from line 4, so this ensures the first scene change has frame number 0.

    # Iterate through the lines
    for line in scdxvid_data:
        # Split the line into tokens
        tokens = line.strip().split()

        # Check if the line starts with 'i'
        if tokens and tokens[0] == 'i':
            # Append the current line number and 'I' to the output
            output_lines.append(f"{current_line_number} I")
        current_line_number += 1

    # Join the output lines
    scenechangelist = '\n'.join(output_lines)

    # Write the output to a new file or print it
    qpfile = os.path.splitext(os.path.basename(encode_script))[0] + ".qp.txt"
    qpfile = os.path.join(scene_change_file_path, qpfile)
    with open(qpfile, "w") as file:
        file.write(scenechangelist)


# Function to find the scene change file recursively
def find_scene_change_file(start_dir, filename):
    for root, dirs, files in os.walk(start_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None


def create_avscenechange_file(scene_change_csv, encode_script, scd_script, scene_change_file_path, downscale_scd):
    if not os.path.exists(scd_script):
        print(f"Scene change analysis script not found: {scd_script}, created manually.\n")
        with open(encode_script, 'r') as file:
            lines = file.readlines()
        source = lines[0]
        cropping = next((line for line in lines if line.strip().lower().startswith('crop(')), None)
        with open(scd_script, 'w') as scd_file:
            # Write the first line content to the new file
            scd_file.write(source)
            if cropping:
                scd_file.write('\n')
                scd_file.write(cropping)
                scd_file.write('\n')
            if downscale_scd > 1:
                scd_file.write('try {')
                scd_file.write(f'\nBicubicResizeMT((width)/{downscale_scd},(height)/{downscale_scd},b=-0.5,c=0.25)\n')
                scd_file.write('}\n')
                scd_file.write('catch(err) {')
                scd_file.write(f'\nBicubicResize((width)/{downscale_scd},(height)/{downscale_scd},b=-0.5,c=0.25)\n')
                scd_file.write('}\n')
            scd_file.write('BitsPerComponent() > 10 ? ConvertBits(10) : last')

    decode_command_avscd = [
        "ffmpeg.exe",
        "-loglevel", "fatal",
        "-i", '"' + scd_script + '"',
        "-f", "yuv4mpegpipe",
        "-strict", "-1",
        "-"
    ]
    avscd_command = [
        "av-scenechange",
        "-",
        "-o", scene_change_csv
    ]
    decode_command_avscd = ' '.join(decode_command_avscd)
    avscd_command = ' '.join(avscd_command)
    scene_change_command = decode_command_avscd + " | " + avscd_command
    start_time = datetime.now()
    print("Detecting scene changes using av-scenechange.\n")
    logger.info("Detecting scene changes using av-scenechange.")
    scd_process = subprocess.Popen(scene_change_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    scd_process.communicate()
    if scd_process.returncode != 0:
        logger.error(f"Error in scene change detection phase, return code: {scd_process.returncode}")
        print("Error in scene change detection phase, return code:", scd_process.returncode)
        sys.exit(1)
    end_time = datetime.now()
    scd_time = end_time - start_time
    logger.info(f"Scene change detection done, duration {scd_time}.")
    print("Converting logfile to QP file format.\n")

    with open(scene_change_csv, "r") as file:
        scd_data = json.load(file)
    scene_changes = scd_data.get("scene_changes", [])
    output_lines = [f"{frame} I" for frame in scene_changes]
    scenechangelist = '\n'.join(output_lines)

    qpfile = os.path.splitext(os.path.basename(encode_script))[0] + ".qp.txt"
    qpfile = os.path.join(scene_change_file_path, qpfile)
    with open(qpfile, "w") as file:
        file.write(scenechangelist)


def create_fgs_table(encode_params, output_grain_table, scripts_folder, video_width, encode_script, graintable_sat, decode_method, encoder, threads, cpu, graintable_cpu, output_grain_file_encoded, output_grain_file_lossless,
                     output_grain_table_baseline):
    # Create the grain table only if it doesn't exist already
    if not os.path.exists(output_grain_table):
        grain_script = os.path.join(scripts_folder, f"grainscript.avs")
        referencefile_start_frame = input("Please enter the first frame of FGS grain table process: ")
        referencefile_end_frame = input("Please enter the last frame of FGS grain table process (default 5 seconds of frames if empty) : ")

        # Check the need to pad the video because grav1synth :/
        # This check and workaround currently supports resolutions only up to 4K
        padleft = 0
        padright = 0
        if 3584 < video_width < 3840:
            padleft = (3840 - video_width) / 2
            if padleft % 2 != 0:
                padleft += 1
            padright = 3840 - video_width - padleft
        elif 2560 < video_width < 3584:
            padleft = (3584 - video_width) / 2
            if padleft % 2 != 0:
                padleft += 1
            padright = 3584 - video_width - padleft
        elif 1920 < video_width < 2560:
            padleft = (2560 - video_width) / 2
            if padleft % 2 != 0:
                padleft += 1
            padright = 2560 - video_width - padleft
        elif 1480 < video_width < 1920:
            padleft = (1920 - video_width) / 2
            if padleft % 2 != 0:
                padleft += 1
            padright = 1920 - video_width - padleft
        elif 1280 < video_width < 1480:
            padleft = (1480 - video_width) / 2
            if padleft % 2 != 0:
                padleft += 1
            padright = 1480 - video_width - padleft
        elif video_width < 1280:
            padleft = (1280 - video_width) / 2
            if padleft % 2 != 0:
                padleft += 1
            padright = 1280 - video_width - padleft

        print("\nVideo width:", video_width)
        print("Padding left:", padleft)
        print("Padding right:", padright)
        print("Final width:", video_width + padleft + padright)

        with open(grain_script, 'w') as grain_file:
            grain_file.write(f'Import("{encode_script}")\n')
            if referencefile_end_frame != '':
                grain_file.write(f'Trim({referencefile_start_frame}, {referencefile_end_frame})\n')
                if graintable_sat < 1.0:
                    grain_file.write(f'Tweak(sat={graintable_sat})\n')
                grain_file.write('ConvertBits(10)\n')
                grain_file.write(f'AddBorders({int(padleft)},0,{int(padright)},0)')
            else:
                grain_file.write('grain_frame_rate = Ceil(FrameRate())\n')
                grain_file.write(f'grain_end_frame = {referencefile_start_frame} + (grain_frame_rate * 5)\n')
                grain_file.write(f'Trim({referencefile_start_frame}, grain_end_frame)\n')
                if graintable_sat < 1.0:
                    grain_file.write(f'Tweak(sat={graintable_sat})\n')
                grain_file.write('ConvertBits(10)\n')
                grain_file.write(f'AddBorders({int(padleft)},0,{int(padright)},0)')

        # Create the encoding command lines
        if decode_method == 0:
            decode_command_grain = [
                "avs2yuv64.exe",
                "-no-mt",
                '"' + grain_script + '"',
                "-"
            ]
        else:
            decode_command_grain = [
                "ffmpeg.exe",
                "-loglevel", "fatal",
                "-i", '"' + grain_script + '"',
                "-f", "yuv4mpegpipe",
                "-strict", "-1",
                "-"
            ]

        if encoder == 'rav1e':
            encode_params_grain = [x.replace(f'--threads {threads}', '--threads 0').replace(f'--speed {cpu}', f'--speed {graintable_cpu}') for x in encode_params]
            enc_command_grain = [
                "rav1e.exe",
                *encode_params_grain,
                "-o", '"' + output_grain_file_encoded + '"',
                "-"
            ]
        elif encoder == 'svt':
            encode_params_grain = [x.replace(f'--lp {threads}', '--lp 0').replace(f'--preset {cpu}', f'--preset {graintable_cpu}') for x in encode_params]
            enc_command_grain = [
                "svtav1encapp.exe",
                *encode_params_grain,
                "-b", '"' + output_grain_file_encoded + '"',
                "-i -"
            ]
        else:
            encode_params_grain = [x.replace(f'--cpu-used={cpu}', f'--cpu-used={graintable_cpu}').replace(f'--threads={threads}', f'--threads={os.cpu_count()}') for x in encode_params]
            enc_command_grain = [
                "aomenc.exe",
                "--ivf",
                *encode_params_grain,
                "--passes=1",
                "-o", '"' + output_grain_file_encoded + '"',
                "-"
            ]
        ffmpeg_command_grain = [
            "ffmpeg.exe",
            "-i", grain_script,
            "-y",
            "-loglevel", "fatal",
            "-c:v", "ffv1",
            "-pix_fmt", "yuv420p10le",
            output_grain_file_lossless
        ]

        # Create the command line to compare the original and encoded files to get the grain table
        grav1synth_command = [
            "grav1synth.exe",
            "diff",
            "-o", output_grain_table_baseline,
            output_grain_file_lossless,
            output_grain_file_encoded
        ]

        # print (avs2yuv_command_grain, enc_command_grain)
        decode_command_grain = ' '.join(decode_command_grain)
        enc_command_grain = ' '.join(enc_command_grain)
        enc_command_grain = decode_command_grain + ' | ' + enc_command_grain

        print("Encoding the FGS analysis AV1 file.")
        enc_process_grain = subprocess.Popen(enc_command_grain, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        enc_process_grain.communicate()
        if enc_process_grain.returncode != 0:
            logger.error(f"Error in FGS analysis encoder processing, return code: {enc_process_grain.returncode}")
            print("Error in FGS analysis encoder processing, return code:", enc_process_grain.returncode)
            sys.exit(1)

        print("Encoding the FGS analysis lossless file.")
        enc_process_grain_ffmpeg = subprocess.Popen(ffmpeg_command_grain, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        enc_process_grain_ffmpeg.communicate()
        if enc_process_grain_ffmpeg.returncode != 0:
            logger.error(f"Error in FGS analysis lossless file processing, return code: {enc_process_grain_ffmpeg.returncode}")
            print("Error in FGS analysis lossless file processing, return code:", enc_process_grain_ffmpeg.returncode)
            sys.exit(1)

        print("Creating the FGS grain table file.\n")
        enc_process_grain_grav = subprocess.Popen(grav1synth_command, shell=True)
        enc_process_grain_grav.communicate()
        if enc_process_grain_grav.returncode != 0:
            logger.error(f"Error in grav1synth process, return code: {enc_process_grain_grav.returncode}")
            print("Error in grav1synth process, return code:", enc_process_grain_grav.returncode)
            sys.exit(1)
        sections = extract_sections(output_grain_table_baseline)

        if len(sections) == 1:
            single_section = sections[0]

            # Print the single section to the output file
            print("\nFGS table (only one FGS section found) :")
            with open(output_grain_table, 'w', newline='\n') as output_file:
                print("filmgrn1", file=output_file)
                for line in single_section:
                    print(line, end='')  # Print to the console
                    print(line, end='', file=output_file)  # Print to the output file
            print("\n")
        elif len(sections) >= 2:
            # Find the second-longest section based on timestamp difference

            # Sort sections by timestamp difference in descending order
            sorted_sections = sorted(sections, key=timestamp_difference, reverse=True)

            # The second-longest section is at index 1 (index 0 is the longest)
            second_longest_section = sorted_sections[1]

            # Replace the header with one from the first section
            second_longest_section[0] = sections[0][0]

            # Replace the end timestamp with 9223372036854775807
            second_longest_section[0] = second_longest_section[0].replace(
                second_longest_section[0].split()[2], '9223372036854775807'
            )

            with open(output_grain_table, 'w', newline='\n') as output_file:
                print("filmgrn1", file=output_file)
                print("\nFGS table:")
                for line in second_longest_section:
                    print(line, end='')  # Print to the console
                    print(line, end='', file=output_file)  # Print to the output file
    else:
        print("The FGS grain table file exists already, skipping creation.\n")


def convert_qp_to_scene_changes(encode_script):
    scene_changes = []
    # Find the scene change file recursively
    scene_change_filename = os.path.splitext(os.path.basename(encode_script))[0] + ".qp.txt"
    scene_change_file_path = os.path.dirname(encode_script)
    scene_change_file_path = find_scene_change_file(scene_change_file_path, scene_change_filename)

    if scene_change_file_path is None:
        logger.error(f"Scene change file not found: {scene_change_filename}")
        print(f"Scene change file not found: {scene_change_filename}")
        sys.exit(1)

    # Read scene changes from the file
    with open(scene_change_file_path, "r") as scene_change_file:
        for line in scene_change_file:
            parts = line.strip().split()
            if len(parts) == 2:
                start_frame = int(parts[0])
                scene_changes.append(start_frame)
    print("Read scene changes from QP file.\n")

    return scene_changes


def adjust_chunkdata(chunkdata_list, credits_start_frame, min_chunk_length, q, credits_q):
    adjusted_chunkdata_list = []

    last_frame = chunkdata_list[-1]['end']

    for i, chunkdata in enumerate(chunkdata_list):
        if chunkdata['start'] <= credits_start_frame <= chunkdata['end']:
            # Update the end frame of the chunk where credits start
            adjusted_length = credits_start_frame - chunkdata['start']
            chunkdata_list[i]['end'] = credits_start_frame - 1
            chunkdata_list[i]['length'] = adjusted_length
            break
        elif i == len(chunkdata_list) - 1 and credits_start_frame == chunkdata['end'] + 1:
            # Credits start at the end of the last chunk
            adjusted_chunk = {
                'chunk': chunkdata['chunk'],
                'length': credits_start_frame - chunkdata['start'],
                'start': chunkdata['start'],
                'end': credits_start_frame - 1,
                'credits': 0,
                'q': q
            }
            adjusted_chunkdata_list.append(adjusted_chunk)
            break

    # Remove chunks that start after the credits start frame
    chunkdata_list = [chunkdata for chunkdata in chunkdata_list if chunkdata['start'] <= credits_start_frame]

    # Check if the last chunk before the credits is too short
    if len(chunkdata_list) > 1 and chunkdata_list[-1]['length'] < min_chunk_length:
        # Merge the last chunk with the second last chunk
        chunkdata_list[-2]['end'] = chunkdata_list[-1]['end']
        chunkdata_list[-2]['length'] = chunkdata_list[-2]['end'] - chunkdata_list[-2]['start'] + 1
        chunkdata_list.pop()

    # Create a new chunk for the credits
    credits_chunk = {
        'chunk': len(chunkdata_list) + 1,
        'length': last_frame - credits_start_frame + 1,
        'start': credits_start_frame,
        'end': last_frame,
        'credits': 1,
        'q': credits_q
    }

    # Append the adjusted chunks to the list
    adjusted_chunkdata_list = chunkdata_list + [credits_chunk]

    # Update the length of each chunk in the adjusted_chunkdata_list
    for chunkdata in adjusted_chunkdata_list:
        chunkdata['length'] = chunkdata['end'] - chunkdata['start'] + 1

    return adjusted_chunkdata_list


def preprocess_chunks(encode_commands, input_files, chunklist, qadjust_cycle, stored_encode_params, scene_changes, video_length, credits_start_frame, min_chunk_length, q, credits_q, encoder, chunks_folder, rpu, qadjust_cpu, encode_script,
                      qadjust_original_file, video_width, video_height, qadjust_b, qadjust_c, scripts_folder, decode_method, cpu, credits_cpu, qadjust_reuse):
    encode_params_original = stored_encode_params.copy()
    enc_command = []
    encode_params = []
    if qadjust_cycle in (-1, 1):
        chunk_number = 1
        i = 0
        combined = False
        next_scene_index = None
        while i < len(scene_changes):
            start_frame = scene_changes[i]
            if i < len(scene_changes) - 1:
                end_frame = scene_changes[i + 1] - 1
            else:
                end_frame = video_length - 1
            # print(i,start_frame,end_frame)

            # Check if the current scene is too short
            if end_frame - start_frame + 1 < min_chunk_length:
                next_scene_index = i + 2
                combined = True

                # Combine scenes until the chunk length is at least min_chunk_length
                while next_scene_index < len(scene_changes):
                    end_frame = scene_changes[next_scene_index] - 1
                    chunk_length = end_frame - start_frame + 1

                    if chunk_length >= min_chunk_length:
                        break  # The combined chunk is long enough
                    else:
                        next_scene_index += 1  # Move to the next scene

                if next_scene_index == len(scene_changes):
                    # No more scenes left to combine
                    end_frame = video_length - 1  # Set end_frame based on the total video length for the last scene (Avisynth counts from 0, hence the minus one)
                # print(f'Next scene index: {next_scene_index}')
            chunk_length = end_frame - start_frame + 1
            # chunk_length = 999999 if chunk_length < 0 else chunk_length

            chunkdata = {
                'chunk': chunk_number, 'length': chunk_length, 'start': start_frame, 'end': end_frame, 'credits': 0, 'q': q
            }
            chunklist.append(chunkdata)

            chunk_number += 1

            if combined:
                i = next_scene_index
                combined = False
            else:
                i += 1

        if credits_start_frame:
            chunklist = adjust_chunkdata(chunklist, credits_start_frame, min_chunk_length, q, credits_q)

    if qadjust_cycle == 2:
        chunklist = sorted(chunklist, key=lambda x: x['chunk'], reverse=False)

    for i in chunklist:
        if qadjust_cycle == 1 and i['credits'] == 1:
            continue
        if encoder != 'x265':
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.ivf")
        else:
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.hevc")
        input_files.append(output_chunk)  # Add the input file for concatenation

    if qadjust_cycle != 1 and not (qadjust_cycle == -1 and qadjust_reuse):
        if rpu and encoder in ('svt', 'x265'):
            chunklist_length = len(chunklist)
            print("Splitting the RPU file based on chunks.\n")
            logger.info("Splitting the RPU file.")
            # Use ThreadPoolExecutor for multithreading
            with ThreadPoolExecutor(max_workers=int(os.cpu_count()/2)) as executor:
                # Submit each chunk to the executor
                futures = [executor.submit(process_rpu, i, chunklist_length, video_length, scripts_folder, chunks_folder, rpu) for i in chunklist]
                # Wait for all threads to complete
                for future in futures:
                    future.result()

    chunklist = sorted(chunklist, key=lambda x: x['length'], reverse=True)

    if qadjust_cycle == 1:
        if encoder == 'svt':
            replacements_list = {'--film-grain ': '--film-grain 0',
                                 '--preset ': f'--preset {qadjust_cpu}',
                                 '--tile-columns ': '--tile-columns 1',
                                 '--tile-rows ': '--tile-rows 0',
                                 '--enable-dlf ': '--enable-dlf 2',
                                 '--complex-hvs ': '--complex-hvs 0'}
            encode_params_original = [
                next((replacements_list[key] for key in replacements_list if x.startswith(key)), x)
                for x in encode_params_original
            ]
            if not any('--tile-columns' in param for param in encode_params_original):
                encode_params_original.append('--tile-columns 1')
            if not any('--tile-rows' in param for param in encode_params_original):
                encode_params_original.append('--tile-rows 0')
            if not any('--enable-dlf' in param for param in encode_params_original):
                encode_params_original.append('--enable-dlf 2')
            if not any('--complex-hvs' in param for param in encode_params_original):
                encode_params_original.append('--complex-hvs 0')

        else:
            replacements_list = {'--preset ': '--preset veryfast',
                                 '--limit-refs ': '--limit-refs 3',
                                 '--rdoq-level ': '--rdoq-level 2',
                                 '--rc-lookahead ': '--rc-lookahead 40',
                                 '--lookahead-slices ': '--lookahead-slices 0',
                                 '--subme ': '--subme 3',
                                 '--me ': '--me hex',
                                 '--merange ': '--merange 25',
                                 '--hme': '--no-hme',
                                 '--ref ': '--ref 3',
                                 '--b-adapt ': '--b-adapt 2'}
            encode_params_original = [
                next((replacements_list[key] for key in replacements_list if x.startswith(key)), x)
                for x in encode_params_original
            ]
            if not any('--preset' in param for param in encode_params_original):
                encode_params_original.append('--preset veryfast')
            if not any('--rdoq-level' in param for param in encode_params_original):
                encode_params_original.append('--rdoq-level 2')
            if not any('--rc-lookahead' in param for param in encode_params_original):
                encode_params_original.append('--rc-lookahead 40')
            if not any('--lookahead-slices' in param for param in encode_params_original):
                encode_params_original.append('--lookahead-slices 0')
            if not any('--b-adapt' in param for param in encode_params_original):
                encode_params_original.append('--b-adapt 2')
            if not any('--me ' in param for param in encode_params_original):
                encode_params_original.append('--me hex')
            if not any('--merange ' in param for param in encode_params_original):
                encode_params_original.append('--merange 25')

        with open(encode_script, 'r') as file:
            lines = file.readlines()
        source = lines[0]
        cropping = next((line for line in lines if line.strip().lower().startswith('crop(')), None)

        with open(qadjust_original_file, "w") as qadjust_script:
            qadjust_script.write(source)
            if cropping:
                qadjust_script.write(cropping)
                qadjust_script.write('\n')
            qadjust_script.write('try {\n')
            qadjust_script.write(f'BicubicResizeMT({video_width},{video_height},b={qadjust_b},c={qadjust_c})\n')
            qadjust_script.write('}\n')
            qadjust_script.write('catch(err) {\n')
            qadjust_script.write(f'BicubicResize({video_width},{video_height},b={qadjust_b},c={qadjust_c})\n')
            qadjust_script.write('}\n')
            qadjust_script.write('ConvertBits(10)')
    for i in chunklist:
        if qadjust_cycle == 1 and i['credits'] == 1:
            continue
        encode_params = copy.deepcopy(encode_params_original)
        if encoder in ('svt', 'x265'):
            encode_params.append(f'--crf {i['q']}')
        if rpu and encoder in ('svt', 'x265') and qadjust_cycle != 1:
            rpupath = os.path.join(chunks_folder, f"scene_{i['chunk']}_rpu.bin")
            encode_params.append(f'--dolby-vision-rpu {rpupath}')
        scene_script_file = os.path.join(scripts_folder, f"scene_{i['chunk']}.avs")
        if encoder != 'x265':
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.ivf")
        else:
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.hevc")
        # Create the Avisynth script for this scene
        if qadjust_cycle != 1:
            with open(scene_script_file, "w") as scene_script:
                scene_script.write(f'Import("{encode_script}")\n')
                scene_script.write(f"Trim({i['start']}, {i['end']})\n")
                if encoder != 'x265':
                    scene_script.write('ConvertBits(10)')
        else:
            with open(scene_script_file, 'w') as scene_script:
                # scene_script.write(source)
                scene_script.write(f'Import("qadjust_original.avs")\n')
                scene_script.write(f"Trim({i['start']}, {i['end']})\n")

        if decode_method == 0:
            decode_command = [
                "avs2yuv64.exe",
                "-no-mt",
                '"'+scene_script_file+'"',  # Use the Avisynth script for this scene
                "-"
            ]
        else:
            decode_command = [
                "ffmpeg.exe",
                "-loglevel", "fatal",
                "-i", '"'+scene_script_file+'"',
                "-f", "yuv4mpegpipe",
                "-strict", "-1",
                "-"
            ]

        if encoder == 'rav1e':
            if i['credits'] == 0:
                enc_command = [
                    "rav1e.exe",
                    *encode_params,
                    "-q",
                    "-o", '"' + output_chunk + '"',
                    "-"
                ]
            else:
                encode_params = [x.replace(f'--speed {cpu}', f'--speed {credits_cpu}').replace(f'--quantizer {q}', f'--quantizer {credits_q}') for x in encode_params]
                enc_command = [
                    "rav1e.exe",
                    *encode_params,
                    "-q",
                    "-o", '"' + output_chunk + '"',
                    "-"
                ]
        elif encoder == 'svt':
            if i['credits'] == 0:
                enc_command = [
                    "svtav1encapp.exe",
                    *encode_params,
                    "-b", '"'+output_chunk+'"',
                    "-i -"
                ]
            elif qadjust_cycle != 1:
                encode_params = [x.replace(f'--preset {cpu}', f'--preset {credits_cpu}').replace(f'--crf {q}', f'--crf {credits_q}') for x in encode_params]
                enc_command = [
                    "svtav1encapp.exe",
                    *encode_params,
                    "-b", '"'+output_chunk+'"',
                    "-i -"
                ]
        elif encoder == 'aom':
            if i['credits'] == 0:
                enc_command = [
                    "aomenc.exe",
                    "-q",
                    "--ivf",
                    *encode_params,
                    "--passes=1",
                    "-o", '"'+output_chunk+'"',
                    "-"
                ]
            else:
                encode_params = [x.replace(f'--cpu-used={cpu}', f'--cpu-used={credits_cpu}').replace(f'--cq-level={q}', f'--cq-level={credits_q}') for x in encode_params]
                enc_command = [
                    "aomenc.exe",
                    "-q",
                    "--ivf",
                    *encode_params,
                    "--passes=1",
                    "-o", '"' + output_chunk + '"',
                    "-"
                ]
        else:
            if i['credits'] == 0:
                enc_command = [
                    "x265.exe",
                    "--y4m",
                    "--no-progress",
                    f'--frames {i['length']}',
                    *encode_params,
                    "--output", '"'+output_chunk+'"',
                    "--input", "-"
                ]
            else:
                encode_params = [x.replace(f'--crf {q}', f'--crf {credits_q}') for x in encode_params]
                enc_command = [
                    "x265.exe",
                    "--y4m",
                    "--no-progress",
                    f'--frames {i['length']}',
                    *encode_params,
                    "--output", '"' + output_chunk + '"',
                    "--input", "-"
                ]

        encode_commands.append((decode_command, enc_command, output_chunk))
    # print (encode_commands)
    chunklist_dict = {chunk_dict['chunk']: chunk_dict['length'] for chunk_dict in chunklist}
    # for i in chunklist:
    # print(i['chunk'], i['length'], i['q'])
    if qadjust_cycle in (-1, 1):
        logger.info(f"Total {len(chunklist)} chunks created.")
    return encode_commands, input_files, chunklist, chunklist_dict, encode_params


def preprocess_probe_chunks(stored_encode_params, video_length, credits_start_frame, encoder, chunks_folder, qadjust_cpu, encode_script, qadjust_original_file, video_width, video_height, qadjust_b,
                            qadjust_c, scripts_folder, decode_method, minkeyint):
    encode_params_original = stored_encode_params.copy()
    encode_params = []
    probe_encode_commands = []
    probe_chunklist = []

    if encoder == 'svt':
        replacements_list = {'--film-grain ': '--film-grain 0',
                             '--preset ': f'--preset {qadjust_cpu}',
                             '--tile-columns ': '--tile-columns 1',
                             '--tile-rows ': '--tile-rows 0',
                             '--enable-dlf ': '--enable-dlf 2',
                             '--complex-hvs ': '--complex-hvs 0'}
        encode_params_original = [
            next((replacements_list[key] for key in replacements_list if x.startswith(key)), x)
            for x in encode_params_original
        ]
        if not any('--tile-columns' in param for param in encode_params_original):
            encode_params_original.append('--tile-columns 1')
        if not any('--tile-rows' in param for param in encode_params_original):
            encode_params_original.append('--tile-rows 0')
        if not any('--enable-dlf' in param for param in encode_params_original):
            encode_params_original.append('--enable-dlf 2')
        if not any('--complex-hvs' in param for param in encode_params_original):
            encode_params_original.append('--complex-hvs 0')

    else:
        replacements_list = {'--preset ': '--preset veryfast',
                             '--limit-refs ': '--limit-refs 3',
                             '--rdoq-level ': '--rdoq-level 2',
                             '--rc-lookahead ': '--rc-lookahead 40',
                             '--lookahead-slices ': '--lookahead-slices 0',
                             '--subme ': '--subme 3',
                             '--me ': '--me hex',
                             '--merange ': '--merange 25',
                             '--hme ': '--no-hme',
                             '--ref ': '--ref 3',
                             '--b-adapt ': '--b-adapt 2'}
        encode_params_original = [
            next((replacements_list[key] for key in replacements_list if x.startswith(key)), x)
            for x in encode_params_original
        ]
        if not any('--preset' in param for param in encode_params_original):
            encode_params_original.append('--preset veryfast')
        if not any('--rdoq-level' in param for param in encode_params_original):
            encode_params_original.append('--rdoq-level 2')
        if not any('--rc-lookahead' in param for param in encode_params_original):
            encode_params_original.append('--rc-lookahead 40')
        if not any('--lookahead-slices' in param for param in encode_params_original):
            encode_params_original.append('--lookahead-slices 0')
        if not any('--b-adapt' in param for param in encode_params_original):
            encode_params_original.append('--b-adapt 2')
        if not any('--me ' in param for param in encode_params_original):
            encode_params_original.append('--me hex')
        if not any('--merange ' in param for param in encode_params_original):
            encode_params_original.append('--merange 25')

    with open(encode_script, 'r') as file:
        lines = file.readlines()
    source = lines[0]
    cropping = next((line for line in lines if line.strip().lower().startswith('crop(')), None)

    start_frame = int(video_length * 0.05) # skipping the first 5% of the source
    end_frame = credits_start_frame - 1 if credits_start_frame is not None else video_length - 1 # probing ends before the credits start if defined
    trimmed_clip_length = end_frame - start_frame

    if encoder == 'svt':
        probe_length = max(2 * minkeyint, 128)
    else:
        probe_length = 128
    probe_ratio = 0.05  # size of total sample (1 = full length)
    step = int(round(probe_length / probe_ratio)) # how many frames between samples
    samples = (trimmed_clip_length + step - 1) // step # number of sample chunks
    probe_clip_length = probe_length * samples # length of the probe clip

    chunk_number = 1
    i = 0

    while True:
        start = i * probe_length
        end = start + (probe_length - 1)

        if start >= probe_clip_length:
            break

        probe_chunklist.append({
            'chunk': chunk_number,
            'length': probe_length,
            'start': start,
            'end': end,
            'credits': 0,
            'q': 0
        })

        chunk_number += 1
        i += 1

    qadjust_original_file = qadjust_original_file.replace(".avs", "_probe.avs")
    with open(qadjust_original_file, "w") as qadjust_script:
        qadjust_script.write(source)
        qadjust_script.write(f'\nTrim({start_frame}, {end_frame})\n')
        qadjust_script.write(f'SelectRangeEvery({step}, {probe_length})\n')
        if cropping:
            qadjust_script.write(cropping)
            qadjust_script.write('\n')
        qadjust_script.write('try {\n')
        qadjust_script.write(f'BicubicResizeMT({video_width},{video_height},b={qadjust_b},c={qadjust_c})\n')
        qadjust_script.write('}\n')
        qadjust_script.write('catch(err) {\n')
        qadjust_script.write(f'BicubicResize({video_width},{video_height},b={qadjust_b},c={qadjust_c})\n')
        qadjust_script.write('}\n')
        qadjust_script.write('ConvertBits(10)')

    # print(probe_chunklist)
    for i in probe_chunklist:
        encode_params = copy.deepcopy(encode_params_original)
        if encoder in ('svt', 'x265'):
            encode_params.append(f'--crf {i['q']}')
        scene_script_file = os.path.join(scripts_folder, f"scene_probe_{i['chunk']}.avs")
        if encoder != 'x265':
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_probe_{i['chunk']}.ivf")
        else:
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_probe_{i['chunk']}.hevc")
        # Create the Avisynth script for this scene
        with open(scene_script_file, 'w') as scene_script:
            # scene_script.write(source)
            scene_script.write(f'Import("qadjust_original_probe.avs")\n')
            scene_script.write(f"Trim({i['start']}, {i['end']})\n")

        if decode_method == 0:
            decode_command = [
                "avs2yuv64.exe",
                "-no-mt",
                '"'+scene_script_file+'"',  # Use the Avisynth script for this scene
                "-"
            ]
        else:
            decode_command = [
                "ffmpeg.exe",
                "-loglevel", "fatal",
                "-i", '"'+scene_script_file+'"',
                "-f", "yuv4mpegpipe",
                "-strict", "-1",
                "-"
            ]

        if encoder == 'svt':
            enc_command = [
                "svtav1encapp.exe",
                *encode_params,
                "-b", '"'+output_chunk+'"',
                "-i -"
            ]
        else:
            enc_command = [
                "x265.exe",
                "--y4m",
                "--no-progress",
                f'--frames {i['length']}',
                *encode_params,
                "--output", '"'+output_chunk+'"',
                "--input", "-"
            ]

        probe_encode_commands.append((decode_command, enc_command, output_chunk))
    # print (encode_commands)
    probe_chunklist_dict = {chunk_dict['chunk']: chunk_dict['length'] for chunk_dict in probe_chunklist}
    # for i in chunklist:
    # print(i['chunk'], i['length'], i['q'])
    logger.info(f"Total {len(probe_chunklist)} chunks created.")
    return probe_encode_commands, probe_chunklist, probe_chunklist_dict, encode_params


def process_rpu(i, chunklist_length, video_length, scripts_folder, chunks_folder, rpu):
    lastframe = video_length - 1
    # print (chunklist)
    jsonpath = os.path.join(scripts_folder, f"scene_{i['chunk']}_rpu.json")
    rpupath = os.path.join(chunks_folder, f"scene_{i['chunk']}_rpu.bin")
    if i['chunk'] == 1:
        start = i['end'] + 1
        data = {
            "remove": [f"{start}-{lastframe}"]
        }
    elif i['chunk'] < chunklist_length:
        start = 0
        end = i['start'] - 1
        start_2 = i['end'] + 1
        data = {
            "remove": [f"{start}-{end}", f"{start_2}-{lastframe}"]
        }
    else:
        start = 0
        end = i['start'] - 1
        data = {
            "remove": [f"{start}-{end}"]
        }
    with open(jsonpath, 'w') as json_file:
        json.dump(data, json_file, indent=2)
    dovitool_command = [
        "dovi_tool.exe",
        "editor",
        "-i", rpu,
        "-j", jsonpath,
        "-o", rpupath
    ]
    dovitool_process = subprocess.Popen(dovitool_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    dovitool_process.communicate()
    if dovitool_process.returncode != 0:
        logger.error(f"Error in RPU processing, return code {dovitool_process.returncode}")
        print("Error in RPU processing, return code:", dovitool_process.returncode)
        sys.exit(1)


def parse_master_display(master_display, max_cll):
    # Define conversion factors
    conversion_factors = {'G': 0.00002, 'B': 0.00002, 'R': 0.00002, 'WP': 0.00002, 'L': 0.0001}

    # Regular expression to extract values from the input string
    pattern = re.compile(r'([A-Z]+)\((\d+),(\d+)\)')

    # Function to apply the conversion factor to the extracted values
    def convert(match):
        group, value1, value2 = match.groups()
        factor = conversion_factors.get(group, 1.0)
        new_value1 = int(value1) * factor
        new_value2 = int(value2) * factor

        # Round the values to three decimals
        new_value1 = round(new_value1, 4)
        new_value2 = round(new_value2, 4)

        # If the result is greater than 1, convert to int
        if new_value1 > 1:
            new_value1 = int(new_value1)
        if new_value2 > 1:
            new_value2 = int(new_value2)

        return f'{group}({new_value1},{new_value2})'

    # Extract all values from the input string
    matches = pattern.findall(master_display)

    # Check if any of the original values is greater than 1
    if any(int(value1) > 1 or int(value2) > 1 for group, value1, value2 in matches):
        # Apply the conversion function to the input string
        processed_string = pattern.sub(convert, master_display)
    else:
        processed_string = master_display

    # Remove quotes from the result
    processed_string = processed_string.replace('"', '')
    max_cll = max_cll.replace('"', '')
    # print(processed_string, max_cll)

    return processed_string, max_cll


# Function to execute encoding commands and print them for debugging
def run_encode_command(command):
    global interrupted
    if interrupted:
        return None
    avs2yuv_command, enc_command, output_chunk = command
    avs2yuv_command = " ".join(avs2yuv_command)
    enc_command = " ".join(enc_command)

    enc_command = avs2yuv_command + ' | ' + enc_command
    # logger.info(f"Launching encoding command {enc_command}")

    # Print the encoding command for debugging
    # print(f"\nEncoder command: {enc_command}")

    for attempt in range(1, 3):
        try:
        # Execute the encoder process
            enc_process = subprocess.Popen(
                enc_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True
            )
            with process_list_lock:
                active_processes.append(enc_process)
            stdout, stderr = enc_process.communicate()
            with process_list_lock:
                active_processes.remove(enc_process)
            if enc_process.returncode == 0:
                return output_chunk
            else:
                logger.warning(f"Error in encoder processing, chunk {output_chunk}, attempt {attempt}.")
                logger.warning(f"Return code: {enc_process.returncode}")
                print(f"\nError in encoder processing, chunk {output_chunk}, attempt {attempt}.\n")
                print("Return code:", enc_process.returncode)
        except Exception as e:
            logger.error(f"Exception while encoding chunk {output_chunk}: {e}")

    logger.error(f"Max retries reached, unable to encode chunk {output_chunk}.")
    logger.error(f"The encoder command line is: {enc_command}.")
    print("Max retries reached, unable to encode chunk", output_chunk)
    print("The encoder command line is:", enc_command)
    return None


def encode_sample(output_folder, encode_script, encode_params, rpu, encoder, sample_start_frame, sample_end_frame, video_length, decode_method, threads, q):
    start_time = datetime.now()
    if rpu and encoder in ('svt', 'x265'):
        jsonpath = os.path.join(output_folder, "rpu_sample.json")
        rpupath = os.path.join(output_folder, "sample_rpu.bin")
        encode_params.append(f'--dolby-vision-rpu {rpupath}')
        if sample_start_frame == 0:
            start = sample_end_frame + 1
            end = video_length - 1
            data = {
                "remove": [f"{start}-{end}"]
            }
        elif sample_end_frame != video_length - 1:
            start = 0
            end = sample_start_frame - 1
            start_2 = sample_end_frame + 1
            end_2 = video_length - 1
            data = {
                "remove": [f"{start}-{end}", f"{start_2}-{end_2}"]
            }
        else:
            start = 0
            end = sample_start_frame - 1
            data = {
                "remove": [f"{start}-{end}"]
            }

        with open(jsonpath, 'w') as json_file:
            json.dump(data, json_file, indent=2)

        dovitool_command = [
            "dovi_tool.exe",
            "editor",
            "-i", rpu,
            "-j", jsonpath,
            "-o", rpupath
        ]
        dovitool_process = subprocess.Popen(dovitool_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        dovitool_process.communicate()
        if dovitool_process.returncode != 0:
            logger.error(f"Error in RPU processing, return code: {dovitool_process.returncode}")
            print("Error in RPU processing, return code:", dovitool_process.returncode)
            sys.exit(1)

    sample_script_file = os.path.join(output_folder, "sample.avs")
    if encoder != 'x265':
        output_chunk = os.path.join(output_folder, "sample.ivf")
    else:
        output_chunk = os.path.join(output_folder, "sample.hevc")
    # Create the Avisynth script for this scene
    with open(sample_script_file, "w") as sample_script:
        sample_script.write(f'Import("{encode_script}")\n')
        sample_script.write(f"Trim({sample_start_frame}, {sample_end_frame})\n")
        if encoder != 'x265':
            sample_script.write('ConvertBits(10)')  # workaround to aomenc bug with 8-bit input and film grain table

    if decode_method == 0:
        decode_command_sample = [
            "avs2yuv64.exe",
            "-no-mt",
            '"' + sample_script_file + '"',  # Use the Avisynth script for this scene
            "-"
        ]
    else:
        decode_command_sample = [
            "ffmpeg.exe",
            "-loglevel", "fatal",
            "-i", '"' + sample_script_file + '"',
            "-f", "yuv4mpegpipe",
            "-strict", "-1",
            "-"
        ]

    if encoder == 'rav1e':
        encode_params = [x.replace(f'--threads {threads}', '--threads 0') for x in encode_params]
        enc_command_sample = [
            "rav1e.exe",
            *encode_params,
            "--no-scene-detection",
            "-o", '"' + output_chunk + '"',
            "-"
        ]
    elif encoder == 'svt':
        encode_params = [x.replace(f'--lp {threads}', '--lp 0') for x in encode_params]
        enc_command_sample = [
            "svtav1encapp.exe",
            *encode_params,
            f"--crf {q}",
            "-b", '"' + output_chunk + '"',
            "-i -"
        ]
    elif encoder == 'aom':
        encode_params = [x.replace(f'--threads={threads}', f'--threads={os.cpu_count()}') for x in encode_params]
        enc_command_sample = [
            "aomenc.exe",
            "--ivf",
            *encode_params,
            "--passes=1",
            "-o", '"' + output_chunk + '"',
            "-"
        ]
    else:
        encode_params = [x.replace(f'--pools {threads}', '--pools *') for x in encode_params]
        enc_command_sample = [
            "x265.exe",
            "--y4m",
            *encode_params,
            f"--crf {q}",
            "--dither",
            "--output", '"' + output_chunk + '"',
            "--input", "-"
        ]

    decode_command_sample = ' '.join(decode_command_sample)
    enc_command_sample = ' '.join(enc_command_sample)
    enc_command_sample = decode_command_sample + ' | ' + enc_command_sample

    print("Encoding the sample file.\n")
    logger.info("Encoding the sample file.")

    enc_process_sample = subprocess.Popen(enc_command_sample, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    enc_process_sample.communicate()
    if enc_process_sample.returncode != 0:
        logger.error(f"Error in sample encode processing, return code: {enc_process_sample.returncode}.")
        print("Error in sample encode processing, return code:", enc_process_sample.returncode)
        sys.exit(1)

    end_time = datetime.now()
    sample_time = end_time - start_time
    logger.info(f"Path to the sample is: {output_chunk}. Encode duration {sample_time}.")
    print("Path to the sample is:", output_chunk)
    if rpu and encoder in ('svt', 'x265'):
        print("Please note that to ensure Dolby Vision mode during playback, it is recommended to mux the file using mkvmerge/MKVToolnix GUI.")


def concatenate(chunks_folder, input_files, output_final, fr, use_mkvmerge, encoder, video_matrix, video_transfer, video_primaries, chroma_location, master_display, max_cll):
    # concat_command = []
    # Create a list file for input files
    input_list_txt = os.path.join(chunks_folder, "input_list.txt")

    # Write the input file list to the text file
    with open(input_list_txt, "w") as file:
        if use_mkvmerge:
            for input_file in input_files:
                file.write(f"{input_file}\n")
        else:
            for input_file in input_files:
                file.write(f"file '{input_file}'\n")

    if use_mkvmerge:
        mkvmerge_json_file = os.path.join(chunks_folder, "input_list.json")
        with open(input_list_txt, "r") as input_file:
            files = [line.strip() for line in input_file]

        max_content_light = None
        max_frame_light = None
        chromaticity_coordinates = None
        white_color_coordinates = None
        max_luminance = None
        min_luminance = None

        if master_display:
            r = re.search(r"R\(([^)]+)\)", master_display).group(1)
            g = re.search(r"G\(([^)]+)\)", master_display).group(1)
            b = re.search(r"B\(([^)]+)\)", master_display).group(1)
            l = re.search(r"L\(([^)]+)\)", master_display).group(1)
            chromaticity_coordinates = ",".join([r, g, b])
            white_color_coordinates = re.search(r"WP\(([^)]+)\)", master_display).group(1)
            max_luminance, min_luminance = l.split(",")
        if max_cll:
            max_content_light, max_frame_light = max_cll.split(",")
            if max_content_light == 0 and max_frame_light == 0:
                max_content_light = None
                max_frame_light = None

        color_matrix_coefficients = str(video_matrix)
        color_transfer_characteristics = str(video_transfer)
        color_primaries = str(video_primaries)
        if chroma_location == 0:
            chroma_siting = "0,0"
        else:
            chroma_siting = "1,1"

        metadata = {
            "--color-matrix-coefficients": "0:" + color_matrix_coefficients,
            "--chroma-siting": "0:" + chroma_siting,
            "--color-transfer-characteristics": "0:" + color_transfer_characteristics,
            "--color-primaries": "0:" + color_primaries,
            "--max-content-light": "0:" + str(max_content_light),
            "--max-frame-light": "0:" + str(max_frame_light),
            "--chromaticity-coordinates": "0:" + str(chromaticity_coordinates),
            "--white-color-coordinates": "0:" + str(white_color_coordinates),
            "--max-luminance": "0:" + str(max_luminance),
            "--min-luminance": "0:" + str(min_luminance)
        }

        mkvmerge_json = [
            "--ui-language", "en",
            "--output", output_final,
            "--language", "0:und",
            "--compression", "0:none"
        ]

        for file in files:
            # Add only those metadata options that have non-empty values
            for key, value in metadata.items():
                if value not in (None, "0:None", ""):
                    mkvmerge_json.extend([key, str(value)])
            mkvmerge_json.extend(["(", file, ")", "+"])
        mkvmerge_json.pop()  # Remove the trailing "+"
        mkvmerge_json.extend([
            "--append-to",
            "1:0:0:0"
        ])

        with open(mkvmerge_json_file, "w") as json_file:
            json.dump(mkvmerge_json, json_file, indent=2)

        concat_command = [
            "mkvmerge.exe",
            "-q",
            f"@{mkvmerge_json_file}"
        ]

        logger.info("Concatenating using mkvmerge.")
        print("Concatenating chunks using mkvmerge.\n")

    else:
        # Define the ffmpeg concatenation command
        if encoder != 'x265':
            concat_command = [
                "ffmpeg.exe",
                "-loglevel", "warning",
                "-f", "concat",
                "-safe", "0",  # Allow absolute paths
                "-i", input_list_txt,
                "-c", "copy",
                "-strict", "strict",
                "-map", "0",
                "-y",  # Overwrite output file if it exists
                output_final
            ]
        else:
            concat_command = [
                "ffmpeg.exe",
                "-loglevel", "error",
                "-f", "concat",
                "-safe", "0",  # Allow absolute paths
                "-fflags", "+genpts",
                "-r", str(fr),
                "-i", input_list_txt,
                "-c", "copy",
                "-strict", "strict",
                "-map", "0",
                "-y",  # Overwrite output file if it exists
                output_final
            ]

        # Print the ffmpeg concatenation command for debugging
        # print("Concatenation Command (ffmpeg):")
        # print(" ".join(ffmpeg_concat_command))

        logger.info("Concatenating using ffmpeg.")
        print("Concatenating chunks using ffmpeg.\n")

    concat_process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    concat_process.communicate()
    if concat_process.returncode != 0:
        logger.error(f"Error when concatenating, return code: {concat_process.returncode}")
        print("Error when concatenating, return code:", concat_process.returncode)
        sys.exit(1)

    logger.info(f"Concatenated video saved as {output_final}.")
    print(f"Concatenated video saved as {output_final}.")


def read_presets(presets, encoder):
    scriptdir = os.path.dirname(os.path.realpath(__file__))
    presetpath = os.path.join(scriptdir, 'presets.ini')
    config = configparser.ConfigParser()
    try:
        config.read(presetpath)
    except Exception as e:
        logger.error(f"Presets.ini could not be found from the script directory. Exception code {e}")
        print("\nPresets.ini could not be found from the script directory, exiting..\n")
        sys.exit(1)
    default_section = encoder + '-default'
    try:
        default_params = dict(config.items(default_section))
    except Exception as e:
        logger.warning(f"The default settings for the encoder could not be found from presets.ini. Exception code {e}")
        print("\nWarning: the default settings for the encoder could not be found from presets.ini.\n")
        default_params = {}
    try:
        base_working_folder = config['paths']['base_working_folder']
    except Exception as e:
        logger.warning(f"The path for base working folder is missing from presets.ini. Exception code {e}")
        print("The path for base working folder is missing from presets.ini.\n")
        base_working_folder = input("Please enter a path: ")
    try:
        preset_config_json = config['paths']['model_config_json']
    except:
        preset_config_json = ''

    merged_params = {}
    for preset in presets:
        preset_section = encoder + '-' + preset
        try:
            preset_params = dict(config.items(preset_section))
            merged_params.update(preset_params)
        except Exception as e:
            logger.error(f"Chosen preset not found from the presets.ini file. Exception code {e}")
            print("\nChosen preset not found from the presets.ini file.\n")
            sys.exit(1)

    return default_params, merged_params, base_working_folder, preset_config_json


def calculate_ssimu2_stats(score_list: list[int]):
    filtered_score_list = [score for score in score_list if score >= 0]
    average = np.mean(filtered_score_list)
    return average, float(np.percentile(filtered_score_list, 5))


def run_encode(qadjust_cycle, chunklist, video_length, fr, max_parallel_encodes, encode_commands, chunklist_dict, reuse_qadjust, start_time):
    global interrupted
    global avg_bitrate
    completed_chunks = []  # List of completed chunks
    processed_length = 0
    total_filesize_kbits = 0.0
    avg_bitrate = 0.0
    chunks = len(chunklist)
    encoded_chunks = 0
    if qadjust_cycle == 1:
        video_length = 0
        for i in chunklist:
            if i['credits'] == 1:
                chunks = chunks - 1
                continue
            else:
                video_length += i['length']
    progress_bar = tqdm(total=video_length, desc="Progress", unit="frames", smoothing=0)
    with ThreadPoolExecutor(max_parallel_encodes) as executor:
        futures = {executor.submit(run_encode_command, cmd): cmd for cmd in encode_commands}
        try:
            for future in as_completed(futures):
                if interrupted:
                    break  # Stop processing more results
                output_chunk = future.result()
                parts = output_chunk.split('_')
                chunk_number = int(str(parts[-1].split('.')[0]))
                chunk_length = chunklist_dict.get(chunk_number) / fr
                chunk_size = os.path.getsize(output_chunk) / 1024 * 8
                processed_length = processed_length + chunk_length
                chunk_avg_bitrate = round(chunk_size / chunk_length, 2)
                total_filesize_kbits = total_filesize_kbits + chunk_size
                avg_bitrate = total_filesize_kbits / processed_length
                encoded_chunks += 1
                if qadjust_cycle != 3:
                    logger.info(f"Chunk {chunk_number} finished, length {round(chunk_length, 2)}s, average bitrate {chunk_avg_bitrate} kbps.")
                progress_bar.update(chunklist_dict.get(chunk_number))
                progress_bar.set_postfix({'Chunks': f'{encoded_chunks}/{chunks}', 'Rate': f'{avg_bitrate:.2f} kbps', 'Est. size': f'{(video_length/fr)*avg_bitrate/8/1024:.2f} MB'})
                completed_chunks.append(output_chunk)
                # print(f"Encoding for scene completed: {output_chunk}")
        except KeyboardInterrupt:
            print("\nEncoding interrupted by user. Terminating all subprocesses...")
            logger.warning("Encoding interrupted by user.")
            interrupted = True
            progress_bar.close()
            terminate_all_processes()
            executor.shutdown(wait=False, cancel_futures=True)
            return
        except Exception as e:
            interrupted = True
            logger.error("Something went wrong while encoding, please restart!")
            logger.error(f"Exception: {e}")
            print("Something went wrong while encoding, please restart!\n")
            print("Exception:", e)
            terminate_all_processes()
            executor.shutdown(wait=False, cancel_futures=True)
            return

    # Wait for all encoding processes to finish before concatenating
    progress_bar.close()
    end_time = datetime.now()
    encoding_time = end_time - start_time
    if qadjust_cycle not in (1, 2) or reuse_qadjust:
        print("Finished encoding the chunks.\n")
        logger.info(f"Final encode finished, average bitrate {avg_bitrate:.2f} kbps.")
    else:
        print("Finished encoding the Q analysis chunks.\n")
        logger.info(f"Q analysis encode finished, average bitrate {avg_bitrate:.2f} kbps.")
    logger.info(f"Total encoding time {encoding_time}.")


def analyze_butteraugli_chunk(chunk, name, ext, qadjust_original_file, skip, qadjust_threads, qadjust_target, phase, video_matrix, video_transfer, video_primaries, chroma_location):
    import vapoursynth as vs

    core = vs.core
    core.max_cache_size = 2048

    cut_source_clip = core.avisource.AVIFileSource(fr"{qadjust_original_file}")[chunk['start']:chunk['end'] + 1]
    cut_encoded_clip = core.lsmas.LWLibavSource(source=f"{name}{chunk['chunk']}{ext}", cache=0)

    if skip != 1:
        cut_source_clip = cut_source_clip.std.SelectEvery(cycle=skip, offsets=0)
        cut_encoded_clip = cut_encoded_clip.std.SelectEvery(cycle=skip, offsets=0)

    # Let's ensure Vship gets the properties of the clips
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Matrix=video_matrix)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Transfer=video_transfer)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Primaries=video_primaries)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _ChromaLocation=chroma_location)

    result = core.vship.BUTTERAUGLI(cut_source_clip, cut_encoded_clip, numStream=qadjust_threads)
    chunk_scores = []
    for frame in result.frames():
        score = frame.props['_BUTTERAUGLI_3Norm']
        chunk_scores.append(score)

    chunk_scores = [s for s in chunk_scores if s > 0]

    if not chunk_scores:
        average = 0.0
    else:
        average = np.mean(np.power(chunk_scores, 3)) ** (1 / 3)

    if phase == 'butter_pass1':
        if average < qadjust_target:
            butter_pass2_crf = 33.0
        else:
            butter_pass2_crf = 10.5
        return chunk['chunk'], average, chunk_scores, butter_pass2_crf
    else:
        return chunk['chunk'], average, chunk_scores


def analyze_ssimu2_chunk(chunk, name, ext, qadjust_original_file, skip, video_matrix, video_transfer, video_primaries, chroma_location, metrics_plugin, qadjust_threads):
    import vapoursynth as vs
    core = vs.core
    core.max_cache_size = 2048

    cut_source_clip = core.avisource.AVIFileSource(fr"{qadjust_original_file}")[chunk['start']:chunk['end'] + 1]
    cut_encoded_clip = core.lsmas.LWLibavSource(source=f"{name}{chunk['chunk']}{ext}", cache=0)

    if metrics_plugin == 3:
        cut_source_clip = cut_source_clip.resize.Bicubic(format=vs.RGBS, matrix_in_s=video_matrix).fmtc.transfer(transs="srgb", transd="linear", bits=32)

    if skip != 1:
        cut_source_clip = cut_source_clip.std.SelectEvery(cycle=skip, offsets=0)
        cut_encoded_clip = cut_encoded_clip.std.SelectEvery(cycle=skip, offsets=0)

    # Let's ensure Vship gets the properties of the clips
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Matrix=video_matrix)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Transfer=video_transfer)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Primaries=video_primaries)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _ChromaLocation=chroma_location)

    if metrics_plugin == 1:
        result = core.vship.SSIMULACRA2(cut_source_clip, cut_encoded_clip, numStream=qadjust_threads)
        scores = [frame.props['_SSIMULACRA2'] for frame in result.frames()]
    elif metrics_plugin == 2:
        result = core.vszip.SSIMULACRA2(cut_source_clip, cut_encoded_clip)
        scores = [frame.props['SSIMULACRA2'] for frame in result.frames()]
    else:
        result = cut_source_clip.ssimulacra2.SSIMULACRA2(cut_encoded_clip)
        scores = [frame.props['SSIMULACRA2'] for frame in result.frames()]

    filtered = [s for s in scores if s >= 0]
    avg = float(np.mean(filtered))
    p5 = float(np.percentile(filtered, 5))

    return chunk['chunk'], avg, scores, p5


def analyze_cvvdp_chunk(chunk, name, ext, qadjust_original_file, skip, video_matrix, video_transfer, video_primaries, chroma_location, phase, cvvdp_model, cvvdp_config):
    import vapoursynth as vs
    core = vs.core
    core.max_cache_size = 2048

    cut_source_clip = core.avisource.AVIFileSource(fr"{qadjust_original_file}")[chunk['start']:chunk['end'] + 1]
    cut_encoded_clip = core.lsmas.LWLibavSource(source=f"{name}{chunk['chunk']}{ext}", cache=0)
    if skip != 1:
        cut_source_clip = cut_source_clip.std.SelectEvery(cycle=skip, offsets=0)
        cut_encoded_clip = cut_encoded_clip.std.SelectEvery(cycle=skip, offsets=0)

    # Let's ensure Vship gets the properties of the clips
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Matrix=video_matrix)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Transfer=video_transfer)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _Primaries=video_primaries)
    cut_source_clip = core.std.SetFrameProps(cut_source_clip, _ChromaLocation=chroma_location)

    if video_transfer == 'smpte2084':
        result = core.vship.CVVDP(cut_source_clip, cut_encoded_clip, distmap=0, model_name=cvvdp_model, resizeToDisplay=1, model_config_json=cvvdp_config)
    else:
        result = core.vship.CVVDP(cut_source_clip, cut_encoded_clip, distmap=0, model_name=cvvdp_model, resizeToDisplay=1, model_config_json=cvvdp_config)

    frames = cut_source_clip.num_frames
    score = [frame.props['_CVVDP'] for frame in result.frames()]

    if phase != 'cvvdp_probing':
        luma_clip = cut_source_clip.std.ShufflePlanes(planes=0, colorfamily=vs.GRAY)
        stats_clip = luma_clip.std.PlaneStats()

        luma_sum = 0.0
        luma_frames = stats_clip.num_frames

        for f in stats_clip.frames():
            luma_sum += f.props['PlaneStatsAverage']

        average_luma = luma_sum / luma_frames
    else:
        average_luma = 0.0

    return chunk['chunk'], score[-1], score[-1] * frames, frames, average_luma, average_luma * frames


# noinspection PyTypeChecker,PyUnboundLocalVariable
def calculate_metrics(chunklist, skip, qadjust_original_file, output_final_metrics, encoder, q, br, qadjust_results_file, video_matrix, video_transfer, video_primaries, chroma_location, qadjust_workers, qadjust_threads, qadjust_mode, qadjust_cpu,
                      cpu, qadjust_target, avg_bitrate, avg_bitrate_qadjust_pass1, phase, min_chunk_length, minkeyint, cvvdp_curve, cvvdp_q, cvvdp_min_luma, cvvdp_max_luma, qadjust_min_q, qadjust_max_q, cvvdp_model, cvvdp_config):
    global butter_scores_pass1
    global butter_scores_pass2
    global chunk_cvvdp_scores
    global average_qadjust_pass1
    global metrics_plugin

    if metrics_plugin == 0:
        import vapoursynth as vs
        core = vs.core
        core.max_cache_size = 512

        plugin_keys = [plugin.identifier for plugin in core.plugins()]
        if 'com.lumen.vship' in plugin_keys:
            print("Using VSHIP to calculate the metrics.")
            metrics_plugin = 1
        elif 'com.julek.vszip' in plugin_keys:
            print("Using vapoursynth-zip to calculate the SSIMU2 score.")
            metrics_plugin = 2
            qadjust_mode = 1
        elif 'com.julek.ssimulacra2' in plugin_keys:
            print("Using the deprecated vapoursynth-ssimulacra2 to calculate the SSIMU2 score. Please consider upgrading to VSHIP or vapoursynth-zip.")
            metrics_plugin = 3
            qadjust_mode = 1
        else:
            print("Unable to find a Vapoursynth module to do the metrics calculation, exiting.")
            logger.error("Unable to find a Vapoursynth module to do the metrics calculation, exiting.")
            sys.exit(1)

    name, ext = os.path.splitext(output_final_metrics)
    if phase not in ('butter_pass2', 'cvvdp_probing'):
        source_length = sum(chunk['length'] for chunk in chunklist)
        analysis_length = sum(chunk['length'] for chunk in chunklist if chunk['credits'] == 0)
        print(f"Original: {source_length} frames, including credits")
        print(f"Analysis pass encode: {analysis_length} frames, credits ignored")

    print(f"Calculating the metrics using skip value {skip}.\n")
    logger.info(f"Calculating the metrics using skip value {skip}.")
    start_time = datetime.now()

    # SSIMU2
    if qadjust_mode == 1:
        tasks = [(chunk, name, ext, qadjust_original_file, skip, video_matrix, video_transfer, video_primaries, chroma_location, metrics_plugin, qadjust_threads)
                 for chunk in chunklist if chunk['credits'] == 0]

        total_metric_scores = []
        percentile_5_total = [0] * len(chunklist)

        with ProcessPoolExecutor(max_workers=qadjust_workers, initializer=silence_worker) as executor:
            futures = [executor.submit(analyze_ssimu2_chunk_wrapper, task) for task in tasks]
            results = []
            with tqdm(total=len(futures), desc="Progress", unit="chunk(s)", smoothing=0) as pbar:
                for future in as_completed(futures):
                    results.append(future.result())
                    pbar.update(1)

        results.sort(key=lambda x: x[0])  # Sort by chunk_number

        for chunk_number, avg, scores, percentile_5 in results:
            total_metric_scores.extend(scores)
            # Store percentile_5 in the right index based on chunk_number
            for i, chunk in enumerate(chunklist):
                if chunk['chunk'] == chunk_number:
                    percentile_5_total[i] = percentile_5
                    break

        # Final stats
        # average = float(np.mean(total_metric_scores))
        total_metric_scores = np.array(total_metric_scores)
        average = len(total_metric_scores) / np.sum(1.0 / total_metric_scores)
        print(f'SSIMU2 harmonic mean:  {average:.5f}')
        logger.info(f"SSIMU2 harmonic mean: {average:.5f}")
    # BUTTERAUGLI
    elif qadjust_mode == 2:
        if phase == 'butter_pass1':
            print(f"Butteraugli at CRF 24.0:")
            tasks = [(chunk, name, ext, qadjust_original_file, skip, qadjust_threads, qadjust_target, phase, video_matrix, video_transfer, video_primaries, chroma_location)
                     for chunk in chunklist if chunk['credits'] == 0]
            with ProcessPoolExecutor(max_workers=qadjust_workers, initializer=silence_worker) as executor:
                futures = [executor.submit(analyze_butteraugli_chunk_wrapper, task) for task in tasks]
                results = []
                with tqdm(total=len(futures), desc="Progress", unit="chunk(s)", smoothing=0) as progress_bar:
                    for future in as_completed(futures):
                        results.append(future.result())
                        progress_bar.update(1)

            butter_scores_pass1.clear()
            all_scores = []
            butter_pass2_crfs = []

            zero_score_chunks = [r[0] for r in results if r[1] == 0.0]  # chunk number where avg == 0
            if zero_score_chunks:
                print("Chunks with only zero-score (black) frames:", ", ".join(map(str, zero_score_chunks)))
                for zc in zero_score_chunks:
                    logger.info(f"Chunk {zc} has only zero-score (black) frames!")

            # Sort by chunk number to match original list
            results.sort(key=lambda x: x[0])
            for chunk_number, rmc, scores, butter_pass2_crf in results:
                butter_scores_pass1.append(rmc)
                all_scores.extend(scores)
                butter_pass2_crfs.append(butter_pass2_crf)

            # average_qadjust_pass1 = len(all_scores) / np.sum(1.0 / (np.array(all_scores)))
            average_qadjust_pass1 = np.mean(np.power(all_scores, 3)) ** (1 / 3)
            print(f'Root mean cube score across all analyzed frames, CRF 24.0:  {average_qadjust_pass1:.5f}\n')
            logger.info(f"Butteraugli CRF 24.0 pass root mean cube score across all analyzed frames: {average_qadjust_pass1:.5f}")

        else:
            print(f"Butteraugli pass 2:")
            tasks = [(chunk, name, ext, qadjust_original_file, skip, qadjust_threads, qadjust_target, phase, video_matrix, video_transfer, video_primaries, chroma_location)
                     for chunk in chunklist if chunk['credits'] == 0]
            with ProcessPoolExecutor(max_workers=qadjust_workers, initializer=silence_worker) as executor:
                futures = [executor.submit(analyze_butteraugli_chunk_wrapper, task) for task in tasks]
                results = []
                with tqdm(total=len(futures), desc="Progress", unit="chunk(s)", smoothing=0) as progress_bar:
                    for future in as_completed(futures):
                        results.append(future.result())
                        progress_bar.update(1)

            zero_score_chunks = [r[0] for r in results if r[1] == 0.0]  # chunk number where avg == 0
            if zero_score_chunks:
                print("Chunks with only zero-score (black) frames:", ", ".join(map(str, zero_score_chunks)))
                for zc in zero_score_chunks:
                    logger.info(f"Chunk {zc} has only zero-score (black) frames!")

            butter_scores_pass2.clear()
            all_scores = []

            # Sort by chunk number to match original list
            results.sort(key=lambda x: x[0])
            for chunk_number, rmc, scores in results:
                butter_scores_pass2.append(rmc)
                all_scores.extend(scores)
    # CVVDP
    else:
        if phase == 'cvvdp_probing':
            qadjust_original_file = qadjust_original_file.replace(".avs", "_probe.avs")
        tasks = [(chunk, name, ext, qadjust_original_file, skip, video_matrix, video_transfer, video_primaries, chroma_location, phase, cvvdp_model, cvvdp_config)
                 for chunk in chunklist if chunk['credits'] == 0]

        total_metric_scores = []
        total_weight = 0.0
        total_weight_luma = 0.0
        weighted_frames = 0

        with ProcessPoolExecutor(max_workers=qadjust_workers, initializer=silence_worker) as executor:
            futures = [executor.submit(analyze_cvvdp_chunk_wrapper, task) for task in tasks]
            results = []
            with tqdm(total=len(futures), desc="Progress", unit="chunk(s)", smoothing=0) as pbar:
                for future in as_completed(futures):
                    results.append(future.result())
                    pbar.update(1)

        results.sort(key=lambda x: x[0])  # Sort by chunk_number

        for chunk_number, score, weight, frames, average_luma, weighted_average_luma in results:
            total_metric_scores.append(score)
            total_weight += weight
            total_weight_luma += weighted_average_luma
            weighted_frames += frames

            chunk_cvvdp_scores.append({
                'chunk': chunk_number,
                'score': score,
                'average_luma': average_luma,
            })

        # Final stats
        # average = float(np.mean(total_metric_scores))
        total_metric_scores = np.array(total_metric_scores)
        average = len(total_metric_scores) / np.sum(1.0 / total_metric_scores)
        weighted_average = total_weight / weighted_frames

        if phase == 'cvvdp_probing':
            print(f'CVVDP harmonic mean: {average:.5f}, weighted score: {weighted_average:.5f}')
            logger.info(f"CVVDP harmonic mean: {average:.5f}, weighted score: {weighted_average:.5f}")
            return weighted_average
        else:
            weighted_average_luma = total_weight_luma / weighted_frames
            print(f'CVVDP harmonic mean: {average:.5f}, weighted score: {weighted_average:.5f}, weighted luma: {weighted_average_luma:.5f}')
            logger.info(f"CVVDP harmonic mean: {average:.5f}, weighted score: {weighted_average:.5f}, weighted luma: {weighted_average_luma:.5f}")

    if qadjust_mode == 1:
        qadjust_data = {
            "qadjust_cpu": qadjust_cpu,
            "avg_bitrate": avg_bitrate,
            "ssimu2_harmonic_mean_score": average,
            "min_chunk_length": min_chunk_length,
            "min_keyint": minkeyint,
            "chunks": []
        }
    elif phase == 'butter_pass2':
        qadjust_data = {
            "qadjust_cpu": qadjust_cpu,
            "butteraugli_target": qadjust_target,
            "avg_bitrate_qadjust_pass1": avg_bitrate_qadjust_pass1,
            "butteraugli_score_pass1": average_qadjust_pass1,
            "min_chunk_length": min_chunk_length,
            "min_keyint": minkeyint,
            "chunks": []
        }
    elif phase == 'cvvdp':
        qadjust_data = {
            "cvvdp": {
                "curve": cvvdp_curve
            },
            "qadjust_cpu": qadjust_cpu,
            "analysis_q": cvvdp_q,
            "target": qadjust_target,
            "cvvdp_weighted_score": weighted_average,
            "average_weighted_luma": weighted_average_luma,
            "cvvdp_q_scaling_min_luma": cvvdp_min_luma,
            "cvvdp_q_scaling_max_luma": cvvdp_max_luma,
            "min_chunk_length": min_chunk_length,
            "min_keyint": minkeyint,
            "chunks": []
        }

    chunklist = sorted(chunklist, key=lambda x: x['chunk'], reverse=False) # order chunklist by chunk number

    if qadjust_mode == 1:
        for i in range(len(chunklist)):
            if chunklist[i]['credits'] == 1:
                continue
            if encoder == 'svt':
                new_q = q - round((1.0 - (percentile_5_total[i] / average)) / 0.5 * 10 * 4) / 4
            else:
                new_q = q - round((1.0 - (percentile_5_total[i] / average)) * 10 * 4) / 4
            if new_q < q - br:
                new_q = q - br
            if new_q > q + br:
                new_q = q + br
            qadjust_data["chunks"].append({
                "chunk_number": chunklist[i]['chunk'],
                "length": chunklist[i]['length'],
                "percentile_5th": percentile_5_total[i],
                "adjusted_Q": new_q
            })
            chunklist[i]['q'] = new_q
        total_length = 0
        weighted_sum = 0
        for chunk in chunklist:
            if chunk['credits'] == 1:
                continue
            length = chunk['length']
            weighted_sum += chunk['q'] * length
            total_length += length
        weighted_crf = round(weighted_sum / total_length, 2)
        qadjust_data = {
            **{k: v for k, v in qadjust_data.items() if k != "chunks"},
            "weighted_crf": weighted_crf,
            "chunks": qadjust_data["chunks"]
        }
    elif phase == 'butter_pass1':
        for i in range(len(chunklist)):
            if chunklist[i]['credits'] == 1:
                continue
            chunklist[i]['q'] = butter_pass2_crfs[i]
    elif qadjust_mode == 2:
        new_crfs = adjust_crf_butteraugli(butter_scores_pass1, butter_scores_pass2, qadjust_target, qadjust_cpu, cpu, q, chunklist)
        for i in range(len(chunklist)):
            if chunklist[i]['credits'] == 1:
                continue
            new_q = new_crfs[i]
            qadjust_data["chunks"].append({
                "chunk_number": chunklist[i]['chunk'],
                "length": chunklist[i]['length'],
                "crf_pass2": chunklist[i]['q'],
                "butteraugli_pass1": float(butter_scores_pass1[i]),
                "butteraugli_pass2": float(butter_scores_pass2[i]),
                "adjusted_Q": new_q
            })
            chunklist[i]['q'] = new_q
        total_length = 0
        weighted_sum = 0
        for chunk in chunklist:
            if chunk['credits'] == 1:
                continue
            length = chunk['length']
            weighted_sum += chunk['q'] * length
            total_length += length
        weighted_crf = round(weighted_sum / total_length, 2)
        qadjust_data = {
            **{k: v for k, v in qadjust_data.items() if k != "chunks"},
            "weighted_crf": weighted_crf,
            "chunks": qadjust_data["chunks"]
        }
    elif qadjust_mode == 3:
        new_crfs = adjust_crf_cvvdp(chunk_cvvdp_scores, cvvdp_q, qadjust_target, cvvdp_curve, cvvdp_min_luma, cvvdp_max_luma, qadjust_min_q, qadjust_max_q, video_transfer)
        new_crf_by_chunk = {row['chunk']: row['q'] for row in new_crfs}
        scale_by_chunk = {row['chunk']: row['scale'] for row in new_crfs}
        base_crf_by_chunk = {row['chunk']: row['q_undamped'] for row in new_crfs}
        score_by_chunk = {row['chunk']: row['score'] for row in chunk_cvvdp_scores}
        luma_by_chunk= {row['chunk']: row['average_luma'] for row in chunk_cvvdp_scores}
        qadjust_data["chunks"] = []

        for chunk in chunklist:
            if chunk['credits'] == 1:
                continue

            chunk_number = chunk['chunk']
            new_q = new_crf_by_chunk[chunk_number]

            qadjust_data["chunks"].append({
                "chunk_number": chunk_number,
                "length": chunk['length'],
                "cvvdp_score": float(score_by_chunk[chunk_number]),
                "average_luma": float(luma_by_chunk[chunk_number]),
                "luma_scale": float(scale_by_chunk[chunk_number]),
                "undamped_Q": base_crf_by_chunk[chunk_number],
                "adjusted_Q": new_q
            })

            # Update the chunk itself
            chunk['q'] = new_q

        total_length = 0
        weighted_sum = 0
        for chunk in chunklist:
            if chunk['credits'] == 1:
                continue
            length = chunk['length']
            weighted_sum += chunk['q'] * length
            total_length += length

        weighted_crf = round(weighted_sum / total_length, 2)

        qadjust_data = {
            **{k: v for k, v in qadjust_data.items() if k != "chunks"},
            "weighted_crf": weighted_crf,
            "chunks": qadjust_data["chunks"]
        }
        # sys.exit(0)

    chunklist = sorted(chunklist, key=lambda x: x['length'], reverse=True)

    if phase != 'butter_pass1':
        with open(qadjust_results_file, 'w') as results_file:
            json.dump(qadjust_data, results_file, indent=4)
        end_time = datetime.now()
        metrics_time = end_time - start_time
        logger.info(f"Metrics calculation finished, duration {metrics_time}.")
        show_qs(chunklist, False)
        return chunklist
    else:
        return chunklist


def calculate_svt_keyint(video_framerate, target_keyint_seconds, startup_mg_size, hierarchical_levels):
    startup_mg_size = 2 ** (startup_mg_size - 1)
    regular_mg_size = 2 ** (hierarchical_levels - 1)

    raw_keyint = video_framerate * target_keyint_seconds

    # We need total keyint >= raw_keyint
    # Total keyint = first_mg_size + N * regular_mg_size
    # Solve for N:
    remaining = raw_keyint - startup_mg_size
    if remaining <= 0:
        # first mini-GOP alone is enough
        aligned_keyint = startup_mg_size
    else:
        # Number of regular mini-GOPs needed after first one, rounded UP
        num_regular_mgs = math.ceil(remaining / regular_mg_size)
        aligned_keyint = startup_mg_size + num_regular_mgs * regular_mg_size

    return aligned_keyint


def linear_butter(score_pass1, score_pass2, qstep_pass1, qstep_pass2, qadjust_target, qadjust_cpu, cpu, min_crf, max_crf):
    dc = np.array([4, 9, 10, 13, 15, 17, 20, 22, 25, 28, 31, 34, 37, 40, 43, 47, 50, 53, 57, 60, 64, 68, 71, 75, 78, 82, 86, 90, 93, 97, 101, 105, 109, 113, 116, 120, 124, 128, 132, 136, 140, 143, 147, 151, 155, 159, 163, 166, 170, 174, 178, 182,
                    185, 189, 193, 197, 200, 204, 208, 212, 215, 219, 223, 226, 230, 233, 237, 241, 244, 248, 251, 255, 259, 262, 266, 269, 273, 276, 280, 283, 287, 290, 293, 297, 300, 304, 307, 310, 314, 317, 321, 324, 327, 331, 334, 337, 343, 350,
                    356, 362, 369, 375, 381, 387, 394, 400, 406, 412, 418, 424, 430, 436, 442, 448, 454, 460, 466, 472, 478, 484, 490, 499, 507, 516, 525, 533, 542, 550, 559, 567, 576, 584, 592, 601, 609, 617, 625, 634, 644, 655, 666, 676, 687, 698,
                    708, 718, 729, 739, 749, 759, 770, 782, 795, 807, 819, 831, 844, 856, 868, 880, 891, 906, 920, 933, 947, 961, 975, 988, 1001, 1015, 1030, 1045, 1061, 1076, 1090, 1105, 1120, 1137, 1153, 1170, 1186, 1202, 1218, 1236, 1253, 1271,
                    1288, 1306, 1323, 1342, 1361, 1379, 1398, 1416, 1436, 1456, 1476, 1496, 1516, 1537, 1559, 1580, 1601, 1624, 1647, 1670, 1692, 1717, 1741, 1766, 1791, 1817, 1844, 1871, 1900, 1929, 1958, 1990, 2021, 2054, 2088, 2123, 2159, 2197,
                    2236, 2276, 2319, 2363, 2410, 2458, 2508, 2561, 2616, 2675, 2737, 2802, 2871, 2944, 3020, 3102, 3188, 3280, 3375, 3478, 3586, 3702, 3823, 3953, 4089, 4236, 4394, 4559, 4737, 4929, 5130, 5347])
    dc_x = np.arange(dc.shape[0])
    fit = np.polynomial.Polynomial.fit([score_pass1, score_pass2], [qstep_pass1, qstep_pass2], 1)
    qstep = fit(qadjust_target)

    # Apply correction for high qstep
    if qstep > 163:
        if qadjust_cpu >= 6:
            factors = {-1: 0.72, 0: 0.73, 2: 0.76, 5: 0.84}
        elif qadjust_cpu >= 5:
            factors = {-1: 0.82, 0: 0.83, 2: 0.86}
        elif qadjust_cpu >= 3:
            factors = {-1: 0.90, 0: 0.91, 2: 0.94}
        else:
            factors = {}

        for threshold, factor in factors.items():
            # check against *final preset* value
            if cpu <= threshold:
                qstep = (qstep - 163) * factor + 163
                break

    # Convert qstep to CRF
    crf = np.interp(qstep, dc, dc_x) / 4
    crf = np.clip(crf, min_crf, max_crf)
    # Force quarter precision, round *up* (ceil)
    crf = float(np.ceil(crf * 4) / 4)

    return crf


def adjust_crf_butteraugli(butter_scores_pass1, butter_scores_pass2, qadjust_target, qadjust_cpu, cpu, q, chunklist):
    crfs = []
    for i in range(len(chunklist)):
        if chunklist[i]['credits'] == 1:
            continue
        if chunklist[i]['q'] > 24.0 and butter_scores_pass2[i] < butter_scores_pass1[i]:
            crf = q
            crfs.append(crf)
            print(f"Fallback CRF {q} used for chunk {chunklist[i]['chunk']}")
            logger.info(f"Fallback CRF {q} used for chunk {chunklist[i]['chunk']}")
            continue
        if chunklist[i]['q'] > 24.0:
            qstep_pass1, qstep_pass2 = 343, 592
        else:
            qstep_pass1, qstep_pass2 = 343, 155
        crf = linear_butter(butter_scores_pass1[i], butter_scores_pass2[i], qstep_pass1, qstep_pass2, qadjust_target, qadjust_cpu, cpu, 10.0, 50.0)
        crfs.append(crf)
    return crfs


def adjust_crf_cvvdp(chunk_cvvdp_scores, cvvdp_q, qadjust_target, cvvdp_curve, cvvdp_min_luma, cvvdp_max_luma, qadjust_min_q, qadjust_max_q, video_transfer):
    """
    Adjust per-chunk CRF based on CVVDP scores, with perceptual protection
    against over-compression in dark scenes using chunk-average luma.

    Core principles:
      - CVVDP controls Q adjustments globally.
      - Raising Q (more compression) is *selectively damped* in dark scenes.
      - Lowering Q (less compression) is never damped.
      - SDR and HDR use different luma response curves.
      - Luma is chunk-average, not frame-based.

    Parameters:
        chunk_cvvdp_scores : list of dict
            [
              {
                'chunk': int,
                'score': float,
                'average_luma': float
              },
              ...
            ]

        cvvdp_q : float
            Reference CRF used during the CVVDP analysis pass.

        qadjust_target : float
            Target CVVDP score.

        cvvdp_curve : list of dict
            Probe curve mapping CRF to CVVDP score:
            [{'q': Q, 'score': S}, ...]

        cvvdp_min_luma : float
            Below this luma, *raising Q is fully suppressed*.
            Dark scenes are fully protected.

        cvvdp_max_luma : float
            At and above this luma, full CVVDP-based Q raising is allowed.

        qadjust_min_q, qadjust_max_q : float
            Absolute CRF clamp.

        video_transfer : int
            Transfer characteristic.
            16 = HDR (PQ), anything else treated as SDR.

    Returns:
        list of dict
            [{'chunk': int, 'q': float}, ...]
    """

    # --- sort probe curve by Q ---
    cvvdp_curve = sorted(cvvdp_curve, key=lambda x: x['q'])

    # --- estimate local slope around cvvdp_q ---
    for a, b in zip(cvvdp_curve, cvvdp_curve[1:]):
        if a['q'] <= cvvdp_q <= b['q']:
            slope = (b['score'] - a['score']) / (b['q'] - a['q'])
            break
    else:
        # Fallback: use last segment (edge of curve)
        a, b = cvvdp_curve[-2], cvvdp_curve[-1]
        slope = (b['score'] - a['score']) / (b['q'] - a['q'])

    chunk_qs = []

    # --- perceptual tuning constants ---
    # These shape how quickly protection is released as luma increases
    log_k_hdr = 10.0   # HDR: stronger early protection near black
    gamma_sdr = 1.7    # SDR: softer but still conservative ramp

    for row in chunk_cvvdp_scores:
        chunk = row['chunk']
        s_i = row['score']
        average_luma = row.get('average_luma', 0.5)

        # --- base CVVDP-derived delta Q ---
        # Positive delta_q  -> raise Q (more compression)
        # Negative delta_q  -> lower Q (less compression)
        delta_q = -(s_i - qadjust_target) / slope
        q_base = cvvdp_q + delta_q

        # --- luma-based damping (ONLY when raising Q) ---
        if delta_q > 0:
            if average_luma <= cvvdp_min_luma:
                # Fully protect dark chunks
                luma_scale = 0.0

            elif average_luma >= cvvdp_max_luma:
                # Fully trust CVVDP
                luma_scale = 1.0

            else:
                # Normalize luma into [0, 1]
                x = (average_luma - cvvdp_min_luma) / (cvvdp_max_luma - cvvdp_min_luma)

                if video_transfer == 16:
                    # HDR (PQ):
                    # Logarithmic ramp keeps protection strong near black,
                    # releases it smoothly as luma increases.
                    luma_scale = math.log1p(log_k_hdr * x) / math.log1p(log_k_hdr)
                else:
                    # SDR:
                    # Power ramp reflects diminishing compression efficiency
                    # gains in dark regions.
                    luma_scale = x ** gamma_sdr

            delta_q *= luma_scale
            # NOTE: delta_q < 0 (quality increase) is intentionally never damped
        else:
            luma_scale = 1.0

        # --- apply adjustment ---
        q_i = cvvdp_q + delta_q

        # --- quantize to quarter-step CRF ---
        q_i = round(q_i * 4) / 4
        q_base = round(q_base * 4) / 4

        # --- clamp to allowed range ---
        q_i = max(qadjust_min_q, min(qadjust_max_q, q_i))
        q_base = max(qadjust_min_q, min(qadjust_max_q, q_base))

        chunk_qs.append({
            'chunk': chunk,
            'scale': luma_scale,
            'q_undamped': q_base,
            'q': q_i,
        })

    return chunk_qs


def show_qs(chunklist, reuse_qadjust):
    # Calculate and visualize the proportions by new q
    filtered_chunks = [chunk for chunk in chunklist if chunk['credits'] == 0]
    default_q = float(np.median([chunk['q'] for chunk in filtered_chunks]))
    length_by_q = defaultdict(int)
    weighted_sum = 0
    total_length = 0
    bar_width = 50
    proportion_range = 5
    for chunk in filtered_chunks:
        length_by_q[chunk['q']] += chunk['length']
        total_length += chunk['length']
        weighted_sum += chunk['q'] * chunk['length']
    weighted_crf = round(weighted_sum / total_length, 2)
    proportions = {q: length / total_length for q, length in length_by_q.items()}
    if default_q not in proportions:
        higher_candidates = [q for q in proportions if q > default_q]
        lower_candidates = [q for q in proportions if q < default_q]
        # Fallback logic
        if higher_candidates:
            default_q = min(higher_candidates)  # Use the next higher q
        elif lower_candidates:
            default_q = max(lower_candidates)  # Use the closest lower q
    higher = [(q, proportions[q]) for q in proportions if q > default_q]
    lower = [(q, proportions[q]) for q in proportions if q < default_q]
    higher = sorted(higher, key=lambda item: abs(item[0] - default_q))[:proportion_range]
    lower = sorted(lower, key=lambda item: abs(item[0] - default_q))[:proportion_range]
    higher = sorted(higher, key=lambda item: item[0], reverse=True)
    lower = sorted(lower, key=lambda item: item[0], reverse=True)
    selected_proportions = higher + [(default_q, proportions[default_q])] + lower
    print(f"New q values centered around the median q '{default_q:.2f}'")
    for q, proportion in selected_proportions:
        bar = '#' * int(proportion * bar_width)
        print(f"{q:.2f}: {bar.ljust(bar_width)} {proportion:.2%}")
    print(f"Final weighted CRF: {weighted_crf}")
    logger.info(f"Final weighted CRF: {weighted_crf}")
    if reuse_qadjust:
        return weighted_crf


def generate_probe_q_range(probes, qadjust_min_q, qadjust_max_q):
    gamma = 1.4
    qs = []
    for i in range(probes):
        t = i / (probes - 1)
        q = qadjust_min_q + (qadjust_max_q - qadjust_min_q) * (t ** (1 / gamma))
        qs.append(int(round(q)))

    return qs


def estimate_analysis_q(cvvdp_curve, qadjust_target):
    """
    probe_results: list of dicts with keys 'q' and 'score'
    target_score: desired global score (float)

    returns: analysis Q (float)
    """

    # Sort by score descending (higher quality first)
    probe_results = sorted(cvvdp_curve, key=lambda x: x['score'], reverse=True)

    for a, b in zip(cvvdp_curve, cvvdp_curve[1:]):
        sa, sb = a['score'], b['score']
        if sa >= qadjust_target >= sb:
            qa, qb = a['q'], b['q']
            q_float = qa + (qadjust_target - sa) * (qb - qa) / (sb - sa)
            return int(round(q_float))

    # Clamp if target is outside probed range
    if qadjust_target > probe_results[0]['score']:
        return int(probe_results[0]['q'])
    else:
        return int(probe_results[-1]['q'])


def terminate_all_processes():
    with process_list_lock:
        for p in active_processes:
            try:
                if p.poll() is None:  # Still running
                    print(f"Terminating process {p.pid}...")
                    p.terminate()
                    p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        active_processes.clear()


# noinspection PyUnboundLocalVariable
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('encode_script')
    parser.add_argument('--encoder', nargs='?', default='svt', type=str)
    parser.add_argument('--preset', nargs='?', default='1080p', type=str)
    parser.add_argument('--cpu', nargs='?', default=2, type=int)
    parser.add_argument('--threads', nargs='?', type=int)
    parser.add_argument('--q', nargs='?', type=float)
    parser.add_argument('--min-chunk-length', nargs='?', type=int)
    parser.add_argument('--max-parallel-encodes', nargs='?', default=4, type=int)
    parser.add_argument('--graintable-method', nargs='?', default=0, type=int)
    parser.add_argument('--graintable-sat', nargs='?', type=float)
    parser.add_argument('--graintable', nargs='?', type=str)
    parser.add_argument('--create-graintable', action='store_true')
    parser.add_argument('--scd-method', nargs='?', default=1, type=int)
    parser.add_argument('--scd-tonemap', nargs='?', default=0, type=int)
    parser.add_argument('--scdetect-only', action='store_true')
    parser.add_argument('--downscale-scd', nargs='?', default=2, type=int)
    parser.add_argument('--decode-method', nargs='?', default=1, type=int)
    parser.add_argument('--credits-start-frame', nargs='?', type=int)
    parser.add_argument('--credits-q', nargs='?', type=int)
    parser.add_argument('--credits-cpu', nargs='?', type=int)
    parser.add_argument('--graintable-cpu', nargs='?', type=int)
    parser.add_argument('--master-display', nargs='?', type=str)
    parser.add_argument('--max-cll', nargs='?', type=str)
    parser.add_argument('--extracl', nargs='?', type=str)
    parser.add_argument('--sample-start-frame', nargs='?', type=int)
    parser.add_argument('--sample-end-frame', nargs='?', type=int)
    parser.add_argument('--rpu', nargs='?', type=str)
    parser.add_argument('--cudasynth', action='store_true')
    parser.add_argument('--list-parameters', action='store_true')
    parser.add_argument('--qadjust', action='store_true')
    parser.add_argument('--qadjust-mode', nargs='?', default=3, type=int)
    parser.add_argument('--qadjust-reuse', action='store_true')
    parser.add_argument('--qadjust-only', action='store_true')
    parser.add_argument('--qadjust-b', nargs='?', default=-0.5, type=float)
    parser.add_argument('--qadjust-c', nargs='?', default=0.25, type=float)
    parser.add_argument('--qadjust-skip', nargs='?', type=int)
    parser.add_argument('--qadjust-cpu', nargs='?', default=7, type=int)
    parser.add_argument('--qadjust-workers', nargs='?', type=str)
    parser.add_argument('--qadjust-target', nargs='?', type=float)
    parser.add_argument('--qadjust-min-q', nargs='?', default=10.0, type=float)
    parser.add_argument('--qadjust-max-q', nargs='?', default=40.0, type=float)
    parser.add_argument('--cvvdp-min-luma', nargs='?', type=float)
    parser.add_argument('--cvvdp-max-luma', nargs='?', type=float)
    parser.add_argument('--probes', nargs='?', default=8, type=int)
    parser.add_argument('--cvvdp-model', nargs='?', type=str)
    parser.add_argument('--cvvdp-config', nargs='?', type=str)

    # Command-line arguments
    args = parser.parse_args()
    encode_script = args.encode_script
    encoder = args.encoder
    presets = args.preset.split(',')
    q = args.q
    min_chunk_length = args.min_chunk_length
    max_parallel_encodes = args.max_parallel_encodes
    threads = args.threads
    graintable = args.graintable
    graintable_method = args.graintable_method
    graintable_sat = args.graintable_sat
    scd_method = args.scd_method
    scd_tonemap = args.scd_tonemap
    scdetect_only = args.scdetect_only
    downscale_scd = args.downscale_scd
    cpu = args.cpu
    decode_method = args.decode_method
    credits_start_frame = args.credits_start_frame
    credits_q = args.credits_q
    credits_cpu = args.credits_cpu
    graintable_cpu = args.graintable_cpu
    master_display = args.master_display
    max_cll = args.max_cll
    extracl = args.extracl
    sample_start_frame = args.sample_start_frame
    sample_end_frame = args.sample_end_frame
    rpu = args.rpu
    cudasynth = args.cudasynth
    listparams = args.list_parameters
    create_graintable = args.create_graintable
    qadjust = args.qadjust
    qadjust_mode = args.qadjust_mode
    qadjust_only = args.qadjust_only
    qadjust_reuse = args.qadjust_reuse
    qadjust_b = args.qadjust_b
    qadjust_c = args.qadjust_c
    qadjust_skip = args.qadjust_skip
    qadjust_cpu = args.qadjust_cpu
    qadjust_workers = args.qadjust_workers
    qadjust_target = args.qadjust_target
    qadjust_min_q = args.qadjust_min_q
    qadjust_max_q = args.qadjust_max_q
    cvvdp_min_luma = args.cvvdp_min_luma
    cvvdp_max_luma = args.cvvdp_max_luma
    probes = args.probes
    cvvdp_model = args.cvvdp_model
    cvvdp_config = args.cvvdp_config
    extracl_dict = {}
    dovicl_dict = {}

    start_time_total = datetime.now()

    # Sanity checks of parameters, thanks to Python argparse being stupid if the allowed range is big
    if encode_script is None:
        print("You need to supply a script to encode.\n")
        sys.exit(1)
    if encoder not in ('rav1e', 'svt', 'aom', 'x265'):
        print("Valid encoder choices are rav1e, svt, aom or x265.\n")
        sys.exit(1)
    if encoder in ('svt', 'aom', 'x265') and 2 > q > 64:
        print("Q must be 2-64.\n")
        sys.exit(1)
    if encoder == 'rav1e' and 0 > q > 255:
        print("Q must be 0-255.\n")
        sys.exit(1)
    if -1 > cpu > 12:
        print("CPU must be -1..12.\n")
        sys.exit(1)
    if threads and 1 > threads > 64:
        print("Threads must be 1-64.\n")
        sys.exit(1)
    if min_chunk_length and 5 > min_chunk_length > 999999:
        print("Minimum chunk length must be 5-999999.\n")
        sys.exit(1)
    if 1 > max_parallel_encodes > 64:
        print("Maximum parallel encodes is 1-64.\n")
        sys.exit(1)
    if graintable_method and graintable_method not in (0, 1):
        print("Graintable method must be 0 or 1.\n")
        sys.exit(1)
    if graintable_sat and 0 > graintable_sat > 1:
        print("Graintable saturation must be 0-1.0.\n")
        sys.exit(1)
    if 0 > scd_method > 2:
        print("Scene change detection method must be 0, 1 or 2.\n")
        sys.exit(1)
    if scd_tonemap and scd_tonemap not in (0, 1):
        print("Scene change detection tonemap must be 0 or 1.\n")
        sys.exit(1)
    if 0 > downscale_scd > 8:
        print("Scene change detection downscale factor must be 0-8.\n")
        sys.exit(1)
    if decode_method and decode_method not in (0, 1):
        print("Decoding method must be 0 or 1.\n")
        sys.exit(1)
    if credits_q and encoder in ('svt', 'aom') and 2 > credits_q > 64:
        print("Q for credits must be 2-64.\n")
        sys.exit(1)
    if credits_q and encoder == 'rav1e' and 0 > credits_q > 255:
        print("Q for credits must be 0-255.\n")
        sys.exit(1)
    if credits_cpu and -1 > credits_cpu > 12:
        print("CPU for credits must be -1..12.\n")
        sys.exit(1)
    if graintable and graintable_cpu and -1 > graintable_cpu > 12:
        print("CPU for FGS analysis must be -1..12.\n")
        sys.exit(1)
    if qadjust and qadjust_cpu and -1 > qadjust_cpu > 12:
        print("CPU for qadjust must be -1..12.\n")
        sys.exit(1)
    if scdetect_only and scd_method == 0:
        print("You must select a scene change detection method.\n")
        sys.exit(1)
    if qadjust_min_q >= qadjust_max_q:
        print("Qadjust-min-q must be less than qadjust-max-q.\n")
        sys.exit(1)
    if cvvdp_min_luma and 0.0 <= cvvdp_min_luma > 0.99:
        print("Cvvdp-min-luma must be 0-0.99.\n")
        sys.exit(1)
    if cvvdp_max_luma and 0.01 >= cvvdp_max_luma > 1:
        print("Cvvdp-max-luma must be 0.01-1.0.\n")
        sys.exit(1)
    if cvvdp_min_luma and cvvdp_max_luma and cvvdp_min_luma >= cvvdp_max_luma:
        print("Cvvdp-min-luma must be less than cvvdp-max-luma.\n")
        sys.exit(1)
    try:
        if qadjust_workers:
            workers_str, threads_str = args.qadjust_workers.split(',')
            qadjust_workers = int(workers_str)
            qadjust_threads = int(threads_str)
            if qadjust_threads == 0:
                qadjust_threads = 1
        else:
            qadjust_workers = 1
            qadjust_threads = 1
    except ValueError:
        print("Invalid format for --qadjust-workers. Expected format: workers,threads (e.g., 2,4). Workers = how many parallel chunks calculated, threads = the numStream parameter in VSHIP.")
        sys.exit(1)

    # Check that needed executables can be found
    if shutil.which("ffmpeg.exe") is None:
        print("Unable to find ffmpeg.exe from PATH, exiting..\n")
        sys.exit(1)
    if encoder == 'rav1e':
        if shutil.which("rav1e.exe") is None:
            print("Unable to find rav1e.exe from PATH, exiting..\n")
            sys.exit(1)
    elif encoder == 'aom':
        if shutil.which("aomenc.exe") is None:
            print("Unable to find aomenc.exe from PATH, exiting..\n")
            sys.exit(1)
    elif encoder == 'svt':
        if shutil.which("svtav1encapp.exe") is None:
            print("Unable to find svtav1encapp.exe from PATH, exiting..\n")
            sys.exit(1)
    else:
        if shutil.which("x265.exe") is None:
            print("Unable to find x265.exe from PATH, exiting..\n")
            sys.exit(1)
    if decode_method == 1:
        if shutil.which("avs2yuv64.exe") is None:
            print("Unable to find avs2yuv64.exe from PATH, exiting..\n")
            sys.exit(1)
    if scd_method == 1:
        if shutil.which("av-scenechange.exe") is None:
            print("Unable to find av-scenechange.exe from PATH, exiting..\n")
            sys.exit(1)
    if graintable_method == 1 or create_graintable:
        if shutil.which("grav1synth.exe") is None:
            print("Unable to find grav1synth.exe from PATH, exiting..\n")
            sys.exit(1)

    # Store the full path of encode_script
    encode_script = os.path.abspath(encode_script)

    # Get video props from the source
    video_width, video_height, video_length, video_transfer, video_matrix, video_primaries, video_framerate, fr, chroma_location = get_video_props(encode_script)

    if credits_start_frame and credits_start_frame >= video_length - 1:
        print("The credits cannot start at or after the end of video.\n")
        sys.exit(1)
    if (sample_start_frame and sample_start_frame >= video_length - 1) or (sample_end_frame and sample_end_frame >= video_length - 1) or (sample_start_frame and sample_end_frame and sample_start_frame >= sample_end_frame):
        print("Please check the sample range.\n")
        sys.exit(1)

    # Set scene change helper file to use the same path as the original source script
    scene_change_file_path = os.path.dirname(encode_script)

    # Set some more case dependent default values
    if graintable:
        graintable_method = 0
    if credits_cpu is None:
        credits_cpu = cpu + 2
        if credits_cpu > 12:
            credits_cpu = 12
        elif credits_cpu < 4:
            credits_cpu = 4
    if graintable_cpu is None:
        graintable_cpu = cpu
    if credits_q is None and encoder == 'rav1e':
        credits_q = 180
    elif credits_q is None and encoder in ('svt', 'aom'):
        credits_q = 40
    elif credits_q is None and encoder == 'x265':
        credits_q = q + 8
    if q is None:
        if encoder == 'rav1e':
            q = 60
        else:
            q = 18
    if threads is None:
        if encoder == 'svt':
            threads = 4
        else:
            threads = 6
    if graintable_sat is None:
        if encoder in ('svt', 'rav1e'):
            graintable_sat = 1
        else:
            graintable_sat = 0
    # Change the master-display and max-cll parameters to svt-av1 format if needed
    if master_display:
        if max_cll is None:
            max_cll = "0,0"
        if encoder != 'x265':
            master_display, max_cll = parse_master_display(master_display, max_cll)
    if shutil.which("mkvmerge.exe") is not None:
        use_mkvmerge = True
    else:
        use_mkvmerge = False
    if rpu and use_mkvmerge is False:
        print("Dolby Vision mode cannot be used if mkvmerge is not available, exiting..\n")
        sys.exit(1)
    if qadjust:
        qadjust_cycle = 1
        if encoder != 'svt' and qadjust_mode == 2:
            print("You can use qadjust-mode 2 only with SVT-AV1.")
            sys.exit(0)
        if encoder not in ('svt', 'x265'):
            encoder = 'svt'
            print("\nQadjust enabled and encoder not svt or x265, encoder set to SVT-AV1.")
    else:
        qadjust_cycle = -1
    if encoder == 'svt':
        br = math.ceil(q * 0.125)
    else:
        br = 2
    if not cvvdp_min_luma:
        if video_transfer != 16:
            cvvdp_min_luma = 0.1
        else:
            cvvdp_min_luma = 0.05
    if not cvvdp_max_luma:
        if video_transfer != 16:
            cvvdp_max_luma = 0.25
        else:
            cvvdp_max_luma = 0.17

    # Collect default values from commandline parameters
    if encoder == 'rav1e':
        default_values = {
            "speed": cpu,
            "quantizer": q,
            "keyint": video_framerate * 10,
            "threads": threads,
        }
        if master_display:
            default_values['mastering-display'] = '"' + master_display + '"'
            default_values['content-light'] = max_cll
    elif encoder == 'svt':
        default_values = {
            "preset": cpu,
            "lp": threads,
            "color-primaries": video_primaries,
            "transfer-characteristics": video_transfer,
            "matrix-coefficients": video_matrix,
            "chroma-sample-position": chroma_location
        }
        if master_display:
            default_values['mastering-display'] = '"' + master_display + '"'
            default_values['content-light'] = max_cll
    elif encoder == 'x265':
        default_values = {
            "log-level": -1,
            "pools": threads,
            "min-keyint": video_framerate * 10,
            "keyint": video_framerate * 10,
        }
        if master_display:
            default_values['master-display'] = '"' + master_display + '"'
            default_values['max-cll'] = '"' + max_cll + '"'
        if video_transfer == 16:
            default_values['colorprim'] = 9
            default_values['transfer'] = 16
            default_values['colormatrix'] = 9
            default_values['chromaloc'] = 2
            default_values['hdr10'] = ""
            default_values['hdr10-opt'] = ""
            default_values['repeat-headers'] = ""
    else:
        default_values = {
            "cpu-used": cpu,
            "cq-level": q,
            "threads": threads,
            "kf-max-dist": video_framerate * 10,
            "chroma-q-offset-u": -q + 2,
            "chroma-q-offset-v": -q + 2,
        }

    default_params, preset_params, base_working_folder, preset_config_json = read_presets(presets, encoder)
    encode_params = {**default_values, **default_params, **preset_params}

    if qadjust_mode == 3:
        if not cvvdp_config:
            if os.path.isfile(preset_config_json):
                cvvdp_config = preset_config_json
        if not cvvdp_model:
            if video_transfer != 16:
                cvvdp_model = 'standard_4k'
            else:
                cvvdp_model = 'standard_hdr_pq'

    if rpu and encoder == 'svt':
        print("Dolby Vision mode detected, please note that it is experimental.\n")
    elif rpu and encoder == 'x265':
        print("Dolby Vision mode detected, VBV enabled and Level 5.1 set.\n")
        dovicl = "--level-idc 5.1 --dolby-vision-profile 8.1 --vbv-bufsize 160000 --vbv-maxrate 160000"
        dovicl = shlex.split(dovicl)
        i = 0
        while i < len(dovicl):
            arg = dovicl[i]
            if arg.startswith('--'):
                key = arg[2:]
                if i + 1 < len(dovicl) and not dovicl[i + 1].startswith('--'):
                    value = dovicl[i + 1]
                    dovicl_dict[key] = value
                    i += 2
                else:
                    dovicl_dict[key] = ''
                    i += 1
            else:
                i += 1
        encode_params.update(dovicl_dict)

    if extracl:
        # A workaround in case extracl only contains one parameter
        if not extracl.endswith(" "):
            extracl += " "
        extracl = shlex.split(extracl)
        i = 0
        while i < len(extracl):
            arg = extracl[i]
            if arg.startswith('--'):
                key = arg[2:]
                if i + 1 < len(extracl) and not extracl[i + 1].startswith('--'):
                    value = extracl[i + 1]
                    extracl_dict[key] = value
                    i += 2
                else:
                    extracl_dict[key] = ''
                    i += 1
            else:
                i += 1
        encode_params.update(extracl_dict)

    if encoder == 'svt':
        startup_mg_size = encode_params.get("startup-mg-size")
        hierarchical_levels = encode_params.get("hierarchical-levels")

        if hierarchical_levels is not None:
            hierarchical_levels = int(hierarchical_levels)
        else:
            if cpu <= 12:
                hierarchical_levels = 6
            else:
                hierarchical_levels = 5
        if startup_mg_size is not None:
            startup_mg_size = int(startup_mg_size)

        if hierarchical_levels == 2:
            hierarchical_levels = 3
        elif hierarchical_levels == 3:
            hierarchical_levels = 4
        elif hierarchical_levels == 4:
            hierarchical_levels = 5
        else:
            hierarchical_levels = 6
        if startup_mg_size is None or startup_mg_size == 0:
            startup_mg_size = hierarchical_levels
        elif startup_mg_size == 2:
            startup_mg_size = 3
        elif startup_mg_size == 3:
            startup_mg_size = 4
        else:
            startup_mg_size = 5

        # minkeyint = calculate_svt_keyint(video_framerate, 10, int(startup_mg_size), int(hierarchical_levels))
        minkeyint = -2
        encode_params["keyint"] = minkeyint
    else:
        minkeyint = video_framerate * 10

    if not min_chunk_length:
        if encoder == 'svt':
            min_chunk_length = calculate_svt_keyint(video_framerate, 2, int(startup_mg_size), int(hierarchical_levels))
        else:
            min_chunk_length = math.ceil(video_framerate) * 2
        print(f"Calculated minimum chunk length is {min_chunk_length} frames.")
        logger.info(f"Calculated minimum chunk length is {min_chunk_length} frames.")

    # Create a list of non-empty parameters in the encoder supported format
    if encoder in ('svt', 'rav1e', 'x265'):
        encode_params = [f"--{key} {value}" for key, value in encode_params.items() if value is not None]
    else:
        encode_params = [f"--{key}={value}" for key, value in encode_params.items() if value is not None]

    if listparams:
        print("\nYour working folder:", base_working_folder)
        print("\nThe default values from the Chunk Norris script:\n")
        for key, value in default_values.items():
            print(key, value)
        print("\nThe default values from presets.ini:\n")
        for key, value in default_params.items():
            print(key, value)
        print("\nThe combined values from the selected preset(s):\n")
        for key, value in preset_params.items():
            print(key, value)
        if extracl:
            print("\nParameters from extracl:\n")
            for key, value in extracl_dict.items():
                print(key, value)
        if rpu and encoder == 'x265':
            print("\nParameters from DoVi mode:\n")
            for key, value in dovicl_dict.items():
                print(key, value)
        # if encoder == 'svt':
            # print("\nKeyint calculated from --startup-mg-size and --hierachical-levels:")
            # print("First mini-GOP hierarchical layers " + str(startup_mg_size) + ", following hierarchical layers " + str(hierarchical_levels) + ", keyint (minimum 10 seconds) " + str(minkeyint) + " frames")
        print("\nAll encoding parameters combined:\n")
        encode_params = " ".join(encode_params)
        encode_params = encode_params.replace('  ', ' ')
        print(encode_params)
        sys.exit(0)

    # Determine the output folder name based on the encode_script
    output_folder_name = os.path.splitext(os.path.basename(encode_script))[0]
    output_folder_name = os.path.join(base_working_folder, output_folder_name)

    # Naming for the Avisynth scripts, encoded chunks, and output folders and base filename
    output_folder = os.path.join(output_folder_name, "output")
    scripts_folder = os.path.join(output_folder_name, "scripts")
    chunks_folder = os.path.join(output_folder_name, "chunks")
    output_name = os.path.splitext(os.path.basename(encode_script))[0]

    # Define final video file name
    if encoder != 'x265':
        output_final = os.path.join(output_folder, f"{output_name}.mkv")
    elif encoder == 'x265' and use_mkvmerge:
        output_final = os.path.join(output_folder, f"{output_name}.mkv")
    else:
        output_final = os.path.join(output_folder, f"{output_name}.mp4")

    # Qadjust related variables
    qadjust_results_file = os.path.join(output_folder, f"{output_name}_qadjust.json")
    qadjust_data = {}
    reuse_qadjust = False
    if qadjust_only:
        qadjust_reuse = False

    # Clean up the target folder if it already exists, keep data from the qadjust analysis file if requested
    if os.path.exists(output_folder_name) and not sample_start_frame and not sample_end_frame and not create_graintable:
        if os.path.exists(qadjust_results_file):
            if qadjust_reuse:
                qadjust_cycle = -1
            if os.path.exists(qadjust_results_file) and not qadjust_only and qadjust:
                if not qadjust_reuse:
                    user_choice = input("\nThe qadjust analysis file already exists. Would you like to use the existing results (Y/Enter) or recreate the file (any other key)? ").strip().lower()
                    if user_choice == 'y' or user_choice == '':
                        reuse_qadjust = True
                else:
                    reuse_qadjust = True
                if reuse_qadjust:
                    with open(qadjust_results_file, 'r') as file:
                        qadjust_data = json.load(file)
                        if 'min_chunk_length' in qadjust_data and qadjust_data['min_chunk_length'] != min_chunk_length:
                            print("The current minimum chunk length does not match the qadjust JSON data file, you must rerun the analysis.")
                            sys.exit(0)
                        if not qadjust_target:
                            while True:
                                try:
                                    if qadjust_mode == 2:
                                        qadjust_target = float(input("\nPlease enter the target value for Butteraugli (for example 1.4): "))
                                    elif qadjust_mode == 3:
                                        qadjust_target = float(input("\nPlease enter the target value for CVVDP (for example 9.8): "))
                                    break
                                except ValueError:
                                    print("Invalid input. Please enter a numeric (float) value.")
                        print(f"Recalculating the CRFs based on existing analysis data and target score {qadjust_target}.")
                        chunklist = []
                        if qadjust_mode == 2:
                            for i in qadjust_data['chunks']:
                                butter_scores_pass1.append(i['butteraugli_pass1'])
                                butter_scores_pass2.append(i['butteraugli_pass2'])
                                chunkdata = {'chunk': i['chunk_number'], 'credits': 0, 'q': i['crf_pass2']}
                                chunklist.append(chunkdata)
                            crfs = adjust_crf_butteraugli(butter_scores_pass1, butter_scores_pass2, qadjust_target, qadjust_cpu, cpu, q, chunklist)
                            for idx, chunk in enumerate(qadjust_data['chunks']):
                                chunk['adjusted_Q'] = crfs[idx]
                            qadjust_data['qadjust_target'] = qadjust_target
                        elif qadjust_mode == 3:
                            cvvdp_q = qadjust_data['analysis_q']
                            cvvdp_curve = qadjust_data['cvvdp']['curve']
                            chunk_cvvdp_scores = [
                                {
                                    "chunk": i['chunk_number'],
                                    "score": i['cvvdp_score'],
                                    "average_luma": i.get('average_luma', 0.5)
                                }
                                for i in qadjust_data['chunks']
                            ]
                            new_crfs = adjust_crf_cvvdp(chunk_cvvdp_scores, cvvdp_q, qadjust_target, cvvdp_curve, cvvdp_min_luma, cvvdp_max_luma, qadjust_min_q, qadjust_max_q, video_transfer)
                            new_q_by_chunk = {i['chunk']: i['q'] for i in new_crfs}
                            scale_by_chunk = {i['chunk']: i['scale'] for i in new_crfs}
                            base_q_by_chunk = {i['chunk']: i['q_undamped'] for i in new_crfs}
                            for i, chunk in enumerate(qadjust_data['chunks']):
                                chunk_number = chunk['chunk_number']
                                qadjust_data['chunks'][i] = {
                                    "chunk_number": chunk_number,
                                    "length": chunk['length'],
                                    "cvvdp_score": chunk['cvvdp_score'],
                                    "average_luma": chunk['average_luma'],
                                    "luma_scale": scale_by_chunk[chunk_number],
                                    "undamped_Q": base_q_by_chunk[chunk_number],
                                    "adjusted_Q": new_q_by_chunk[chunk_number]
                                }
                            qadjust_data['target'] = qadjust_target
                            qadjust_data['cvvdp_q_scaling_min_luma'] = cvvdp_min_luma
                            qadjust_data['cvvdp_q_scaling_max_luma'] = cvvdp_max_luma
        else:
            qadjust_reuse = False
        print(f"Cleaning up the existing folder: {output_folder_name}\n")
        clean_folder(output_folder_name)
    else:
        qadjust_reuse = False

    # Create directories if they don't exist
    os.makedirs(output_folder, exist_ok=True)
    os.makedirs(scripts_folder, exist_ok=True)
    os.makedirs(chunks_folder, exist_ok=True)

    encode_log_file = os.path.join(output_folder, f"encode_log.txt")
    file_handler = logging.FileHandler(encode_log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    buffer.seek(0)
    for line in buffer:
        file_handler.stream.write(line)
    file_handler.flush()
    logger.removeHandler(buffer_handler)

    if reuse_qadjust:
        logger.info("Processed existing qadjust data from the JSON file.")
        with open(qadjust_results_file, 'w') as results_file:
            json.dump(qadjust_data, results_file, indent=4)

    # Grain table creation, only for AV1
    if encoder != 'x265' and not qadjust_only and (create_graintable or graintable):
        # Generate the FGS analysis file names
        output_grain_file_lossless = os.path.join(output_folder, f"{output_name}_lossless.mkv")
        output_grain_file_encoded = os.path.join(output_folder, f"{output_name}_encoded.ivf")
        output_grain_table = os.path.split(encode_script)[0]
        output_grain_table_baseline = os.path.join(output_grain_table, f"{output_name}_grain_baseline.tbl")
        output_grain_table = os.path.join(output_grain_table, f"{output_name}_grain.tbl")

        start_time = datetime.now()
        if create_graintable:
            try:
                create_fgs_table(encode_params, output_grain_table, scripts_folder, video_width, encode_script, graintable_sat, decode_method, encoder, threads, cpu, graintable_cpu, output_grain_file_encoded, output_grain_file_lossless,
                                 output_grain_table_baseline)
                end_time = datetime.now()
                graintable_time = end_time - start_time
                logger.info(f"Graintable created, path {output_grain_table}. Duration {graintable_time}.")
                print("Graintable created successfully, the path is:", output_grain_table)
            except Exception as e:
                logger.error(f"Graintable creation failed, please check your script and settings. Exception code {e}")
                print("Graintable creation failed, please check your script and settings.\n")
                sys.exit(1)
            sys.exit(0)
        # Create the reference files for FGS
        if encoder == 'svt':
            if graintable_method > 0:
                create_fgs_table(encode_params, output_grain_table, scripts_folder, video_width, encode_script, graintable_sat, decode_method, encoder, threads, cpu, graintable_cpu, output_grain_file_encoded, output_grain_file_lossless,
                                 output_grain_table_baseline)
                end_time = datetime.now()
                graintable_time = end_time - start_time
                logger.info(f"Graintable created, path {output_grain_table}. Duration {graintable_time}.")
                if qadjust_cycle != 1:
                    encode_params.append(f"--fgs-table \"{output_grain_table}\"")
            elif graintable:
                if qadjust_cycle != 1:
                    encode_params.append(f"--fgs-table \"{graintable}\"")
        elif encoder == 'rav1e':
            if graintable_method > 0:
                create_fgs_table(encode_params, output_grain_table, scripts_folder, video_width, encode_script, graintable_sat, decode_method, encoder, threads, cpu, graintable_cpu, output_grain_file_encoded, output_grain_file_lossless,
                                 output_grain_table_baseline)
                end_time = datetime.now()
                graintable_time = end_time - start_time
                logger.info(f"Graintable created, path {output_grain_table}. Duration {graintable_time}.")
                encode_params.append(f"--film-grain-table \"{output_grain_table}\"")
            elif graintable:
                encode_params.append(f"--film-grain-table \"{graintable}\"")
        else:
            if graintable_method > 0:
                create_fgs_table(encode_params, output_grain_table, scripts_folder, video_width, encode_script, graintable_sat, decode_method, encoder, threads, cpu, graintable_cpu, output_grain_file_encoded, output_grain_file_lossless,
                                 output_grain_table_baseline)
                end_time = datetime.now()
                graintable_time = end_time - start_time
                logger.info(f"Graintable created, path {output_grain_table}. Duration {graintable_time}.")
                encode_params.append(f"--film-grain-table=\"{output_grain_table}\"")
            elif graintable:
                encode_params.append(f"--film-grain-table=\"{graintable}\"")

    # Encode only the sample if start and end frames are supplied, exit afterwards.
    if sample_start_frame is not None and sample_end_frame is not None:
        encode_sample(output_folder, encode_script, encode_params, rpu, encoder, sample_start_frame, sample_end_frame, video_length, decode_method, threads, q)
        sys.exit(0)

    # Detect scene changes
    scd_script = os.path.splitext(os.path.basename(encode_script))[0] + "_scd.avs"
    scd_script = os.path.join(os.path.dirname(encode_script), scd_script)
    if scd_method == 1:
        scene_change_csv = os.path.join(output_folder_name, f"scene_changes_{os.path.splitext(os.path.basename(encode_script))[0]}.json")
        create_avscenechange_file(scene_change_csv, encode_script, scd_script, scene_change_file_path, downscale_scd)
    elif scd_method == 2:
        scene_change_csv = os.path.join(output_folder_name, f"scene_changes_{os.path.splitext(os.path.basename(encode_script))[0]}.csv")
        create_scxvid_file(scene_change_csv, scd_tonemap, encode_script, cudasynth, downscale_scd, scd_script, scene_change_file_path)
    scene_changes = convert_qp_to_scene_changes(encode_script)
    logger.info("Finished processing the scene change data.")
    if scdetect_only:
        print("Scene change detection complete.\n")
        sys.exit(0)

    # Create the AVS scripts, prepare encoding and concatenation commands for chunks
    encode_commands = []  # List to store the encoding commands
    input_files = []  # List to store input files for concatenation
    chunklist = []  # Helper list for producing the encoding and concatenation lists
    qadjust_original_file = os.path.join(scripts_folder, f"qadjust_original.avs")

    # Run encoding commands with a set maximum of concurrent processes
    stored_encode_params = encode_params.copy()
    encode_commands, input_files, chunklist, chunklist_dict, encode_params = preprocess_chunks(encode_commands, input_files, chunklist, qadjust_cycle, stored_encode_params, scene_changes, video_length, credits_start_frame, min_chunk_length, q,
                                                                                                   credits_q, encoder, chunks_folder, rpu, qadjust_cpu, encode_script, qadjust_original_file,video_width, video_height, qadjust_b, qadjust_c,
                                                                                                   scripts_folder, decode_method, cpu, credits_cpu, qadjust_reuse)

    encode_params_displist = " ".join(encode_params)
    encode_params_displist = encode_params_displist.replace('  ', ' ')
    encode_params_displist = encode_params_displist.replace(f' --crf {q}', '')

    if qadjust or qadjust_only:
        if reuse_qadjust:
            reused_q_values = [chunk['adjusted_Q'] for chunk in qadjust_data['chunks']]
            chunklist = sorted(chunklist, key=lambda x: x['chunk'], reverse=False)
            for i in range(len(chunklist)):
                if chunklist[i]['credits'] == 1:
                    continue
                chunklist[i]['q'] = reused_q_values[i]
            logger.info("Updated CRFs based on the qadjust data.")
            weighted_crf = show_qs(chunklist, True)
            with open(qadjust_results_file, 'r') as file:
                qadjust_data = json.load(file)
            qadjust_data = {
                **{k: v for k, v in qadjust_data.items() if k != "chunks"},
                "weighted_crf": weighted_crf,
                "chunks": qadjust_data["chunks"]
            }
            with open(qadjust_results_file, 'w') as file:
                json.dump(qadjust_data, file, indent=4)
            chunklist = sorted(chunklist, key=lambda x: x['length'], reverse=True)
            qadjust_cycle = 2
        else:
            if not qadjust_skip:
                if (video_width * video_height) > 921600:
                    if qadjust_mode == 3:
                        qadjust_skip = 2
                    else:
                        qadjust_skip = 3
                else:
                    qadjust_skip = 1
            logger.info("Set up chunklist and corresponding encode commands for the Q adjust phase.")
            # mode 1 = SSIMU2
            if qadjust_mode == 1:
                logger.info(f"Started analysis using SSIMULACRA2 using CRF {q}.")
                if encoder != 'x265':
                    output_final_metrics = os.path.join(chunks_folder, f"encoded_chunk_.ivf")
                else:
                    output_final_metrics = os.path.join(chunks_folder, f"encoded_chunk_.hevc")
                print("The encoder parameters for the analysis:", encode_params_displist)
                print("Running the analysis pass using your final CRF value.")
                run_encode(qadjust_cycle, chunklist, video_length, fr, max_parallel_encodes, encode_commands, chunklist_dict, reuse_qadjust, start_time = datetime.now())
                chunklist = calculate_metrics(chunklist, qadjust_skip, qadjust_original_file, output_final_metrics, encoder, q, br, qadjust_results_file, video_matrix, video_transfer, video_primaries, chroma_location, qadjust_workers, qadjust_threads, qadjust_mode,
                                              qadjust_cpu, cpu, qadjust_target,avg_bitrate, 0,'ssimu2', min_chunk_length, minkeyint, [], 0, 0, 0, qadjust_min_q,
                                              qadjust_max_q, cvvdp_model, cvvdp_config)
            # mode 2 = Butteraugli with two passes
            elif qadjust_mode == 2:
                if not qadjust_target:
                    while True:
                        try:
                            qadjust_target = float(input("\nPlease enter the target value for Butteraugli (for example 1.4): "))
                            break
                        except ValueError:
                            print("Invalid input. Please enter a numeric (float) value.")

                modified_encode_commands = []

                for avs_cmd, enc_cmd, out_path in encode_commands:
                    new_avs_cmd = [arg.replace('encoded_chunk_', 'encoded_chunk_pass1_') for arg in avs_cmd]

                    new_enc_cmd = [
                        (f'--crf 24.0' if arg.startswith('--crf ') else arg.replace('encoded_chunk_', 'encoded_chunk_pass1_'))
                        for arg in enc_cmd
                    ]

                    new_out_path = out_path.replace('encoded_chunk_', 'encoded_chunk_pass1_')

                    modified_encode_commands.append((new_avs_cmd, new_enc_cmd, new_out_path))
                print("The encoder parameters for the analysis:", encode_params_displist)
                logger.info(f"Started analysis using Butteraugli, pass 1 at CRF 24.0.")
                print(f"Running the analysis pass 1 at CRF 24.0.")
                run_encode(qadjust_cycle, chunklist, video_length, fr, max_parallel_encodes, modified_encode_commands, chunklist_dict, reuse_qadjust, start_time = datetime.now())
                avg_bitrate_qadjust_pass1 = avg_bitrate
                if encoder != 'x265':
                    output_final_metrics = os.path.join(chunks_folder, f"encoded_chunk_pass1_.ivf")
                else:
                    output_final_metrics = os.path.join(chunks_folder, f"encoded_chunk_pass1_.hevc")
                chunklist = calculate_metrics(chunklist, qadjust_skip, qadjust_original_file, output_final_metrics, encoder, q, br, qadjust_results_file, video_matrix, video_transfer, video_primaries, chroma_location, qadjust_workers,
                                              qadjust_threads, qadjust_mode, qadjust_cpu, cpu, qadjust_target, 0, avg_bitrate_qadjust_pass1,'butter_pass1', min_chunk_length, minkeyint, [], 0,
                                              0, 0, qadjust_min_q, qadjust_max_q, cvvdp_model, cvvdp_config)
                modified_encode_commands = []

                filtered_chunks = [c for c in chunklist if c.get("credits", 0) != 1]
                for chunk, (avs_cmd, enc_cmd, out_path) in zip(filtered_chunks, encode_commands):
                    new_avs_cmd = [arg.replace('encoded_chunk_', 'encoded_chunk_pass2_') for arg in avs_cmd]
                    new_out_path = out_path.replace('encoded_chunk_', 'encoded_chunk_pass2_')
                    if chunk["credits"] == 1:
                        # Keep original encoder command for credits chunks
                        continue
                    else:
                        # Replace CRF with per-chunk q and adjust filenames
                        new_enc_cmd = [
                            (f'--crf {chunk["q"]}' if arg.startswith('--crf ')
                             else arg.replace('encoded_chunk_', 'encoded_chunk_pass2_'))
                            for arg in enc_cmd
                        ]

                    modified_encode_commands.append((new_avs_cmd, new_enc_cmd, new_out_path))
                logger.info("Started analysis using Butteraugli, pass 2.")
                print("Running the analysis pass 2.")
                run_encode(qadjust_cycle, chunklist, video_length, fr, max_parallel_encodes, modified_encode_commands, chunklist_dict, reuse_qadjust, start_time = datetime.now())
                if encoder != 'x265':
                    output_final_metrics = os.path.join(chunks_folder, f"encoded_chunk_pass2_.ivf")
                else:
                    output_final_metrics = os.path.join(chunks_folder, f"encoded_chunk_pass2_.hevc")
                chunklist = calculate_metrics(chunklist, qadjust_skip, qadjust_original_file, output_final_metrics, encoder, q, br, qadjust_results_file, video_matrix, video_transfer, video_primaries, chroma_location, qadjust_workers,
                                              qadjust_threads, qadjust_mode, qadjust_cpu, cpu, qadjust_target, 0, avg_bitrate_qadjust_pass1,'butter_pass2', min_chunk_length, minkeyint, [], 0,
                                              0, 0, qadjust_min_q, qadjust_max_q, cvvdp_model, cvvdp_config)
            # CVVDP
            else:
                if not qadjust_target:
                    while True:
                        try:
                            qadjust_target = float(input("\nPlease enter the target value for CVVDP (for example 9.8): "))
                            break
                        except ValueError:
                            print("Invalid input. Please enter a numeric (float) value.")

                logger.info("Started analysis using CVVDP.")
                print("Started analysis using CVVDP.")

                cvvdp_curve = []
                probe_round = 1
                probe_qs = generate_probe_q_range(probes, qadjust_min_q, qadjust_max_q)
                probe_encode_commands, probe_chunklist, probe_chunklist_dict, encode_params = preprocess_probe_chunks(stored_encode_params, video_length, credits_start_frame, encoder, chunks_folder, qadjust_cpu, encode_script, qadjust_original_file,
                                                                                                                      video_width, video_height, qadjust_b, qadjust_c, scripts_folder, decode_method, minkeyint)
                encode_params_displist = " ".join(encode_params)
                encode_params_displist = encode_params_displist.replace('  ', ' ')
                encode_params_displist = encode_params_displist.replace(f' --crf 0', '')
                if encoder != 'x265':
                    output_final_metrics = os.path.join(chunks_folder, "encoded_chunk_probe_.ivf")
                else:
                    output_final_metrics = os.path.join(chunks_folder, "encoded_chunk_probe_.hevc")
                print("The encoder parameters for the analysis:", encode_params_displist)
                logger.info(f"Running {probes} probes at Q {probe_qs} to predict a Q/score curve.")
                print(f"Running {probes} probes at Q {probe_qs} to predict a Q/score curve.")
                qadjust_cycle = 1
                while probe_round <= probes:
                    probe_q = probe_qs[probe_round - 1]
                    for _, enc_cmd, _ in probe_encode_commands:
                        for i, arg in enumerate(enc_cmd):
                            if arg.startswith('--crf'):
                                enc_cmd[i] = f'--crf {probe_q}'
                                break
                    logger.info(f'Probing Q: {probe_q}')
                    print(f'\nProbing Q: {probe_q}')
                    run_encode(qadjust_cycle, probe_chunklist, video_length, fr, max_parallel_encodes, probe_encode_commands, probe_chunklist_dict, reuse_qadjust, start_time=datetime.now())
                    score = calculate_metrics(probe_chunklist, qadjust_skip, qadjust_original_file, output_final_metrics, encoder, q, br, qadjust_results_file, video_matrix, video_transfer, video_primaries, chroma_location, qadjust_workers,
                                              qadjust_threads, qadjust_mode, qadjust_cpu, cpu, qadjust_target, avg_bitrate, 0, 'cvvdp_probing', min_chunk_length, minkeyint, cvvdp_curve, 0,
                                              cvvdp_min_luma, cvvdp_max_luma, qadjust_min_q, qadjust_max_q, cvvdp_model, cvvdp_config)

                    cvvdp_curve.append({'q': probe_q, 'avg_bitrate': avg_bitrate, 'score': score})
                    probe_round += 1

                if encoder != 'x265':
                    output_final_metrics = os.path.join(chunks_folder, "encoded_chunk_.ivf")
                else:
                    output_final_metrics = os.path.join(chunks_folder, "encoded_chunk_.hevc")
                cvvdp_q = estimate_analysis_q(cvvdp_curve, qadjust_target)
                logger.info(f"Running a full analysis at Q {cvvdp_q} to adjust Q per chunk using the curve.")
                print(f"\nRunning a full analysis at Q {cvvdp_q} to adjust Q per chunk using the curve.")
                for _, enc_cmd, _ in encode_commands:
                    for i, arg in enumerate(enc_cmd):
                        if arg.startswith('--crf'):
                            enc_cmd[i] = f'--crf {cvvdp_q}'
                        if arg.startswith('--preset'):
                            if encoder == 'svt':
                                enc_cmd[i] = f'--preset {qadjust_cpu}'
                            else:
                                enc_cmd[i] = ''
                        if encoder == 'x265':
                            if arg.startswith('--hme '):
                                enc_cmd[i] = '--no-hme'
                            if arg.startswith('--subme '):
                                enc_cmd[i] = '--subme 3'
                            if arg.startswith('--ref '):
                                enc_cmd[i] = '--ref 3'
                            if arg.startswith('--merange '):
                                enc_cmd[i] = '--merange 25'
                run_encode(qadjust_cycle, chunklist, video_length, fr, max_parallel_encodes, encode_commands, chunklist_dict, reuse_qadjust, start_time=datetime.now())
                chunklist = calculate_metrics(chunklist, qadjust_skip, qadjust_original_file, output_final_metrics, encoder, q, br, qadjust_results_file, video_matrix, video_transfer, video_primaries, chroma_location, qadjust_workers,
                                              qadjust_threads, qadjust_mode, qadjust_cpu, cpu, qadjust_target, avg_bitrate, 0,'cvvdp', min_chunk_length, minkeyint, cvvdp_curve, cvvdp_q,
                                              cvvdp_min_luma, cvvdp_max_luma, qadjust_min_q, qadjust_max_q, cvvdp_model, cvvdp_config)

            if qadjust_only:
                try:
                    clean_files(chunks_folder, 'encoded')
                except Exception as e:
                    print(f"Unable to remove the intermediate files, exception {e}")
                    logger.warning(f"Unable to remove the intermediate files, exception {e}")
                sys.exit(0)

        if not qadjust_reuse:
            qadjust_cycle = 2
        try:
            clean_files(chunks_folder, 'encoded')
        except Exception as e:
            print(f"Unable to remove the intermediate files, exception {e}")
            logger.warning(f"Unable to remove the intermediate files, exception {e}")
        encode_commands = []
        input_files = []
        encode_commands, input_files, chunklist, chunklist_dict, encode_params = preprocess_chunks(encode_commands, input_files, chunklist, qadjust_cycle, stored_encode_params, scene_changes, video_length, credits_start_frame, min_chunk_length, q,
                                                                                                   credits_q, encoder, chunks_folder, rpu, qadjust_cpu, encode_script, qadjust_original_file, video_width, video_height, qadjust_b, qadjust_c,
                                                                                                   scripts_folder, decode_method, cpu, credits_cpu, qadjust_reuse)
        encode_params_displist = " ".join(encode_params)
        encode_params_displist = encode_params_displist.replace('  ', ' ')
        print("The encoder parameters for the final encode:", encode_params_displist)
        print("\n")
        qadjust_cycle = -1
        run_encode(qadjust_cycle, chunklist, video_length, fr, max_parallel_encodes, encode_commands, chunklist_dict, reuse_qadjust, start_time = datetime.now())
        if not master_display:
            master_display = None
        if not max_cll:
            max_cll = None
        concatenate(chunks_folder, input_files, output_final, fr, use_mkvmerge, encoder, video_matrix, video_transfer, video_primaries, chroma_location, master_display, max_cll)
    else:
        logger.info("Set up chunklist and corresponding encode commands for the final encode.")
        print("The encoder parameters for the final encode:", encode_params_displist)
        print("\n")
        run_encode(qadjust_cycle, chunklist, video_length, fr, max_parallel_encodes, encode_commands, chunklist_dict, reuse_qadjust, start_time = datetime.now())
        if not master_display:
            master_display = None
        if not max_cll:
            max_cll = None
        concatenate(chunks_folder, input_files, output_final, fr, use_mkvmerge, encoder, video_matrix, video_transfer, video_primaries, chroma_location, master_display, max_cll)

    end_time_total = datetime.now()
    total_duration = end_time_total - start_time_total
    logger.info(f"Process finished, total duration {total_duration}.")


if __name__ == "__main__":
    main()
