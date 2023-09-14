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
#
# Usage: python chunk_norris.py script.avs preset q min_chunk_length, for example:
# python chunk_norris.py greatmovie.avs 720p 16 120
#
# 1. The script creates a folder structure based on the AVS script name under the set base working folder, removing the existing folders with same name first.
#   
# 2. It searches for the QP file in the specified folder or its subfolders.
# 
# 3. The chunks to encode are created based on the QP file. If a chunk (scene) length is less than the specified minimum,
#    it will combine it with the next one and so on until the minimum length is met. The last scene can be shorter.
#    The encoder parameters are picked up from the default parameters + selected preset.
#
# 4. The encoding queue is ordered from longest to shortest chunk. This ensures that there will not be any single long encodes running at the end.
#    The last scene is encoded in the first batch of chunks since we don't know its length based on the QP file.
#   
# 5. The encoded chunks are concatenated in their original order to a Matroska container using ffmpeg.

import os
import subprocess
import sys
import concurrent.futures
import shutil

# Function to clean up a folder
def clean_folder(folder):
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
        if os.path.isfile(item_path):
            os.unlink(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)

# Function to find the scene change file recursively
def find_scene_change_file(start_dir, filename):
    for root, dirs, files in os.walk(start_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None

# Set the base working folder, use double backslashes
base_working_folder = "F:\\Temp\\Captures\\encodes"

# Set the QP file path
scene_change_file_path = "F:\\Temp\\Captures"

# Set the maximum number of parallel encodes
max_parallel_encodes = 6

# Check for command-line arguments
if len(sys.argv) != 5:
    print("Usage: python script.py encode_script.avs preset_name q min_chunk_length")
    sys.exit(1)

# Command-line arguments
encode_script = sys.argv[1]
preset_name = sys.argv[2]
q = int(sys.argv[3])
min_chunk_length = int(sys.argv[4])

# Define default encoding parameters common to each preset as a list
default_params = [
    "--cpu-used=3",
    "--threads=8",
    "--bit-depth=10",
    "--end-usage=q",
    "--aq-mode=0",
    "--enable-chroma-deltaq=1",
    "--tune-content=psy",
    "--tune=ssim",
    "--lag-in-frames=64",
    "--sb-size=dynamic",
    "--enable-qm=1",
    "--qm-min=0",
    "--qm-max=8",
    "--row-mt=1",
    "--kf-min-dist=5",
    "--kf-max-dist=480",
    "--disable-trellis-quant=0",
    "--enable-dnl-denoising=0",
    "--denoise-noise-level=0",
    "--enable-keyframe-filtering=1",
    "--tile-columns=0",
    "--tile-rows=0",
    "--sharpness=3",
    "--enable-cdef=0",
    "--enable-fwd-kf=1",
    "--arnr-strength=1",
    "--arnr-maxframes=5",
    "--quant-b-adapt=1"
]

# Define presets as lists of encoder parameters
presets = {
    "720p": [
        "--color-primaries=bt709",
        "--transfer-characteristics=bt709",
        "--matrix-coefficients=bt709"
        # Add more parameters as needed
    ],
    "1080p": [
        "--color-primaries=bt709",
        "--transfer-characteristics=bt709",
        "--matrix-coefficients=bt709"
        # Add more parameters as needed
    ],
    # Add more presets as needed
}.get(preset_name, [])

# Merge default parameters and preset parameters into a single list
encode_params = default_params + presets

# Determine the output folder name based on the encode_script
output_folder_name = os.path.splitext(os.path.basename(encode_script))[0]
output_folder_name = os.path.join(base_working_folder, output_folder_name)

# Clean up the target folder if it already exists
if os.path.exists(output_folder_name):
    print(f"Cleaning up the existing folder: {output_folder_name}")
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

# Find the scene change file recursively
scene_change_filename = os.path.splitext(os.path.basename(encode_script))[0] + ".qp.txt"
scene_change_file_path = find_scene_change_file(scene_change_file_path, scene_change_filename)

if scene_change_file_path is None:
    print(f"Scene change file not found: {scene_change_filename}")
    sys.exit(1)

# Read scene changes from the file
scene_changes = []

with open(scene_change_file_path, "r") as scene_change_file:
    for line in scene_change_file:
        parts = line.strip().split()
        if len(parts) == 2:
            start_frame = int(parts[0])
            scene_changes.append(start_frame)

# Debug: Print the scene changes from the file
print("Scene Changes from File:")
for i, start_frame in enumerate(scene_changes):
    end_frame = 0 if i == len(scene_changes) - 1 else scene_changes[i + 1] - 1
    print(f"Scene {i}: Start Frame: {start_frame}, End Frame: {end_frame}")

# Step 2: Encode the Scenes
encode_commands = [] # List to store the encoding commands
input_files = []  # List to store input files for concatenation
chunklist = [] # Helper list for producing the encoding and concatenation lists

i = 0
combined = False
chunk_number = 1

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

    if combined:
        i = next_scene_index
        combined = False
    else:
        i += 1

    chunk_length = end_frame - start_frame + 1
    chunk_length = 999999 if chunk_length < 0 else chunk_length
    chunkdata = {
            'chunk': chunk_number, 'length': chunk_length, 'start': start_frame, 'end': end_frame
        }
    chunklist.append(chunkdata)

    chunk_number += 1

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
    "-"
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
#
    encode_commands.append((avs2yuv_command, aomenc_command, output_chunk))

# Function to execute encoding commands and print them for debugging
def run_encode_command(command):
    avs2yuv_command, aomenc_command, output_chunk = command
    avs2yuv_command = " ".join(avs2yuv_command)
    aomenc_command = " ".join(aomenc_command)

    # Print the aomenc encoding command for debugging
    # print(f"aomenc command: {aomenc_command}")

    # Execute avs2yuv and pipe the output to aomenc
    avs2yuv_process = subprocess.Popen(avs2yuv_command, stdout=subprocess.PIPE, shell=True)
    aomenc_process = subprocess.Popen(aomenc_command, stdin=avs2yuv_process.stdout, shell=True)

    # Wait for aomenc to finish
    aomenc_process.communicate()

    return output_chunk

# Run encoding commands with a set maximum of concurrent processes
completed_chunks = []

with concurrent.futures.ThreadPoolExecutor(max_parallel_encodes) as executor:
    futures = {executor.submit(run_encode_command, cmd): cmd for cmd in encode_commands}
    for future in concurrent.futures.as_completed(futures):
        output_chunk = future.result()
        completed_chunks.append(output_chunk)
        print(f"Encoding for scene completed: {output_chunk}")

# Wait for all encoding processes to finish before concatenating
print("Encoding for all scenes completed.")

# Output final video file name
output_final_ffmpeg = os.path.join(output_folder, f"output_final.mkv")

# Create a list file for input files
input_list_txt = os.path.join(chunks_folder, "input_list.txt")

# Write the input file list to the text file
with open(input_list_txt, "w") as file:
    for input_file in input_files:
        file.write(f"file '{input_file}'\n")

# Define the ffmpeg concatenation command
ffmpeg_concat_command = [
    "ffmpeg",
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
