# Chunk Norris
#
# A very simple Python script to do chunked encoding using the aomenc CLI encoder.
#
# Requirements: Python 3.10.x (possibly just 3.x), scene change list in x264/x265 QP file format, Avisynth, avs2yuv64, ffmpeg, aomenc (the lavish mod recommended)
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
from tqdm import tqdm


# Function to clean up a folder
def clean_folder(folder):
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
        if os.path.isfile(item_path):
            os.unlink(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)


def create_scxvid_file(scene_change_csv):
    if scd_method == 5:
        with open(encode_script, 'r') as file:
            # Read the first line from the original file
            source = file.readline()
        with open(scd_script, 'w') as scd_file:
            # Write the first line content to the new file
            scd_file.write(source)
            if downscale_scd:
                scd_file.write('\n')
                scd_file.write('ReduceBy2()\n')
                scd_file.write('Crop(16,16,-16,-16)\n')
            scd_file.write(f'SCXvid(log="{scene_change_csv}")')
    else:
        with open(scd_script, 'w') as scd_file:
            scd_file.write(f'Import("{encode_script}")\n')
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
        "-o", output_folder_name,
        "detect-adaptive",
        "-t", f"{scdthresh}",
        "-m", f"{min_chunk_length}",
        "list-scenes",
        "-f", scene_change_csv
    ]
    print("Detecting scene changes using PySceneDetect.\n")
    subprocess.run(scene_change_command)


def create_fgs_table():
    # Create the grain table only if it doesn't exist already
    if os.path.exists(output_grain_table) is False:
        grain_script = os.path.join(scripts_folder, f"grainscript.avs")

        if graintable_method == 1:
            with open(grain_script, 'w') as grain_file:
                grain_file.write(f'Import("{encode_script}")\n')
                grain_file.write('grain_frame_rate = Ceil(FrameRate())\n')
                grain_file.write('grain_frame_count = FrameCount()\n')
                grain_file.write(f'step = Ceil(grain_frame_count / ({grain_clip_length} - 1))\n')
                grain_file.write('SelectRangeEvery(step, grain_frame_rate * 2)')
        else:
            referencefile_start_frame = input("Please enter the first frame of FGS grain table process: ")
            referencefile_end_frame = input("Please enter the last frame of FGS grain table process (default 60 seconds of frames if empty): ")
            with open(grain_script, 'w') as grain_file:
                grain_file.write(f'Import("{encode_script}")\n')
                if referencefile_end_frame != '':
                    grain_file.write(f'Trim({referencefile_start_frame}, {referencefile_end_frame})')
                else:
                    grain_file.write('grain_frame_rate = Ceil(FrameRate())\n')
                    grain_file.write(f'grain_end_frame = {referencefile_start_frame} + (grain_frame_rate * 60)\n')
                    grain_file.write(f'Trim({referencefile_start_frame}, grain_end_frame)')

        # Create the encoding command lines
        avs2yuv_command_grain = [
            "avs2yuv64.exe",
            "-no-mt",
            grain_script,
            "-",
            #        "2> nul"
        ]

        aomenc_command_grain = [
            "aomenc.exe",
            *encode_params,
            "--passes=1",
            f"--cq-level={q}",
            "-o", output_grain_file_encoded,
            "-"
        ]

        x264_command_grain = [
            "x264.exe",
            "--demuxer", "y4m",
            "--preset", "slow",
            "--crf", "0",
            "-o", output_grain_file_lossless,
            "-"
        ]

        # Create the command line to compare the original and encoded files to get the grain table
        grav1synth_command = [
            "grav1synth.exe",
            "diff",
            "-o", output_grain_table,
            output_grain_file_lossless,
            output_grain_file_encoded
        ]

        print("Encoding the FGS analysis AV1 file.\n")
        avs2yuv_grain_process = subprocess.Popen(avs2yuv_command_grain, stdout=subprocess.PIPE, shell=True)
        aomenc_grain_process = subprocess.Popen(aomenc_command_grain, stdin=avs2yuv_grain_process.stdout, shell=True)
        aomenc_grain_process.communicate()

        print("\n\nEncoding the FGS analysis lossless file.\n")
        avs2yuv_grain_process = subprocess.Popen(avs2yuv_command_grain, stdout=subprocess.PIPE, shell=True)
        x264_grain_process = subprocess.Popen(x264_command_grain, stdin=avs2yuv_grain_process.stdout, shell=True)
        x264_grain_process.communicate()

        print("\nCreating the FGS grain table file.\n")
        subprocess.run(grav1synth_command)
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
                    if downscale_scd:
                        scd_file.write('\n')
                        scd_file.write('ReduceBy2()\n')
                        scd_file.write('Crop(16,16,-16,-16)')
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
                    scd_file.write('Crop(16,16,-16,-16)')
            pyscd(scd_script)
        else:
            print(f"Using scene change analysis script: {scd_script}.\n")
            pyscd(scd_script)
    else:
        print(f"Using scene change analysis script: {encode_script}.\n")
        pyscd(encode_script)

    return scene_changes


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
                end_frame = 0
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
                    end_frame = 0  # Set end_frame to 0 for the last scene
                # print(f'Next scene index: {next_scene_index}')
            chunk_length = end_frame - start_frame + 1
            chunk_length = 999999 if chunk_length < 0 else chunk_length

            chunkdata = {
                'chunk': chunk_number, 'length': chunk_length, 'start': start_frame, 'end': end_frame
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
                'chunk': chunk_number, 'length': chunk_length, 'start': start_frame, 'end': end_frame
            }
            chunklist.append(chunkdata)

