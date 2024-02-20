# Chunk Norris
#
# A very simple Python script to do chunked encoding using an AV1 or x265 CLI encoder.
#
# Requirements: Python 3.10.x (possibly just 3.x), scene change list in x264/x265 QP file format, Avisynth, avs2yuv64, ffmpeg, aomenc (the lavish mod recommended), svt-av1, rav1e or x265
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
        with open(scd_script, 'w') as scd_file:
            # Write the first line content to the new file
            scd_file.write(source)
            if downscale_scd > 1:
                scd_file.write('\n')
                scd_file.write(f'Spline16Resize(width()/{downscale_scd},height()/{downscale_scd})\n')
                scd_file.write('Crop(16,16,-16,-16)\n')
            if scd_tonemap != 0:
                scd_file.write('\nConvertBits(16).DGHDRtoSDR(gamma=1/2.4)\n')
            scd_file.write(f'SCXvid(log="{scene_change_csv}")')
    else:
        with open(scd_script, 'w') as scd_file:
            scd_file.write(f'Import("{encode_script}")\n')
            if scd_tonemap != 0:
                scd_file.write('\nConvertBits(16).DGHDRtoSDR(gamma=1/2.4)\n')
            scd_file.write(f'SCXvid(log="{scene_change_csv}")')

    scene_change_command = [
        "ffmpeg",
        "-i", scd_script,
        "-loglevel", "warning",
        "-an", "-f", "null", "NUL"
    ]

    print("Detecting scene changes using SCXviD.\n")
    subprocess.run(scene_change_command)

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
        subprocess.run(scene_change_command, stderr=stderr_file)

        # Step 2: Split the Encode into Chunks
        scene_changes = [0]

        # Initialize variables to store frame rate and frame number
        frame_rate = None

        # Function to check if a line contains 'pts_time' information
        def has_pts_time(line):
            return 'pts_time' in line and ':' in line

        with open(scene_change_csv, "r") as csv_file:
            for line in csv_file:
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
    subprocess.run(scene_change_command)


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
        print("\n")

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
            avs2yuv_command_grain = [
                "avs2yuv64.exe",
                "-no-mt",
                '"' + grain_script + '"',
                "-"
            ]
        else:
            avs2yuv_command_grain = [
                "ffmpeg.exe",
                "-loglevel", "fatal",
                "-i", '"' + grain_script + '"',
                "-f", "yuv4mpegpipe",
                "-strict", "-1",
                "-"
            ]

        if encoder == 'rav1e':
            encode_params_grain = [x.replace(f'--threads {threads}', '--threads 0') for x in encode_params]
            enc_command_grain = [
                "rav1e.exe",
                *encode_params_grain,
                f"--speed {cpu}",
                f"--quantizer {q}",
                "--no-scene-detection",
                "-o", '"' + output_grain_file_encoded + '"',
                "-"
            ]
        elif encoder == 'svt':
            encode_params_grain = [x.replace(f'--lp {threads}', '--lp 0') for x in encode_params]
            enc_command_grain = [
                "svtav1encapp.exe",
                *encode_params_grain,
                f"--preset {cpu}",
                f"--crf {q}",
                "-b", '"' + output_grain_file_encoded + '"',
                "-i -"
            ]
        else:
            enc_command_grain = [
                "aomenc.exe",
                "--ivf",
                *encode_params,
                "--passes=1",
                f"--cpu-used={cpu}",
                f"--cq-level={q}",
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
        avs2yuv_command_grain = ' '.join(avs2yuv_command_grain)
        enc_command_grain = ' '.join(enc_command_grain)
        print("Encoding the FGS analysis AV1 file.\n")
        avs2yuv_grain_process = subprocess.Popen(avs2yuv_command_grain, stdout=subprocess.PIPE, shell=True)
        aomenc_grain_process = subprocess.Popen(enc_command_grain, stdin=avs2yuv_grain_process.stdout, shell=True)
        aomenc_grain_process.communicate()

        print("\n\nEncoding the FGS analysis lossless file.\n")
        subprocess.run(ffmpeg_command_grain)

        print("\nCreating the FGS grain table file.\n")
        subprocess.run(grav1synth_command)

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
        # Find the second longest section based on timestamp difference

            # Sort sections by timestamp difference in descending order
            sorted_sections = sorted(sections, key=timestamp_difference, reverse=True)

            # The second longest section is at index 1 (index 0 is the longest)
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
            print("\n")
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
                with open(scd_script, 'w') as scd_file:
                    # Write the first line content to the new file
                    scd_file.write(source)
                    if downscale_scd > 1:
                        scd_file.write('\n')
                        scd_file.write(f'Spline16Resize(width()/{downscale_scd},height()/{downscale_scd})\n')
                        scd_file.write('Crop(16,16,-16,-16)')
                    if scd_tonemap != 0:
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
                with open(scd_script, 'w') as scd_file:
                    # Write the first line content to the new file
                    scd_file.write(source)
                    scd_file.write('\n')
                    scd_file.write(f'Spline16Resize(width()/{downscale_scd},height()/{downscale_scd})\n')
                    scd_file.write('Crop(16,16,-16,-16)')
                    if scd_tonemap != 0:
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


def preprocess_chunks(encode_commands, input_files, chunklist):
    if scd_method in (0, 1, 2, 5, 6):
        chunk_number = 1
        i = 0
        combined = False
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
            avs2yuv_command = [
                "avs2yuv64.exe",
                "-no-mt",
                '"'+scene_script_file+'"',  # Use the Avisynth script for this scene
                "-",
                "2> nul"
            ]
        else:
            avs2yuv_command = [
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
                    f"--speed {cpu}",
                    f"--quantizer {q}",
                    "--no-scene-detection",
                    "-o", '"' + output_chunk + '"',
                    "-",
                    "2> nul"
                ]
            else:
                enc_command = [
                    "rav1e.exe",
                    *encode_params,
                    "-q",
                    f"--speed {credits_cpu}",
                    f"--quantizer {credits_q}",
                    "-o", '"' + output_chunk + '"',
                    "-"
                ]
        elif encoder == 'svt':
            if i['credits'] == 0:
                enc_command = [
                    "svtav1encapp.exe",
                    *encode_params,
                    f"--preset {cpu}",
                    f"--crf {q}",
                    "-b", '"'+output_chunk+'"',
                    "-i -",
                    "2> nul"
                ]
            else:
                enc_command = [
                    "svtav1encapp.exe",
                    *encode_params,
                    f"--preset {credits_cpu}",
                    f"--crf {credits_q}",
                    "-b", '"'+output_chunk+'"',
                    "-i -",
                    "2> nul"
                ]
        elif encoder == 'aom':
            if i['credits'] == 0:
                enc_command = [
                    "aomenc.exe",
                    "-q",
                    "--ivf",
                    f"--cpu-used={cpu}",
                    *encode_params,
                    "--passes=1",
                    f"--cq-level={q}",
                    "-o", '"'+output_chunk+'"',
                    "-"
                ]
            else:
                enc_command = [
                    "aomenc.exe",
                    "-q",
                    "--ivf",
                    f"--cpu-used={credits_cpu}",
                    *encode_params,
                    "--passes=1",
                    f"--cq-level={credits_q}",
                    "-o", '"' + output_chunk + '"',
                    "-"
                ]
        else:
            if i['credits'] == 0:
                enc_command = [
                    "x265.exe",
                    "--y4m",
                    "--no-progress",
                    "--log-level", "-1",
                    *encode_params,
                    "--dither",
                    f"--crf {q}",
                    "--output", '"'+output_chunk+'"',
                    "--input", "-"
                ]
            else:
                enc_command = [
                    "x265.exe",
                    "--y4m",
                    "--no-progress",
                    "--log-level", "-1",
                    *encode_params,
                    "--dither",
                    f"--crf {credits_q}",
                    "--output", '"' + output_chunk + '"',
                    "--input", "-"
                ]

        encode_commands.append((avs2yuv_command, enc_command, output_chunk))
    return encode_commands, input_files, chunklist, chunklist_dict


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

    # Print the encoding command for debugging
    # print(f"\nEncoder command: {enc_command}")

    # Execute avs2yuv and pipe the output to the encoder
    avs2yuv_process = subprocess.Popen(avs2yuv_command, stdout=subprocess.PIPE, shell=True)
    enc_process = subprocess.Popen(enc_command, stdin=avs2yuv_process.stdout, shell=True)

    # Wait for the encoder to finish
    enc_process.communicate()

    if enc_process.returncode != 0:
        print("Error in encoder processing, chunk", output_chunk)
        print("Return code:", enc_process.returncode)

    return output_chunk


def encode_sample(output_folder, encode_script):
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
        avs2yuv_command_sample = [
            "avs2yuv64.exe",
            "-no-mt",
            '"' + sample_script_file + '"',  # Use the Avisynth script for this scene
            "-",
            "2> nul"
        ]
    else:
        avs2yuv_command_sample = [
            "ffmpeg.exe",
            "-loglevel", "fatal",
            "-i", '"' + sample_script_file + '"',
            "-f", "yuv4mpegpipe",
            "-strict", "-1",
            "-"
        ]

    if encoder == 'rav1e':
        enc_command_sample = [
            "rav1e.exe",
            *encode_params,
            f"--speed {cpu}",
            f"--quantizer {q}",
            "--no-scene-detection",
            "-o", '"' + output_chunk + '"',
            "-"
        ]
    elif encoder == 'svt':
        enc_command_sample = [
            "svtav1encapp.exe",
            *encode_params,
            f"--preset {cpu}",
            f"--crf {q}",
            "-b", '"' + output_chunk + '"',
            "-i -"
        ]
    elif encoder == 'aom':
        enc_command_sample = [
            "aomenc.exe",
            "--ivf",
            f"--cpu-used={cpu}",
            *encode_params,
            "--passes=1",
            f"--cq-level={q}",
            "-o", '"' + output_chunk + '"',
            "-"
        ]
    else:
        enc_command_sample = [
            "x265.exe",
            "--y4m",
            *encode_params,
            "--dither",
            f"--crf {q}",
            "--output", '"' + output_chunk + '"',
            "--input", "-"
        ]

    avs2yuv_command_sample = ' '.join(avs2yuv_command_sample)
    enc_command_sample = ' '.join(enc_command_sample)
    print("Encoding the sample file.\n")
    avs2yuv_sample_process = subprocess.Popen(avs2yuv_command_sample, stdout=subprocess.PIPE, shell=True)
    enc_sample_process = subprocess.Popen(enc_command_sample, stdin=avs2yuv_sample_process.stdout, shell=True)
    enc_sample_process.communicate()
    print("Path for the sample is:", output_chunk)


def concatenate(chunks_folder, input_files, output_final_ffmpeg, fr):
    # Create a list file for input files
    input_list_txt = os.path.join(chunks_folder, "input_list.txt")

    # Write the input file list to the text file
    with open(input_list_txt, "w") as file:
        for input_file in input_files:
            file.write(f"file '{input_file}'\n")

    # Define the ffmpeg concatenation command
    if encoder != 'x265':
        ffmpeg_concat_command = [
            "ffmpeg.exe",
            "-loglevel", "warning",
            "-f", "concat",
            "-safe", "0",  # Allow absolute paths
            "-i", input_list_txt,
            "-c", "copy",
            "-strict", "strict",
            "-map", "0",
            "-y",  # Overwrite output file if it exists
            output_final_ffmpeg
        ]
    else:
        ffmpeg_concat_command = [
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
            output_final_ffmpeg
        ]

    # Print the ffmpeg concatenation command for debugging
    # print("Concatenation Command (ffmpeg):")
    # print(" ".join(ffmpeg_concat_command))

    print("Concatenating chunks using ffmpeg.\n")

    # Run the ffmpeg concatenation command
    subprocess.run(ffmpeg_concat_command)

    print(f"Concatenated video saved as {output_final_ffmpeg}")


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
parser.add_argument('--sharpness', nargs='?', default=2, type=int)
parser.add_argument('--tile-columns', nargs='?', type=int)
parser.add_argument('--tile-rows', nargs='?', type=int)
parser.add_argument('--arnr-strength', default=2, nargs='?', type=int)
parser.add_argument('--arnr-maxframes', default=7, nargs='?', type=int)
parser.add_argument('--tpl-strength', nargs='?', type=int)
parser.add_argument('--max-reference-frames', default=4, nargs='?', type=int)
parser.add_argument('--tune', nargs='?', type=str)
parser.add_argument('--tune-content', nargs='?', default='psy', type=str)
parser.add_argument('--luma-bias', nargs='?', type=int)
parser.add_argument('--luma-bias-strength', nargs='?', type=int)
parser.add_argument('--luma-bias-midpoint', nargs='?', type=int)
parser.add_argument('--deltaq-mode', nargs='?', type=int)
parser.add_argument('--tf', nargs='?', default=0, type=int)
parser.add_argument('--cdef', nargs='?', default=0, type=int)
parser.add_argument('--restoration', nargs='?', default=0, type=int)
parser.add_argument('--scm', nargs='?', default=2, type=int)
parser.add_argument('--qm-min', nargs='?', default=5, type=int)
parser.add_argument('--qm-max', nargs='?', default=9, type=int)
parser.add_argument('--graintable-method', nargs='?', default=1, type=int)
parser.add_argument('--graintable-sat', nargs='?', type=float)
parser.add_argument('--graintable', nargs='?', type=str)
parser.add_argument('--scd-method', nargs='?', default=3, type=int)
parser.add_argument('--scd-tonemap', nargs='?', type=int)
parser.add_argument('--scdthresh', nargs='?', type=float)
parser.add_argument('--downscale-scd', nargs='?', default=4, type=int)
parser.add_argument('--decode-method', nargs='?', default=1, type=int)
parser.add_argument('--credits-start-frame', nargs='?', type=int)
parser.add_argument('--credits-q', nargs='?', type=int)
parser.add_argument('--credits-cpu', nargs='?', type=int)
parser.add_argument('--master-display', nargs='?', type=str)
parser.add_argument('--max-cll', nargs='?', type=str)
parser.add_argument('--lookahead', nargs='?', type=int)
parser.add_argument('--x265cl', nargs='?', type=str)
parser.add_argument('--sample-start-frame', nargs='?', type=int)
parser.add_argument('--sample-end-frame', nargs='?', type=int)

# Set the base working folder, use double backslashes
base_working_folder = "F:\\Temp\\Captures\\encodes"

# Command-line arguments
args = parser.parse_args()
encode_script = args.encode_script
encoder = args.encoder
preset = args.preset
q = args.q
min_chunk_length = args.min_chunk_length
max_parallel_encodes = args.max_parallel_encodes
threads = args.threads
noiselevel = args.noiselevel
sharpness = args.sharpness
tile_columns = args.tile_columns
tile_rows = args.tile_rows
graintable = args.graintable
graintable_method = args.graintable_method
graintable_sat = args.graintable_sat
scd_method = args.scd_method
scd_tonemap = args.scd_tonemap
scdthresh = args.scdthresh
downscale_scd = args.downscale_scd
cpu = args.cpu
tune = args.tune
tune_content = args.tune_content
arnr_strength = args.arnr_strength
arnr_maxframes = args.arnr_maxframes
decode_method = args.decode_method
max_reference_frames = args.max_reference_frames
luma_bias = args.luma_bias
luma_bias_strength = args.luma_bias_strength
luma_bias_midpoint = args.luma_bias_midpoint
deltaq_mode = args.deltaq_mode
tf = args.tf
cdef = args.cdef
restoration = args.restoration
scm = args.scm
tplstrength = args.tpl_strength
credits_start_frame = args.credits_start_frame
credits_q = args.credits_q
credits_cpu = args.credits_cpu
master_display = args.master_display
max_cll = args.max_cll
lookahead = args.lookahead
qm_min = args.qm_min
qm_max = args.qm_max
x265cl = args.x265cl
sample_start_frame = args.sample_start_frame
sample_end_frame = args.sample_end_frame

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
elif 0 > sharpness > 7:
    print("Sharpness must be 0-7.\n")
    sys.exit(1)
elif tile_columns and 0 > tile_columns > 4:
    print("Number of column or row tiles must be 0-4.\n")
    sys.exit(1)
elif tile_rows and 0 > tile_rows > 4:
    print("Number of column or row tiles must be 0-4.\n")
    sys.exit(1)
elif 0 > arnr_strength > 6:
    print("Arnr-strength must be 0-6.\n")
    sys.exit(1)
elif 0 > arnr_maxframes > 15:
    print("Arnr-maxframes must be 0-15.\n")
    sys.exit(1)
elif tplstrength and 0 > tplstrength > 100:
    print("Tpl-strength must be 0-100.\n")
    sys.exit(1)
elif 3 > max_reference_frames > 7:
    print("Maximum reference frames must be 3-7.\n")
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
elif deltaq_mode and 0 > deltaq_mode > 6:
    print("Deltaq-mode must be 0-6.\n")
    sys.exit(1)
elif 0 > tf > 1:
    print("Tf must be 0 or 1.\n")
    sys.exit(1)
elif 0 > cdef > 1:
    print("Cdef must be 0 or 1.\n")
    sys.exit(1)
elif 0 > restoration > 1:
    print("Restoration must be 0 or 1.\n")
    sys.exit(1)
elif 0 > scm > 2:
    print("Scm must be 0-2.\n")
    sys.exit(1)
elif 0 > graintable_method > 1:
    print("Graintable method must be 0 or 1.\n")
    sys.exit(1)
elif graintable_sat and 0 > graintable_sat > 1:
    print("Graintable saturation must be 0-1.0.\n")
    sys.exit(1)
elif 0 > scd_method > 6:
    print("Scene change detection method must be 0-6.\n")
    sys.exit(1)
elif scd_tonemap and 0 > scd_tonemap > 1:
    print("Scene change detection tonemap must be 0 or 1.\n")
    sys.exit(1)
elif scdthresh and 1 > scdthresh > 10:
    print("Scene change detection threshold must be 1-10.0.\n")
    sys.exit(1)
elif 0 > downscale_scd > 8:
    print("Scene change detection downscale factor must be 0-8.\n")
    sys.exit(1)
elif 0 > decode_method > 1:
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
elif lookahead and 0 > lookahead > 120:
    print("Lookahead must be 0-120.\n")
    sys.exit(1)
elif (qm_min and 0 > qm_min > 15) or (qm_max and 0 > qm_max > 15):
    print("Qm-min or qtm-max must be 0-15.\n")
    sys.exit(1)
elif preset not in ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo') and encoder == 'x265':
    print("Incorrect preset for x265.\n")
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
        graintable_sat = 1.0
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
    master_display, max_cll = parse_master_display(master_display, max_cll)

if encoder == 'rav1e':
    default_values = {
        "keyint": video_framerate * 10,
        "photon-noise": noiselevel,
        "threads": threads,
        "rdo-lookahead-frames": lookahead,
        "min-keyint": 5,
    }
    presets = {
        "720p": {
            "primaries": 'bt709',
            "transfer": 'bt709',
            "matrix": 'bt709',
            "tile-cols": 0,
            "tile-rows": 0,
            "threads": 2,
            # Add more parameters as needed
        },
        "1080p": {
            "primaries": 'bt709',
            "transfer": 'bt709',
            "matrix": 'bt709',
            "tile-cols": 0,
            "tile-rows": 0,
            "threads": 2,
            # Add more parameters as needed
        },
        "1080p-hdr": {
            "primaries": 'bt2020',
            "transfer": 'smpte2084',
            "matrix": 'bt2020ncl',
            "mastering-display": master_display,
            "content-light": max_cll,
            "tile-cols": 0,
            "tile-rows": 0,
            "threads": 2,
            # Add more parameters as needed
        },
        "1440p-hdr": {
            "primaries": 'bt2020',
            "transfer": 'smpte2084',
            "matrix": 'bt2020ncl',
            "mastering-display": master_display,
            "content-light": max_cll,
            "tile-cols": 1,
            "tile-rows": 0,
            "threads": 4,
            # Add more parameters as needed
        },
        "2160p-hdr": {
            "primaries": 'bt2020',
            "transfer": 'smpte2084',
            "matrix": 'bt2020ncl',
            "mastering-display": master_display,
            "content-light": max_cll,
            "tile-cols": 2,
            "tile-rows": 1,
            "threads": 6,
            # Add more parameters as needed
        },
        # Add more presets as needed
    }.get(preset, {})
elif encoder == 'svt':
    default_values = {
        "tune": tune,
        "enable-qm": 1,
        "qm-min": qm_min,
        "qm-max": qm_max,
        "keyint": "10s",
        "enable-cdef": cdef,
        "enable-restoration": restoration,
        "enable-tf": tf,
        "scm": scm,
        "sharpness": sharpness,
        "film-grain": noiselevel,
        "film-grain-denoise": 0,
        "lp": threads,
        "lookahead": lookahead,
    }
    presets = {
        "720p": {
            "color-primaries": 1,
            "transfer-characteristics": 1,
            "matrix-coefficients": 1,
            "tile-columns": 0,
            "tile-rows": 0,
            # Add more parameters as needed
        },
        "1080p": {
            "color-primaries": 1,
            "transfer-characteristics": 1,
            "matrix-coefficients": 1,
            "tile-columns": 0,
            "tile-rows": 0,
            # Add more parameters as needed
        },
        "1080p-hdr": {
            "color-primaries": 9,
            "transfer-characteristics": 16,
            "matrix-coefficients": 9,
            "chroma-sample-position": 2,
            "mastering-display": master_display,
            "content-light": max_cll,
            "enable-hdr": 1,
            "tile-columns": 0,
            "tile-rows": 0,
            # Add more parameters as needed
        },
        "1440p-hdr": {
            "color-primaries": 9,
            "transfer-characteristics": 16,
            "matrix-coefficients": 9,
            "chroma-sample-position": 2,
            "mastering-display": master_display,
            "content-light": max_cll,
            "enable-hdr": 1,
            "tile-columns": 1,
            "tile-rows": 0,
            # Add more parameters as needed
        },
        "2160p-hdr": {
            "color-primaries": 9,
            "transfer-characteristics": 16,
            "matrix-coefficients": 9,
            "chroma-sample-position": 2,
            "mastering-display": master_display,
            "content-light": max_cll,
            "enable-hdr": 1,
            "tile-columns": 2,
            "tile-rows": 1,
            # Add more parameters as needed
        },
        # Add more presets as needed
    }.get(preset, {})
elif encoder == 'x265':
    default_values = {
        "preset": preset,
        "frame-threads": 1,
        "pools": threads,
        "keyint": video_framerate * 10,
    }
    presets = {}
else:
    default_values = {
        "threads": threads,
        "bit-depth": 10,
        "end-usage": "q",
        "aq-mode": 0,
        "deltaq-mode": 1,
        "enable-chroma-deltaq": 1,
        "tune-content": tune_content,
        "tune": "ssim",
        "lag-in-frames": lookahead,
        "enable-qm": 1,
        "qm-min": qm_min,
        "qm-max": qm_max,
        "sb-size": "dynamic",
        "kf-min-dist": 5,
        "kf-max-dist": video_framerate * 10,
        "disable-trellis-quant": 0,
        "enable-dnl-denoising": 0,
        "denoise-noise-level": noiselevel,
        "enable-keyframe-filtering": 1,
        "tile-columns": tile_columns,
        "tile-rows": tile_rows,
        "sharpness": sharpness,
        "enable-cdef": cdef,
        "enable-restoration": restoration,
        "enable-fwd-kf": 0,
        "arnr-strength": arnr_strength,
        "arnr-maxframes": arnr_maxframes,
        "quant-b-adapt": 1,
        "enable-global-motion": 0,
        "max-reference-frames": max_reference_frames,
    }
    # Define presets as dictionaries of encoder parameters
    presets = {
        "720p": {
            "color-primaries": "bt709",
            "transfer-characteristics": "bt709",
            "matrix-coefficients": "bt709",
            "tile-columns": 0,
            "tile-rows": 0,
            "max-partition-size": 32,
            "max-reference-frames": 5,
            # Add more parameters as needed
        },
        "720p-lavish": {
            "color-primaries": "bt709",
            "transfer-characteristics": "bt709",
            "matrix-coefficients": "bt709",
            "deltaq-mode": 6,
            "tile-columns": 0,
            "tile-rows": 0,
            "max-partition-size": 32,
            "ssim-rd-mult": 125,
            "luma-bias": 24,
            "luma-bias-midpoint": 25,
            "luma-bias-strength": 10,
            "max-reference-frames": 5,
            "arnr-strength": 0,
            "arnr-maxframes": 0,
            "tpl-strength": 0,
            "chroma-q-offset-u": -q + 2,
            "chroma-q-offset-v": -q + 2,
            "enable-experimental-psy": 1,
            # Add more parameters as needed
        },
        "1080p": {
            "color-primaries": "bt709",
            "transfer-characteristics": "bt709",
            "matrix-coefficients": "bt709",
            "tile-columns": 0,
            "tile-rows": 0,
            "max-partition-size": 32,
            "max-reference-frames": 4,
            # Add more parameters as needed
        },
        "1080p-lavish": {
            "color-primaries": "bt709",
            "transfer-characteristics": "bt709",
            "matrix-coefficients": "bt709",
            "deltaq-mode": 6,
            "tile-columns": 0,
            "tile-rows": 0,
            "max-partition-size": 32,
            "ssim-rd-mult": 125,
            "luma-bias": 24,
            "luma-bias-midpoint": 25,
            "luma-bias-strength": 10,
            "max-reference-frames": 4,
            "arnr-strength": 0,
            "arnr-maxframes": 0,
            "tpl-strength": 0,
            "chroma-q-offset-u": -q + 2,
            "chroma-q-offset-v": -q + 2,
            "enable-experimental-psy": 1,
            # Add more parameters as needed
        },
        "1080p-hdr": {
            "color-primaries": "bt2020",
            "transfer-characteristics": "smpte2084",
            "matrix-coefficients": "bt2020ncl",
            "deltaq-mode": 5,
            "tile-columns": 0,
            "tile-rows": 0,
            "max-partition-size": 32,
            "max-reference-frames": 4,
            # Add more parameters as needed
        },
        "1080p-hdr-lavish": {
            "color-primaries": "bt2020",
            "transfer-characteristics": "smpte2084",
            "matrix-coefficients": "bt2020ncl",
            "deltaq-mode": 6,
            "tile-columns": 0,
            "tile-rows": 0,
            "max-partition-size": 32,
            "ssim-rd-mult": 125,
            "luma-bias": 24,
            "luma-bias-midpoint": 25,
            "luma-bias-strength": 10,
            "max-reference-frames": 4,
            "arnr-strength": 0,
            "arnr-maxframes": 0,
            "tpl-strength": 0,
            "chroma-q-offset-u": -q + 2,
            "chroma-q-offset-v": -q + 2,
            "enable-experimental-psy": 1,
            # Add more parameters as needed
        },
        "1440p-hdr": {
            "color-primaries": "bt2020",
            "transfer-characteristics": "smpte2084",
            "matrix-coefficients": "bt2020ncl",
            "deltaq-mode": 5,
            "tile-columns": 1,
            "tile-rows": 0,
            "max-partition-size": 32,
            "max-reference-frames": 4,
            # Add more parameters as needed
        },
        "1440p-hdr-lavish": {
            "color-primaries": "bt2020",
            "transfer-characteristics": "smpte2084",
            "matrix-coefficients": "bt2020ncl",
            "deltaq-mode": 6,
            "tile-columns": 1,
            "tile-rows": 0,
            "max-partition-size": 32,
            "ssim-rd-mult": 125,
            "luma-bias": 24,
            "luma-bias-midpoint": 25,
            "luma-bias-strength": 10,
            "max-reference-frames": 4,
            "arnr-strength": 0,
            "arnr-maxframes": 0,
            "tpl-strength": 0,
            "chroma-q-offset-u": -q + 2,
            "chroma-q-offset-v": -q + 2,
            "enable-experimental-psy": 1,
            # Add more parameters as needed
        },
        "2160p-hdr": {
            "color-primaries": "bt2020",
            "transfer-characteristics": "smpte2084",
            "matrix-coefficients": "bt2020ncl",
            "deltaq-mode": 5,
            "tile-columns": 2,
            "tile-rows": 1,
            "max-partition-size": 32,
            "max-reference-frames": 4,
            # Add more parameters as needed
        },
        "2160p-hdr-lavish": {
            "color-primaries": "bt2020",
            "transfer-characteristics": "smpte2084",
            "matrix-coefficients": "bt2020ncl",
            "deltaq-mode": 6,
            "tile-columns": 2,
            "tile-rows": 1,
            "max-partition-size": 32,
            "ssim-rd-mult": 125,
            "luma-bias": 24,
            "luma-bias-midpoint": 25,
            "luma-bias-strength": 10,
            "max-reference-frames": 4,
            "arnr-strength": 0,
            "arnr-maxframes": 0,
            "tpl-strength": 0,
            "chroma-q-offset-u": -q + 2,
            "chroma-q-offset-v": -q + 2,
            "enable-experimental-psy": 1,
            # Add more parameters as needed
        },
        # Add more presets as needed
    }.get(preset, {})

if not presets and encoder != 'x265':
    print("No matching preset found.\n")
    sys.exit(1)

# Update or add parameters from the preset if they are not specified in valid_params
for key, value in presets.items():
    if key not in default_values or default_values[key] is None:
        default_values[key] = value

# Merge default parameters and preset parameters into a single dictionary
encode_params = {**default_values, **presets}

# Iterate through command-line arguments and update the encode_params dictionary
for key, value in vars(args).items():
    # Convert underscores to hyphens for comparison
    param_key = key.replace('_', '-')

    # Check if the parameter exists in encode_params and is not None
    if param_key in encode_params and value is not None:
        encode_params[param_key] = value

# Create a list of non-empty parameters in the encoder supported format
if encoder in ('svt', 'rav1e', 'x265'):
    encode_params = [f"--{key} {value}" for key, value in encode_params.items() if value is not None]
else:
    encode_params = [f"--{key}={value}" for key, value in encode_params.items() if value is not None]

if encoder == 'x265' and x265cl:
    encode_params.append(x265cl)

# print (encode_params)

# Determine the output folder name based on the encode_script
output_folder_name = os.path.splitext(os.path.basename(encode_script))[0]
output_folder_name = os.path.join(base_working_folder, output_folder_name)

# Clean up the target folder if it already exists
if os.path.exists(output_folder_name):
    print(f"\nCleaning up the existing folder: {output_folder_name}\n")
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
    output_final_ffmpeg = os.path.join(output_folder, f"{output_name}.mkv")
else:
    output_final_ffmpeg = os.path.join(output_folder, f"{output_name}.mp4")

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
    encode_sample(output_folder, encode_script)
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
encode_commands, input_files, chunklist, chunklist_dict = preprocess_chunks(encode_commands, input_files, chunklist)
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
        os._exit(1)

# Wait for all encoding processes to finish before concatenating
progress_bar.close()
print("Encoding for all scenes completed.\n")

concatenate(chunks_folder, input_files, output_final_ffmpeg, fr)
