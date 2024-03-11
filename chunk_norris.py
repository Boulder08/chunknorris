# Chunk Norris
#
# A very simple Python script to do chunked encoding using an AV1 or x265 CLI encoder.
#
# Make sure you have ffmpeg and the encoder in PATH or where you run the script.
#
# Set common parameters in default_params and add/edit the presets as needed.
# Set base_working_folder and scene_change_file_path according to your folder structure.
# Set max_parallel_encodes to the maximum number of encodes you want to run simultaneously (tune according to your processor and memory usage!)

import os
import subprocess
import re
import sys
import concurrent.futures
import shutil
import argparse
import csv
import ffmpeg
import math
import json
import configparser
import copy
from tqdm import tqdm


def get_video_props(video_path):
    probe = ffmpeg.probe(video_path, v='error')
    video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
    if video_stream:
        video_width = int(video_stream['width'])
        video_length = int(video_stream['nb_frames'])
        video_framerate = str(video_stream['r_frame_rate'])
        num, denom = map(int, video_framerate.split('/'))
        fr = float(num/denom)
        video_framerate = int(math.ceil(num/denom))
        try:
            video_transfer = str(video_stream['color_transfer'])
        except:
            video_transfer = 'unknown'
        return video_width, video_length, video_transfer, video_framerate, fr
    else:
        print("No video stream found in the input video.")
        return None


# Function to clean up a folder
def clean_folder(folder):
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
        if os.path.isfile(item_path):
            os.unlink(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)


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


