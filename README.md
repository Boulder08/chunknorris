# Chunk Norris

A Python script for chunked encoding using the aomenc CLI encoder


---

## Overview

**Chunk Norris** is a simple Python script designed for chunked encoding using the **aomenc CLI encoder**. This script allows you to process large video files more efficiently by dividing them into smaller, manageable chunks for parallel encoding. It also offers the flexibility to set various encoding parameters and presets to suit your specific needs.

---

## Prerequisites

Before using **Chunk Norris**, ensure you have the following dependencies installed and properly configured on your system:

- Python 3.10.x (or a compatible 3.x version)
- (A scene change list in x264/x265 QP file format)
- Avisynth+
- avs2yuv64 (well, the 32-bit one also works if your whole chain is 32-bit)
- FFmpeg
- aomenc (the lavish mod is recommended)
- grav1synth (in case of --graintable-method 1 or 2)

Additionally, make sure that all the tools are accessible from your system's PATH or in the directory where you run this script.

---

## Process

1. The script creates a folder structure based on the AVS script name under the set base working folder, removing the existing folders with same name first.
   
2. It searches for the QP file in the specified folder or its subfolders, or if --ffmpeg-scd is set, uses ffmpeg to scan for scene changes.
 
3. The chunks to encode are created based on the scene changes. If a chunk (scene) length is less than the specified minimum,
   it will combine it with the next one and so on until the minimum length is met. The last scene can be shorter.
   The encoder parameters are picked up from the default parameters + selected preset.

4. The encoding queue is ordered from longest to shortest chunk. This ensures that there will not be any single long encodes running at the end.
   The last scene is encoded in the first batch of chunks since we don't know its length based on the scene changes.
   
5. If you have enabled the creation of a grain table or supply it with a separate parameter, it is applied during the encode.

6. You can control the amount of parallel encodes by a CLI parameter. Tune the amount according to your system, both CPU and memory-wise.
   
7. The encoded chunks are concatenated in their original order to a Matroska container using ffmpeg.

---

## Configuration

You can customize **Chunk Norris** by adjusting the following settings in the script:

- **default_params**: Set common encoding parameters as a list.
- **base_working_folder**: Define the base working folder where the script will organize its output.
- **scene_change_file_path**: Specify the path to the QP file containing scene change information.
- Command-line arguments: Modify encoding parameters such as preset, quality (q), minimum chunk length, and more by passing them as arguments when running the script.

---

## Usage

To use **Chunk Norris**, run the script from the command line with the following syntax:

```bash
python chunk_norris.py encode_script [options]
```

---

## Options

**--preset**: Choose a preset defined in the script. You can add your own and change the existing ones.
- Example: --preset "720p"
- Default: "1080p"

**--q**: Defines a Q value the encoder will use. It does a one-pass encode in Q mode, which is the closest to constant quality with a single pass.
- Example: --q 16
- Default: 14

**--min-chunk-length**: Defines the minimum encoded chunk length in frames. If there are detected scenes shorter than this amount, the script combines adjacent scenes until the condition is satisfied.
- Example: --min-chunk-length 100
- Default: 64 (the same as --lag-in-frames in the default parameters)

**--max-parallel-encodes**: Defines how many simultaneous encodes may run. Choose carefully, and try to avoid saturating your CPU or exhausting all your memory.
- Example: --max-parallel-encodes 8
- Default: 10

**--threads**: Defines the amount of threads each encoder may utilize. Keep it at least at 2 to allow threaded lookahead.
- Example: --threads 4
- Default: 8

**--noiselevel**: Defines the strength of the internal Film Grain Synthesis. Disabled if a grain table is used.
- Example: --noiselevel 20
- Default: 0

**--graintable-method**: Defines the automatic method for creating a Film Grain Synthesis grain table file using grav1synth. The table is then automatically applied while encoding.
- --graintable-method 0 skips creation
- --graintable-method 1 creates a table based on two-second long chunks picked evenly throughout the whole video. Use --grain-clip-length to define the amount of chunks.
- --graintable-method 2 creates a table based on a user set range.
- The grain table file is placed in the same folder as the encoding script, named "'encoding_script'_grain.tbl". If it already exists, a new one is not created.
- Example: --graintable-method 1
- Default: 1

**--grain-clip-length**: Defines the amount of chunks used for creating the Film Grain Synthesis grain table, when --graintable-method is 1.
- Example: --grain-clip-length 120
- Default: 60

**--graintable**: Defines a (full) path to an existing Film Grain Synthesis grain table file, which you can get by using grav1synth.
- Example: --graintable C:\Temp\grain.tbl
- Default: None

**--ffmpeg-scd**: Defines the method for scene change detection.
- --ffmpeg-scd 0 uses a QP file style list of scene changes. It attempts to find the file from the path where the encoding script is, searching also in subfolders if needed.
- --ffmpeg-scd 1 uses ffmpeg for detection, and uses a separate Avisynth script. If it finds one from the encoding script path with the same name as the encoding script with '_scd' added at the end, it uses that.
  Otherwise a new file will be created based on the encoding script, loading only the source. Please make sure the source is loaded in the first line of the encoding script!
- --ffmpeg-scd 2 uses ffmpeg for detection, and uses the encoding script to do it.
- Example: --ffmpeg-scd 1
- Default: 0

**--scdthresh**: Defines the threshold for scene change detection in ffmpeg. Lower values mean more scene changes detected, but also more false detections.
- Example: --ffmpeg-scd 0.4
- Default: 0.3

**--downscale-scd**: Set this parameter to enable downscaling using ReduceBy2() in the scene change detection script (if --ffmpeg-scd is 1).
- Example: --downscale-scd
- Default: None

**encode_script**: Give the path (full or relative to the path where you run the script) to the Avisynth script you want to use for encoding.

---

## Output

The script will create the following folders in the specified base_working_folder:

- **output**: Contains the final concatenated video file.
- **scripts**: Stores Avisynth scripts for each scene.
- **chunks**: Stores encoded video chunks.

The final concatenated video file will be named based on the input Avisynth script and saved in the output folder.

---

## Example

Here's an example of how to use Chunk Norris:

```bash
python chunk_norris.py my_video.avs --preset "1080p" --q 18 --max-parallel-encodes 6 --ffmpeg-scd 1 --downscale-scd
```

This command will encode the video specified in my_video.avs using the '1080p' preset, a quality level of 18, and a maximum of 6 parallel encoding processes.