#    print (chunklist)

    for i in chunklist:
        output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.webm")
        input_files.append(output_chunk)  # Add the input file for concatenation

    chunklist = sorted(chunklist, key=lambda x: x['length'], reverse=True)

    for i in chunklist:
        scene_script_file = os.path.join(scripts_folder, f"scene_{i['chunk']}.avs")
        output_chunk = os.path.join(chunks_folder, f"encoded_chunk_{i['chunk']}.webm")
        # Create the Avisynth script for this scene
        with open(scene_script_file, "w") as scene_script:
            scene_script.write(f'Import("{encode_script}")\n')
            scene_script.write(f"Trim({i['start']}, {i['end']})")

        avs2yuv_command = [
            "avs2yuv64.exe",
            "-no-mt",
            scene_script_file,  # Use the Avisynth script for this scene
            "-",
            "2> nul"
        ]

        aomenc_command = [
            "aomenc.exe",
            "-q",
            *encode_params,
            "--passes=1",
            f"--cq-level={q}",
            "-o", output_chunk,
            "-"
        ]

        encode_commands.append((avs2yuv_command, aomenc_command, output_chunk))
    return encode_commands, input_files, chunklist


# Function to execute encoding commands and print them for debugging
def run_encode_command(command):
    avs2yuv_command, aomenc_command, output_chunk = command
    avs2yuv_command = " ".join(avs2yuv_command)
    aomenc_command = " ".join(aomenc_command)

    # Print the aomenc encoding command for debugging
    # print(f"\naomenc command: {aomenc_command}")

    # Execute avs2yuv and pipe the output to aomenc
    avs2yuv_process = subprocess.Popen(avs2yuv_command, stdout=subprocess.PIPE, shell=True)
    aomenc_process = subprocess.Popen(aomenc_command, stdin=avs2yuv_process.stdout, shell=True)

    # Wait for aomenc to finish
    aomenc_process.communicate()

    return output_chunk


parser = argparse.ArgumentParser()
parser.add_argument('encode_script')
parser.add_argument('--preset', nargs='?', default='1080p', type=str)
parser.add_argument('--cpu', nargs='?', default=3, type=int)
parser.add_argument('--threads', nargs='?', default=8, type=int)
parser.add_argument('--q', nargs='?', default=14, type=int)
parser.add_argument('--min-chunk-length', nargs='?', default=64, type=int)
parser.add_argument('--max-parallel-encodes', nargs='?', default=6, type=int)
parser.add_argument('--noiselevel', nargs='?', type=int)
parser.add_argument('--sharpness', nargs='?', default=2, type=int)
parser.add_argument('--tile-columns', nargs='?', type=int)
parser.add_argument('--tile-rows', nargs='?', type=int)
parser.add_argument('--graintable-method', nargs='?', default=1, type=int)
parser.add_argument('--grain-clip-length', nargs='?', default=60, type=int)
parser.add_argument('--graintable', nargs='?', type=str)
parser.add_argument('--scd-method', nargs='?', default=1, type=int)
parser.add_argument('--scdthresh', nargs='?', type=float)
parser.add_argument('--downscale-scd', action="store_true")

# Set the base working folder, use double backslashes
base_working_folder = "F:\\Temp\\Captures\\encodes"

# Set the QP file path
scene_change_file_path = "F:\\Temp\\Captures"

# Command-line arguments
args = parser.parse_args()
encode_script = args.encode_script
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
grain_clip_length = args.grain_clip_length
scd_method = args.scd_method
scdthresh = args.scdthresh
downscale_scd = args.downscale_scd
cpu = args.cpu

# Set some more case dependent default values
if noiselevel is None or graintable or graintable_method > 0:
    noiselevel = 0
if scdthresh is None:
    if scd_method in (1, 2):
        scdthresh = 0.3
    else:
        scdthresh = 2.0