def create_scxvid_file(scene_change_csv):
    if scd_method == 5:
        with open(encode_script, 'r') as file:
            # Read the first line from the original file
            source = file.readline()
            if scd_tonemap != 0 and cudasynth and "dgsource" in source.lower():
                source = source.replace(".dgi\",", ".dgi\",h2s_enable=1,")
                source = source.replace(".dgi\")", ".dgi\",h2s_enable=1)")
        with open(scd_script, 'w') as scd_file:
            # Write the first line content to the new file
            scd_file.write(source)
            if downscale_scd > 1:
                scd_file.write('\n')
                scd_file.write(f'Spline16Resize(width()/{downscale_scd},height()/{downscale_scd})\n')
                scd_file.write('Crop(16,16,-16,-16)\n')
            if scd_tonemap != 0 and not cudasynth:
                scd_file.write('\nConvertBits(16).DGHDRtoSDR(gamma=1/2.4)\n')
            scd_file.write('ConvertBits(8)\n')
            scd_file.write(f'SCXvid(log="{scene_change_csv}")')
    else:
        with open(encode_script, 'r') as file:
            # Read the first line from the original file
            source = file.readlines()
            for i in range(len(source)):
                if scd_tonemap != 0 and cudasynth and "dgsource" in source[i].lower():
                    source[i] = source[i].replace(".dgi\",", ".dgi\",h2s_enable=1,")
                    source[i] = source[i].replace(".dgi\")", ".dgi\",h2s_enable=1)")
        with open(scd_script, 'w') as scd_file:
            scd_file.writelines(source)
            if scd_tonemap != 0 and not cudasynth:
                scd_file.write('\nConvertBits(16).DGHDRtoSDR(gamma=1/2.4)\n')
            scd_file.write(f'\nConvertBits(bits=8)\n')
            scd_file.write(f'SCXvid(log="{scene_change_csv}")')

    scene_change_command = [
        "ffmpeg",
        "-i", scd_script,
        "-loglevel", "warning",
        "-an", "-f", "null", "NUL"
    ]

    print("Detecting scene changes using SCXviD.\n")
    scd_process = subprocess.Popen(scene_change_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    scd_process.communicate()
    if scd_process.returncode != 0:
        print("Error in scene change detection phase, return code:", scd_process.returncode)
        sys.exit(1)

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


# Function to detect scene changes with ffmpeg
def ffscd(scd_script):
    # Step 1: Detect Scene Changes

    scene_change_command = [
        "ffmpeg",
        "-i", scd_script,
        "-vf", f"select='gt(scene,{scdthresh})',metadata=print",
        "-an", "-f", "null",
        "-",
    ]

    # Redirect stderr to the CSV file
    print("Detecting scene changes using ffmpeg.\n")
    with open(scene_change_csv, "w") as stderr_file:
        scd_process = subprocess.Popen(scene_change_command, stdout=subprocess.PIPE, stderr=stderr_file, shell=True)
        scd_process.communicate()
        if scd_process.returncode != 0:
            print("Error in scene change detection, return code:", scd_process.returncode)
            sys.exit(1)

        # Step 2: Split the Encode into Chunks
        scene_changes = [0]

        # Initialize variables to store frame rate and frame number
        frame_rate = None

        # Function to check if a line contains 'pts_time' information
        def has_pts_time(line):
            return 'pts_time' in line and ':' in line

        with open(scene_change_csv, "r") as csv_file:
            for line in csv_file:
                if "error" in line:
                    print("Scene change detection reported an error, exiting.")
                    print(f"Error message: {line}")
                    print(f"More details in {scene_change_csv}.")
                    sys.exit(1)
                if "Stream #0:" in line and "fps," in line:
                    # Extract frame rate using regular expression
                    match = re.search(r"(\d+\.\d+)\s*fps,", line)
                    if match:
                        frame_rate = float(match.group(1))
                elif has_pts_time(line):
                    parts = line.split("pts_time:")
                    if len(parts) == 2:
                        try:
                            scene_time = float(parts[1].strip())
                            # Calculate frame number based on pts_time and frame rate
                            scene_frame = int(scene_time * frame_rate)
                            scene_changes.append(scene_frame)
                        except ValueError:
                            print(f"Error converting to float: {line}")

        # print("scene_changes:", scene_changes)
        return scene_changes


# Function to detect scene changes with PySceneDetect
def pyscd(scd_script):
    scene_change_command = [
        "scenedetect.exe",
        "-i", scd_script,
        "-b", "moviepy",
        "-d", "1",
        "-o", output_folder_name,
        "detect-adaptive",
        "-t", f"{scdthresh}",
        "-m", f"{min_chunk_length}",
        "list-scenes",
        "-f", scene_change_csv,
        "-q"
    ]
    print("Detecting scene changes using PySceneDetect.\n")
    scd_process = subprocess.Popen(scene_change_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    scd_process.communicate()
    if scd_process.returncode != 0:
        print("Error in scene change detection, return code:", scd_process.returncode)
        sys.exit(1)


def create_fgs_table(encode_params):
    # Create the grain table only if it doesn't exist already
    if os.path.exists(output_grain_table) is False:
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
            print("Error in FGS analysis encoder processing, return code:", enc_process_grain.returncode)
            sys.exit(1)

        print("Encoding the FGS analysis lossless file.")
        enc_process_grain_ffmpeg = subprocess.Popen(ffmpeg_command_grain, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        enc_process_grain_ffmpeg.communicate()
        if enc_process_grain_ffmpeg.returncode != 0:
            print("Error in FGS analysis lossless file processing, return code:", enc_process_grain_ffmpeg.returncode)
            sys.exit(1)

        print("Creating the FGS grain table file.\n")
        enc_process_grain_grav = subprocess.Popen(grav1synth_command, shell=True)
        enc_process_grain_grav.communicate()
        if enc_process_grain_grav.returncode != 0:
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


def scene_change_detection(scd_script):
    scene_changes = []
    # Detect scene changes or use QP-file
    if scd_method in (0, 5, 6):
        # Find the scene change file recursively
        scene_change_filename = os.path.splitext(os.path.basename(encode_script))[0] + ".qp.txt"
        scene_change_file_path = os.path.dirname(encode_script)
        scene_change_file_path = find_scene_change_file(scene_change_file_path, scene_change_filename)

        if scene_change_file_path is None:
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

        # Debug: Print the scene changes from the file
        # print("\nScene Changes from File:")
        # for i, start_frame in enumerate(scene_changes):
            # end_frame = 0 if i == len(scene_changes) - 1 else scene_changes[i + 1] - 1
            # print(f"Scene {i}: Start Frame: {start_frame}, End Frame: {end_frame}")

    elif scd_method == 1:
        if os.path.exists(scd_script) is False:
            print(f"Scene change analysis script not found: {scd_script}, created manually.\n")
            with open(encode_script, 'r') as file:
                # Read the first line from the original file
                source = file.readline()
                if scd_tonemap != 0 and cudasynth and "dgsource" in source.lower():
                    source = source.replace(".dgi\",", ".dgi\",h2s_enable=1,")
                    source = source.replace(".dgi\")", ".dgi\",h2s_enable=1)")
                with open(scd_script, 'w') as scd_file:
                    # Write the first line content to the new file
                    scd_file.write(source)
                    if downscale_scd > 1:
                        scd_file.write('\n')
                        scd_file.write(f'Spline16Resize(width()/{downscale_scd},height()/{downscale_scd})\n')
                        scd_file.write('Crop(16,16,-16,-16)')
                    if scd_tonemap != 0 and not cudasynth:
                        scd_file.write('\nConvertBits(16).DGHDRtoSDR(gamma=1/2.4)')
            scene_changes = ffscd(scd_script)
        else:
            print(f"Using scene change analysis script: {scd_script}.\n")
            scene_changes = ffscd(scd_script)
    elif scd_method == 2:
        print(f"Using scene change analysis script: {encode_script}.\n")
        scd_script = encode_script
        scene_changes = ffscd(scd_script)
    elif scd_method == 3:
        scd_script = os.path.splitext(os.path.basename(encode_script))[0] + "_scd.avs"
        scd_script = os.path.join(os.path.dirname(encode_script), scd_script)
        if os.path.exists(scd_script) is False:
            print(f"Scene change analysis script not found: {scd_script}, created manually.\n")
            with open(encode_script, 'r') as file:
                # Read the first line from the original file
                source = file.readline()
                if scd_tonemap != 0 and cudasynth and "dgsource" in source.lower():
                    source = source.replace(".dgi\",", ".dgi\",h2s_enable=1,")
                    source = source.replace(".dgi\")", ".dgi\",h2s_enable=1)")
                with open(scd_script, 'w') as scd_file:
                    # Write the first line content to the new file
                    scd_file.write(source)
                    scd_file.write(f'\nSpline16Resize(width()/{downscale_scd},height()/{downscale_scd})\n')
                    scd_file.write('Crop(16,16,-16,-16)')
                    if scd_tonemap != 0 and not cudasynth:
                        scd_file.write('\nConvertBits(16).DGHDRtoSDR(gamma=1/2.4)')
            pyscd(scd_script)
        else:
            print(f"Using scene change analysis script: {scd_script}.\n")
            pyscd(scd_script)
    else:
        print(f"Using scene change analysis script: {encode_script}.\n")
        pyscd(encode_script)

    return scene_changes


def adjust_chunkdata(chunkdata_list, credits_start_frame):
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
                'credits': 0
            }
            adjusted_chunkdata_list.append(adjusted_chunk)
            break

    # Remove chunks that start after the credits start frame
    chunkdata_list = [chunkdata for chunkdata in chunkdata_list if chunkdata['start'] <= credits_start_frame]

    # Create a new chunk for the credits
    credits_chunk = {
        'chunk': len(chunkdata_list) + 1,
        'length': last_frame - credits_start_frame + 1,
        'start': credits_start_frame,
        'end': last_frame,
        'credits': 1
    }

    # Append the adjusted chunks to the list
    adjusted_chunkdata_list = chunkdata_list + [credits_chunk]

    # Update the length of each chunk in the adjusted_chunkdata_list
    for chunkdata in adjusted_chunkdata_list:
        chunkdata['length'] = chunkdata['end'] - chunkdata['start'] + 1

    return adjusted_chunkdata_list


def preprocess_chunks(encode_commands, input_files, chunklist, encode_params_original):
    if scd_method in (0, 1, 2, 5, 6):
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
                'chunk': chunk_number, 'length': chunk_length, 'start': start_frame, 'end': end_frame, 'credits': 0
            }
            chunklist.append(chunkdata)

            chunk_number += 1

            if combined:
                i = next_scene_index
                combined = False
            else:
                i += 1
    else:
        with open(scene_change_csv, 'r') as pyscd_file:
            scenelist = csv.reader(pyscd_file)
            scenelist = list(scenelist)

            found_start = False  # Flag to indicate when a line starting with a number is found

        # Process each line in the CSV file
        for row in scenelist:
            if not found_start:
                if row and row[0].isdigit():
                    found_start = True  # Start processing lines
                else:
                    continue  # Skip lines until a line starting with a number is found

            if len(row) >= 5:
                chunk_number = int(row[0])  # First column
                start_frame = int(row[1]) - 1  # Second column
                end_frame = int(row[4]) - 1  # Fifth column
                chunk_length = int(row[7])  # Eighth column
                chunkdata = {
                    'chunk': chunk_number, 'length': chunk_length, 'start': start_frame, 'end': end_frame, 'credits': 0
                }
                chunklist.append(chunkdata)

    if credits_start_frame:
        chunklist = adjust_chunkdata(chunklist, credits_start_frame)

    if rpu and encoder in ('svt', 'x265'):
        print("Splitting the RPU file based on chunks.\n")
        # Use ThreadPoolExecutor for multithreading
        with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.cpu_count()/2)) as executor:
            # Submit each chunk to the executor
            futures = [executor.submit(process_rpu, i) for i in chunklist]
            # Wait for all threads to complete
            for future in futures:
                future.result()

    # print (chunklist)
    chunklist_dict = {chunk_dict['chunk']: chunk_dict['length'] for chunk_dict in chunklist}

    for i in chunklist:
        if encoder != 'x265':
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.ivf")
        else:
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.hevc")
        input_files.append(output_chunk)  # Add the input file for concatenation

    chunklist = sorted(chunklist, key=lambda x: x['length'], reverse=True)

    for i in chunklist:
        encode_params = copy.deepcopy(encode_params_original)
        if rpu and encoder in ('svt', 'x265'):
            rpupath = os.path.join(chunks_folder, f"scene_{i['chunk']}_rpu.bin")
            encode_params.append(f'--dolby-vision-rpu {rpupath}')
        scene_script_file = os.path.join(scripts_folder, f"scene_{i['chunk']}.avs")
        if encoder != 'x265':
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.ivf")
        else:
            output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.hevc")
        # Create the Avisynth script for this scene
        with open(scene_script_file, "w") as scene_script:
            scene_script.write(f'Import("{encode_script}")\n')
            scene_script.write(f"Trim({i['start']}, {i['end']})\n")
            if encoder != 'x265':
                scene_script.write('ConvertBits(10)')  # workaround to aomenc bug with 8-bit input and film grain table

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
            else:
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
                    *encode_params,
                    "--output", '"' + output_chunk + '"',
                    "--input", "-"
                ]

        encode_commands.append((decode_command, enc_command, output_chunk))
    # print (encode_commands)
    return encode_commands, input_files, chunklist, chunklist_dict