default_values = {
    "cpu-used": cpu,
    "threads": threads,
    "bit-depth": 10,
    "end-usage": "q",
    "aq-mode": 0,
    "deltaq-mode": 1,
    "enable-chroma-deltaq": 1,
    "tune-content": "psy",
    "tune": "ssim",
    "lag-in-frames": 64,
    "enable-qm": 1,
    "qm-min": 0,
    "qm-max": 8,
    "sb-size": 64,
    "row-mt": 1,
    "kf-min-dist": 5,
    "kf-max-dist": 480,
    "disable-trellis-quant": 0,
    "enable-dnl-denoising": 0,
    "denoise-noise-level": noiselevel,
    "enable-keyframe-filtering": 1,
    "tile-columns": tile_columns,
    "tile-rows": tile_rows,
    "sharpness": sharpness,
    "enable-cdef": 0,
    "enable-fwd-kf": 1,
    "arnr-strength": 0,
    "arnr-maxframes": 5,
    "quant-b-adapt": 1
}

# Define presets as dictionaries of encoder parameters
presets = {
    "720p": {
        "color-primaries": "bt709",
        "transfer-characteristics": "bt709",
        "matrix-coefficients": "bt709",
        "tile-columns": 0,
        "tile-rows": 0,
        # Add more parameters as needed
    },
    "1080p": {
        "color-primaries": "bt709",
        "transfer-characteristics": "bt709",
        "matrix-coefficients": "bt709",
        "tile-columns": 1,
        "tile-rows": 0,
        # Add more parameters as needed
    },
    "1080p-hdr": {
        "color-primaries": "bt2020",
        "transfer-characteristics": "smpte2084",
        "matrix-coefficients": "bt2020ncl",
        "deltaq-mode": 5,
        "tile-columns": 1,
        "tile-rows": 0,
        # Add more parameters as needed
    },
    "1440p-hdr": {
        "color-primaries": "bt2020",
        "transfer-characteristics": "smpte2084",
        "matrix-coefficients": "bt2020ncl",
        "deltaq-mode": 5,
        "tile-columns": 1,
        "tile-rows": 1,
        # Add more parameters as needed
    },
    "2160p-hdr": {
        "color-primaries": "bt2020",
        "transfer-characteristics": "smpte2084",
        "matrix-coefficients": "bt2020ncl",
        "deltaq-mode": 5,
        "tile-columns": 1,
        "tile-rows": 1,
        # Add more parameters as needed
    },
    # Add more presets as needed
}.get(preset, {})

# Update or add parameters from the preset if they are not specified in valid_params
for key, value in presets.items():
    if key not in default_values or default_values[key] is None:
        default_values[key] = value

# Merge default parameters and preset parameters into a single dictionary
encode_params = {**default_values, **presets}

# Create a list of non-empty parameters in the format "--key=value"
encode_params = [f"--{key}={value}" for key, value in encode_params.items() if value is not None]

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

# Store the full path of encode_script
encode_script = os.path.abspath(encode_script)

# Define final video file name
output_name = os.path.splitext(os.path.basename(encode_script))[0]
output_final_ffmpeg = os.path.join(output_folder, f"{output_name}.mkv")

# Generate the FGS analysis file names
output_grain_file_lossless = os.path.join(output_folder, f"{output_name}_lossless.264")
output_grain_file_encoded = os.path.join(output_folder, f"{output_name}_encoded.webm")
output_grain_table = os.path.split(encode_script)[0]
output_grain_table = os.path.join(output_grain_table, f"{output_name}_grain.tbl")

# Create the reference files for FGS
if graintable_method > 0:
    create_fgs_table()
    encode_params.append(f"--film-grain-table={output_grain_table}")
else:
    encode_params.append(f"--film-grain-table={graintable}")

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
encode_commands, input_files, chunklist = preprocess_chunks(encode_commands, input_files, chunklist)

# Run encoding commands with a set maximum of concurrent processes
completed_chunks = []

# Create a tqdm progress bar
progress_bar = tqdm(total=len(chunklist), desc="Progress", unit="step")

with concurrent.futures.ThreadPoolExecutor(max_parallel_encodes) as executor:
    futures = {executor.submit(run_encode_command, cmd): cmd for cmd in encode_commands}
    for future in concurrent.futures.as_completed(futures):
        output_chunk = future.result()
        progress_bar.update(1)
        completed_chunks.append(output_chunk)
        # print(f"Encoding for scene completed: {output_chunk}")


# Wait for all encoding processes to finish before concatenating
progress_bar.close()
print("Encoding for all scenes completed.")

# Create a list file for input files
input_list_txt = os.path.join(chunks_folder, "input_list.txt")

# Write the input file list to the text file
with open(input_list_txt, "w") as file:
    for input_file in input_files:
        file.write(f"file '{input_file}'\n")

# Define the ffmpeg concatenation command
ffmpeg_concat_command = [
    "ffmpeg",
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

# Print the ffmpeg concatenation command for debugging
# print("Concatenation Command (ffmpeg):")
# print(" ".join(ffmpeg_concat_command))

# Run the ffmpeg concatenation command
subprocess.run(ffmpeg_concat_command)

print(f"Concatenated video saved as {output_final_ffmpeg}")