def process_rpu(i):
    lastframe = video_length - 1
    # for i in chunklist:
    jsonpath = os.path.join(scripts_folder, f"scene_{i['chunk']}_rpu.json")
    rpupath = os.path.join(chunks_folder, f"scene_{i['chunk']}_rpu.bin")
    if i['chunk'] == 1:
        start = i['end'] + 1
        data = {
            "remove": [f"{start}-{lastframe}"]
        }
    elif i['chunk'] < len(chunklist) + 1:
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
        new_value1 = round(new_value1, 3)
        new_value2 = round(new_value2, 3)

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
    avs2yuv_command, enc_command, output_chunk = command
    avs2yuv_command = " ".join(avs2yuv_command)
    enc_command = " ".join(enc_command)

    enc_command = avs2yuv_command + ' | ' + enc_command

    # Print the encoding command for debugging
    # print(f"\nEncoder command: {enc_command}")

    for attempt in range(1, 3):
        # Execute the encoder process
        enc_process = subprocess.Popen(enc_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        enc_process.communicate()
        if enc_process.returncode == 0:
            return output_chunk
        else:
            print(f"Error in encoder processing, chunk {output_chunk}, attempt {attempt}.\n")
            print("Return code:", enc_process.returncode)

    print("Max retries reached, unable to encode chunk", output_chunk)
    print("The encoder command line is:", enc_command)
    sys.exit(1)


def encode_sample(output_folder, encode_script, encode_params, rpu):
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
            "--dither",
            "--output", '"' + output_chunk + '"',
            "--input", "-"
        ]

    decode_command_sample = ' '.join(decode_command_sample)
    enc_command_sample = ' '.join(enc_command_sample)
    enc_command_sample = decode_command_sample + ' | ' + enc_command_sample

    print("Encoding the sample file.\n")

    enc_process_sample = subprocess.Popen(enc_command_sample, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    enc_process_sample.communicate()
    if enc_process_sample.returncode != 0:
        print("Error in sample encode processing, return code:", enc_process_sample.returncode)
        sys.exit(1)

    print("Path to the sample is:", output_chunk)
    if rpu and encoder in ('svt', 'x265'):
        print("Please note that to ensure Dolby Vision mode during playback, it is recommended to mux the file using mkvmerge/MKVToolnix GUI.")


def concatenate(chunks_folder, input_files, output_final, fr):
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
        mkvmerge_json = [
            "--ui-language",
            "en",
            "--output",
            output_final,
            "--language",
            "0:und",
            "--compression",
            "0:none"
        ]

        for file in files:
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

        print("Concatenating chunks using ffmpeg.\n")

    concat_process = subprocess.Popen(concat_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    concat_process.communicate()
    if concat_process.returncode != 0:
        print("Error when concatenating, return code:", concat_process.returncode)
        sys.exit(1)

    print(f"Concatenated video saved as {output_final}")


def read_presets(presets):
    scriptdir = os.path.dirname(os.path.realpath(__file__))
    presetpath = os.path.join(scriptdir, 'presets.ini')
    config = configparser.ConfigParser()
    try:
        config.read(presetpath)
    except:
        print("\nPresets.ini could not be found from the script directory, exiting..\n")
        sys.exit(1)
    default_section = encoder + '-default'
    try:
        default_params = dict(config.items(default_section))
    except:
        print("\nWarning: the default settings for the encoder could not be found from presets.ini.\n")
        default_params = {}
    try:
        base_working_folder = config['paths']['base_working_folder']
    except:
        print("The path for base working folder is missing from presets.ini.\n")
        base_working_folder = input("Please enter a path: ")

    merged_params = {}
    for preset in presets:
        preset_section = encoder + '-' + preset
        try:
            preset_params = dict(config.items(preset_section))
            merged_params.update(preset_params)
        except:
            print("\nChosen preset not found from the presets.ini file.\n")
            sys.exit(1)

    return default_params, merged_params, base_working_folder


parser = argparse.ArgumentParser()
parser.add_argument('encode_script')
parser.add_argument('--encoder', nargs='?', default='svt', type=str)
parser.add_argument('--preset', nargs='?', default='1080p', type=str)
parser.add_argument('--cpu', nargs='?', default=3, type=int)
parser.add_argument('--threads', nargs='?', type=int)
parser.add_argument('--q', nargs='?', type=int)
parser.add_argument('--min-chunk-length', nargs='?', default=64, type=int)
parser.add_argument('--max-parallel-encodes', nargs='?', default=4, type=int)
parser.add_argument('--noiselevel', nargs='?', type=int)
parser.add_argument('--luma-bias', nargs='?', type=int)
parser.add_argument('--luma-bias-strength', nargs='?', type=int)
parser.add_argument('--luma-bias-midpoint', nargs='?', type=int)
parser.add_argument('--variance-boost-strength', nargs='?', default=2, type=int)
parser.add_argument('--variance-octile', nargs='?', default=6, type=int)
parser.add_argument('--graintable-method', nargs='?', default=1, type=int)
parser.add_argument('--graintable-sat', nargs='?', type=float)
parser.add_argument('--graintable', nargs='?', type=str)
parser.add_argument('--scd-method', nargs='?', default=1, type=int)
parser.add_argument('--scd-tonemap', nargs='?', type=int)
parser.add_argument('--scdthresh', nargs='?', type=float)
parser.add_argument('--downscale-scd', nargs='?', default=4, type=int)
parser.add_argument('--decode-method', nargs='?', default=1, type=int)
parser.add_argument('--credits-start-frame', nargs='?', type=int)
parser.add_argument('--credits-q', nargs='?', type=int)
parser.add_argument('--credits-cpu', nargs='?', type=int)
parser.add_argument('--graintable-cpu', nargs='?', type=int)
parser.add_argument('--master-display', nargs='?', type=str)
parser.add_argument('--max-cll', nargs='?', type=str)
parser.add_argument('--lookahead', nargs='?', type=int)
parser.add_argument('--x265cl', nargs='?', type=str)
parser.add_argument('--sample-start-frame', nargs='?', type=int)
parser.add_argument('--sample-end-frame', nargs='?', type=int)
parser.add_argument('--rpu', nargs='?', type=str)
parser.add_argument('--cudasynth', action='store_true')
parser.add_argument('--list-parameters', action='store_true')

# Command-line arguments
args = parser.parse_args()
encode_script = args.encode_script
encoder = args.encoder
presets = args.preset.split(',')
q = args.q
min_chunk_length = args.min_chunk_length
max_parallel_encodes = args.max_parallel_encodes
threads = args.threads
noiselevel = args.noiselevel
graintable = args.graintable
graintable_method = args.graintable_method
graintable_sat = args.graintable_sat
scd_method = args.scd_method
scd_tonemap = args.scd_tonemap
scdthresh = args.scdthresh
downscale_scd = args.downscale_scd
cpu = args.cpu
decode_method = args.decode_method
luma_bias = args.luma_bias
luma_bias_strength = args.luma_bias_strength
luma_bias_midpoint = args.luma_bias_midpoint
credits_start_frame = args.credits_start_frame
credits_q = args.credits_q
credits_cpu = args.credits_cpu
graintable_cpu = args.graintable_cpu
master_display = args.master_display
max_cll = args.max_cll
lookahead = args.lookahead
vb_strength = args.variance_boost_strength
octile = args.variance_octile
x265cl = args.x265cl
sample_start_frame = args.sample_start_frame
sample_end_frame = args.sample_end_frame
rpu = args.rpu
cudasynth = args.cudasynth
listparams = args.list_parameters

# Sanity checks of parameters, no thanks to Python argparse being stupid if the allowed range is big
if encode_script is None:
    print("You need to supply a script to encode.\n")
    sys.exit(1)
elif encoder not in ('rav1e', 'svt', 'aom', 'x265'):
    print("Valid encoder choices are rav1e, svt, aom or x265.\n")
    sys.exit(1)
elif encoder in ('svt', 'aom', 'x265') and 2 > q > 64:
    print("Q must be 2-64.\n")
    sys.exit(1)
elif encoder == 'rav1e' and 0 > q > 255:
    print("Q must be 0-255.\n")
    sys.exit(1)
elif -1 > cpu > 11:
    print("CPU must be -1..11.\n")
    sys.exit(1)
elif 1 > threads > 64:
    print("Threads must be 1-64.\n")
    sys.exit(1)
elif 5 > min_chunk_length > 999999:
    print("Minimum chunk length must be 5-999999.\n")
    sys.exit(1)
elif 1 > max_parallel_encodes > 64:
    print("Maximum parallel encodes is 1-64.\n")
    sys.exit(1)
elif luma_bias and 0 > luma_bias > 100:
    print("Valid ranges for luma bias, luma bias strength and luma bias midpoint is 0-100, 0-100 and 0-255 respectively.\n")
    sys.exit(1)
elif luma_bias_strength and 0 > luma_bias_strength > 100:
    print("Valid ranges for luma bias, luma bias strength and luma bias midpoint is 0-100, 0-100 and 0-255 respectively.\n")
    sys.exit(1)
elif luma_bias_midpoint and 0 > luma_bias_midpoint > 255:
    print("Valid ranges for luma bias, luma bias strength and luma bias midpoint is 0-100, 0-100 and 0-255 respectively.\n")
    sys.exit(1)
elif graintable_method and graintable_method not in (0, 1):
    print("Graintable method must be 0 or 1.\n")
    sys.exit(1)
elif graintable_sat and 0 > graintable_sat > 1:
    print("Graintable saturation must be 0-1.0.\n")
    sys.exit(1)
elif 0 > scd_method > 6:
    print("Scene change detection method must be 0-6.\n")
    sys.exit(1)
elif scd_tonemap and scd_tonemap not in (0, 1):
    print("Scene change detection tonemap must be 0 or 1.\n")
    sys.exit(1)
elif scdthresh and 1 > scdthresh > 10:
    print("Scene change detection threshold must be 1-10.0.\n")
    sys.exit(1)
elif 0 > downscale_scd > 8:
    print("Scene change detection downscale factor must be 0-8.\n")
    sys.exit(1)
elif decode_method and decode_method not in (0, 1):
    print("Decoding method must be 0 or 1.\n")
    sys.exit(1)
elif credits_q and encoder in ('svt', 'aom') and 2 > credits_q > 64:
    print("Q for credits must be 2-64.\n")
    sys.exit(1)
elif credits_q and encoder == 'rav1e' and 0 > credits_q > 255:
    print("Q for credits must be 0-255.\n")
    sys.exit(1)
elif credits_cpu and -1 > credits_cpu > 11:
    print("CPU for credits must be -1..11.\n")
    sys.exit(1)
elif graintable and graintable_cpu and -1 > graintable_cpu > 11:
    print("CPU for FGS analysis must be -1..11.\n")
    sys.exit(1)
elif lookahead and 0 > lookahead > 120:
    print("Lookahead must be 0-120.\n")
    sys.exit(1)
elif vb_strength and 0 > vb_strength > 4:
    print("Variance boost strength must be 0-4.\n")
    sys.exit(1)
elif octile and octile not in (1, 2, 4, 6, 8):
    print("Octile must be 1, 2, 4, 6 or 8.\n")
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
if scd_method in (3, 4):
    if shutil.which("scenedetect.exe") is None:
        print("Unable to find scenedetect.exe from PATH, exiting..\n")
        sys.exit(1)
if graintable_method == 1:
    if shutil.which("grav1synth.exe") is None:
        print("Unable to find grav1synth.exe from PATH, exiting..\n")
        sys.exit(1)

# Store the full path of encode_script
encode_script = os.path.abspath(encode_script)

# Get video props from the source
video_width, video_length, video_transfer, video_framerate, fr = get_video_props(encode_script)

if credits_start_frame and credits_start_frame >= video_length - 1:
    print("The credits cannot start at or after the end of video.\n")
    sys.exit(1)
if (sample_start_frame and sample_start_frame >= video_length - 1) or (sample_end_frame and sample_end_frame >= video_length - 1) or (sample_start_frame and sample_end_frame and sample_start_frame >= sample_end_frame):
    print("Please check the sample range.\n")
    sys.exit(1)

# Set scene change helper file to use the same path as the original source script
scene_change_file_path = os.path.dirname(encode_script)

# Set some more case dependent default values
if noiselevel is None or graintable or graintable_method > 0:
    if encoder in ('svt', 'aom'):
        noiselevel = 0
    else:
        noiselevel = None
if graintable:
    graintable_method = 0
if scdthresh is None:
    if scd_method in (1, 2):
        scdthresh = 0.3
    else:
        scdthresh = 3.0
if scd_tonemap is None:
    if video_transfer == 'smpte2084':
        scd_tonemap = 1
    else:
        scd_tonemap = 0
if credits_cpu is None:
    credits_cpu = cpu + 1
if graintable_cpu is None:
    if encoder in ('svt', 'aom'):
        graintable_cpu = 3
    else:
        graintable_cpu = 4
if credits_q is None and encoder == 'rav1e':
    credits_q = 180
elif credits_q is None and encoder in ('svt', 'aom'):
    credits_q = 32
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
        graintable_sat = 0.5
    else:
        graintable_sat = 0
if lookahead is None:
    if encoder == 'svt':
        lookahead = None
    elif encoder == 'rav1e':
        lookahead = 40
    else:
        lookahead = 64
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

# Collect default values from commandline parameters
if encoder == 'rav1e':
    default_values = {
        "speed": cpu,
        "quantizer": q,
        "keyint": video_framerate * 10,
        "photon-noise": noiselevel,
        "threads": threads,
        "rdo-lookahead-frames": lookahead,
    }
    if master_display:
        default_values['mastering-display'] = '"' + master_display + '"'
        default_values['content-light'] = max_cll
elif encoder == 'svt':
    default_values = {
        "preset": cpu,
        "crf": q,
        "film-grain": noiselevel,
        "lp": threads,
        "lookahead": lookahead,
        "variance-boost-strength": vb_strength,
        "variance-octile": octile,
    }
    if master_display:
        default_values['mastering-display'] = '"' + master_display + '"'
        default_values['content-light'] = max_cll
        default_values['enable-hdr'] = 1
        default_values['chroma-sample-position'] = 2
elif encoder == 'x265':
    default_values = {
        "crf": q,
        "log-level": -1,
        "pools": threads,
        "keyint": video_framerate * 10,
    }
    if master_display:
        default_values['master-display'] = '"' + master_display + '"'
        default_values['max-cll'] = '"' + max_cll + '"'
    if video_transfer == 'smpte2084':
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
        "lag-in-frames": lookahead,
        "kf-max-dist": video_framerate * 10,
        "denoise-noise-level": noiselevel,
        "chroma-q-offset-u": -q + 2,
        "chroma-q-offset-v": -q + 2,
    }

default_params, preset_params, base_working_folder = read_presets(presets)
encode_params = {**default_values, **default_params, **preset_params}

# Create a list of non-empty parameters in the encoder supported format
if encoder in ('svt', 'rav1e', 'x265'):
    encode_params = [f"--{key} {value}" for key, value in encode_params.items() if value is not None]
else:
    encode_params = [f"--{key}={value}" for key, value in encode_params.items() if value is not None]

if encoder == 'x265' and x265cl:
    encode_params.append(x265cl)

if rpu and encoder == 'svt':
    print("Dolby Vision mode detected, please note that it is experimental.\n")
elif rpu and encoder == 'x265':
    print("Dolby Vision mode detected, VBV enabled and Level 5.1 set.\n")
    encode_params.append('--level-idc 5.1 --no-high-tier --dolby-vision-profile 8.1 --vbv-bufsize 40000 --vbv-maxrate 40000')

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
    print("\nAll encoding parameters combined:\n")
    encode_params = " ".join(encode_params)
    encode_params = encode_params.replace('  ', ' ')
    print(encode_params)
    sys.exit(0)

# Determine the output folder name based on the encode_script
output_folder_name = os.path.splitext(os.path.basename(encode_script))[0]
output_folder_name = os.path.join(base_working_folder, output_folder_name)

# Clean up the target folder if it already exists
if os.path.exists(output_folder_name) and not sample_start_frame and not sample_end_frame:
    print(f"Cleaning up the existing folder: {output_folder_name}\n")
    clean_folder(output_folder_name)

# Create folders for the Avisynth scripts, encoded chunks, and output
output_folder = os.path.join(output_folder_name, "output")
scripts_folder = os.path.join(output_folder_name, "scripts")
chunks_folder = os.path.join(output_folder_name, "chunks")

# Create directories if they don't exist
os.makedirs(output_folder, exist_ok=True)
os.makedirs(scripts_folder, exist_ok=True)
os.makedirs(chunks_folder, exist_ok=True)

# Define final video file name
output_name = os.path.splitext(os.path.basename(encode_script))[0]
if encoder != 'x265':
    output_final = os.path.join(output_folder, f"{output_name}.mkv")
elif encoder == 'x265' and use_mkvmerge:
    output_final = os.path.join(output_folder, f"{output_name}.mkv")
else:
    output_final = os.path.join(output_folder, f"{output_name}.mp4")

# Grain table creation, only for AV1
if encoder != 'x265':
    # Generate the FGS analysis file names
    output_grain_file_lossless = os.path.join(output_folder, f"{output_name}_lossless.mkv")
    output_grain_file_encoded = os.path.join(output_folder, f"{output_name}_encoded.ivf")
    output_grain_table = os.path.split(encode_script)[0]
    output_grain_table_baseline = os.path.join(output_grain_table, f"{output_name}_grain_baseline.tbl")
    output_grain_table = os.path.join(output_grain_table, f"{output_name}_grain.tbl")

    # Create the reference files for FGS
    if encoder == 'svt':
        if graintable_method > 0:
            create_fgs_table(encode_params)
            encode_params.append(f"--fgs-table \"{output_grain_table}\"")
        elif graintable:
            encode_params.append(f"--fgs-table \"{graintable}\"")
    elif encoder == 'rav1e':
        if graintable_method > 0:
            create_fgs_table(encode_params)
            encode_params.append(f"--film-grain-table \"{output_grain_table}\"")
        elif graintable:
            encode_params.append(f"--film-grain-table \"{graintable}\"")
    else:
        if graintable_method > 0:
            create_fgs_table(encode_params)
            encode_params.append(f"--film-grain-table=\"{output_grain_table}\"")
        elif graintable:
            encode_params.append(f"--film-grain-table=\"{graintable}\"")

# Encode only the sample if start and end frames are supplied, exit afterwards.
if sample_start_frame and sample_end_frame:
    encode_sample(output_folder, encode_script, encode_params, rpu)
    sys.exit(0)

# Detect scene changes
scd_script = os.path.splitext(os.path.basename(encode_script))[0] + "_scd.avs"
scd_script = os.path.join(os.path.dirname(encode_script), scd_script)
scene_change_csv = os.path.join(output_folder_name, f"scene_changes_{os.path.splitext(os.path.basename(encode_script))[0]}.csv")
if scd_method in (5, 6):
    create_scxvid_file(scene_change_csv)
scene_changes = scene_change_detection(scd_script)

# Create the AVS scripts, prepare encoding and concatenation commands for chunks
encode_commands = []  # List to store the encoding commands
input_files = []  # List to store input files for concatenation
chunklist = []  # Helper list for producing the encoding and concatenation lists
completed_chunks = []  # List of completed chunks

# Run encoding commands with a set maximum of concurrent processes
processed_length = 0.0
total_filesize_kbits = 0.0
avg_bitrate = 0.0
encode_commands, input_files, chunklist, chunklist_dict = preprocess_chunks(encode_commands, input_files, chunklist, encode_params)
progress_bar = tqdm(total=video_length, desc="Progress", unit="frames", smoothing=0)

with concurrent.futures.ThreadPoolExecutor(max_parallel_encodes) as executor:
    futures = {executor.submit(run_encode_command, cmd): cmd for cmd in encode_commands}
    try:
        for future in concurrent.futures.as_completed(futures):
            output_chunk = future.result()
            parts = output_chunk.split('_')
            chunk_number = int(str(parts[-1].split('.')[0]))
            processed_length = processed_length + (chunklist_dict.get(chunk_number) / fr)
            total_filesize_kbits = total_filesize_kbits + (os.path.getsize(output_chunk) / 1024 * 8)
            avg_bitrate = str(round(total_filesize_kbits / processed_length, 2))
            progress_bar.update(chunklist_dict.get(chunk_number))
            progress_bar.set_postfix({'Rate': avg_bitrate})
            completed_chunks.append(output_chunk)
            # print(f"Encoding for scene completed: {output_chunk}")
    except:
        print("Something went wrong while encoding, exiting!\n")
        sys.exit(1)

# Wait for all encoding processes to finish before concatenating
progress_bar.close()
print("Encoding for all scenes completed.\n")

concatenate(chunks_folder, input_files, output_final, fr)
