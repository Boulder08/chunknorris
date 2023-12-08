# Chunk Norris

A Python script for chunked encoding using an AV1 CLI encoder


---



## Overview

**Chunk Norris** is a simple Python script designed for chunked encoding of AV1 streams. This script allows you to process large video files more efficiently by dividing them into smaller, manageable chunks for parallel encoding. It also offers the flexibility to set various encoding parameters and presets to suit your specific needs.

---



## Prerequisites

Before using **Chunk Norris**, ensure you have the following dependencies installed and properly configured on your system:

- Python 3.10.x (or a compatible 3.x version) + support modules (see the beginning of the script)
- (A scene change list in x264/x265 QP file format)
- Avisynth+ (+ the SCXviD plugin if scdmethod 5 or 6)
- (avs2yuv64 (well, the 32-bit one also works if your whole chain is 32-bit, just rename it to avs2yuv64.exe))
- ffmpeg
- PySceneDetect (Python module) **Note that you need to install the module using pip and also install MoviePy. When running Chunk Norris, do not worry about the error messages it shows using PySceneDetect.**
- ffmpeg-python (Python module) **Note that you need to install ffmpeg-python, not ffmpeg**
- aomenc (the lavish mod is recommended) / svt-av1 (see below for compatible binaries)
- grav1synth (in case of --graintable-method 1)

Additionally, make sure that all the tools are accessible from your system's PATH or in the directory where you run this script.

I built some aomenc-lavish binaries for easy access, find the package here:
https://drive.google.com/file/d/1h8K0_0P730firYb8cmM0_jCARYn8v53u/view?usp=drive_link **(latest mainline-merge including the new deltaq-mode 6)**

Based on the current (as of November 19th, 2023) source by clybius (thanks!)

The source: https://github.com/Clybius/aom-av1-lavish/tree/opmox/mainline-merge


**SVT-AV1 binaries to use with this script**: https://drive.google.com/file/d/1HheHLCXxc91T_K6gcTNtcN1JNRn-xN7i/view?usp=drive_link
- includes the var-deltaq optimizations (https://gitlab.com/AOMediaCodec/SVT-AV1/-/issues/2105#note_1666136918), choose one you wish to apply and rename the binary to svtav1encapp.exe
The source: https://github.com/BlueSwordM/SVT-AV1 (thanks!)


---



## Process

1. The script creates a folder structure based on the AVS script name under the set base working folder, removing the existing folders with same name first.
   
2. It searches for the QP file in the specified folder or its subfolders, or uses the various scene change detection methods set by --scd-method.
 
3. The chunks to encode are created based on the scene changes. If a chunk (scene) length is less than the specified minimum,
   it will combine it with the next one and so on until the minimum length is met. The last scene can be shorter.
   The encoder parameters are picked up from the default parameters + selected preset.

4. The encoding queue is ordered from longest to shortest chunk. This ensures that there will not be any single long encodes running at the end.
   
5. If you have enabled the creation of a grain table or supply it with a separate parameter, it is applied during the encode.

6. You can control the amount of parallel encodes by a CLI parameter. Tune the amount according to your system, both CPU and memory-wise.
   
7. The encoded chunks are concatenated in their original order to a Matroska container using ffmpeg.

---



## Configuration

You can customize **Chunk Norris** by adjusting the following settings in the script:

- **default_params**: Set common encoding parameters as a list.
- **base_working_folder**: Define the base working folder where the script will organize its output.
- **presets**: Add or edit the existing presets for a set of encoder options. The ones you set in the preset override default ones.
- Command-line arguments: Modify encoding parameters such as preset, quality (q), minimum chunk length, and more by passing them as arguments when running the script.
  
  **There are some ready-made film grain tables available in the av1-graintables directory.**

---


## Usage

To use **Chunk Norris**, run the script from the command line with the following syntax:

```bash
python chunk_norris.py encode_script [options]
```

---



## Some ideas for number of parallel encodes

I've found these numbers generally saturating the CPU near ~100% on aomenc but not go overboard, using a Ryzen 5950X (16c/32t):
- 1440p : 6
- 1080p : 8
- 720p : 10

Naturally this also depends on the number of tiles, these figures are tested using the pre-made presets. I've used --threads 8 myself with no ill effects on aomenc, svt-av1 uses threads much more so very system- and source dependent!

---



## Options

**--encoder**: Chooses the encoder to use. Currently available: aomenc, svt
- Example: --encoder aom
- Default: svt

**--preset**: Choose a preset defined in the script. You can add your own and change the existing ones.
- Example: --preset 720p
- Default: 1080p

**--cpu**: Defines the '--cpu-used' parameter in aomenc, or '--preset' in svt-av1. Lower is better, but also slower.
- Example: --cpu 6
- Default: 3

**--threads**: Defines the amount of threads each encoder may utilize. Keep it at least at 2 to allow threaded lookahead in aomenc and much better performance in svt-av1.
- Example: --threads 4
- Default: 6 for aomenc, 2 for svt-av1

**--q**: Defines a Q value the encoder will use. In aomenc, the script does a one-pass encode in Q mode, which is the closest to constant quality with a single pass. In svt-av1, CRF mode is used.
- Example: --q 16
- Default: 14

**--min-chunk-length**: Defines the minimum encoded chunk length in frames. If there are detected scenes shorter than this amount, the script combines adjacent scenes until the condition is satisfied.
- Example: --min-chunk-length 100
- Default: 64 (the same as --lag-in-frames in the default parameters)

**--max-parallel-encodes**: Defines how many simultaneous encodes may run. Choose carefully, and try to avoid saturating your CPU or exhausting all your memory.
- Example: --max-parallel-encodes 8
- Default: 4

**--noiselevel**: Defines the strength of the internal Film Grain Synthesis. Disabled if a grain table is used.
- Example: --noiselevel 20
- Default: 0

**--sharpness**: Defines the '--sharpness' parameter in aomenc. It is a psy RD setting more than a sharpener, lower values allocate more bits to flat areas, blurring sharper ones and vice versa.
- Example: --sharpness 3
- Default: 2

**--tile-columns** and **--tile-rows**: Define the corresponding parameters for splitting the encoding (and decoding) into multiple tiles in aomenc. Svt-av1 optimizes tiles automatically based on the resolution.
- Example: --tile-columns 1 --tile-rows 1
- Default: None for both

**--arnr-strength** and **--arnr-maxframes**: Define the parameters for internal alt-ref frame denoising in aomenc.
- Example: --arnr-strength 3 --arnr-maxframes 9
- Default: 2 for arnr-strength, 7 for arnr-maxframes

**--tpl-strength**: Defines the multiplier (percentage) for temporal filtering (arnr-strength) in aomenc, 100 being equal to "as is".
- Example: --tpl-strength 50
- Default: None (100)

**--max-reference-frames**: Defines the maximum amount of reference frames aomenc may use. For live action content, a lower amount like 4-5 is enough; for animated content, using 7 could be beneficial.
- Example: --max-reference-frames 4
- Default: 5

**--tune**: Defines the tuning to use in aomenc. Use 'ssim' or 'omni'.
- Example: --tune omni
- Default: ssim

**--tune-content**: Defines the content-based (psy) tuning to use in aomenc. With the lavish mod, 'psy' is recommended.
- Example: --tune-content default
- Default: psy

**--luma-bias**, **--luma-bias-strength** and **--luma-bias-midpoint**: Define the parameters for the luma bias modification in aomenc-lavish. See https://www.desmos.com/calculator/nwxoa44sie (lower y means less compression)
- Example: --luma-bias 10
- Default: None

**--deltaq-mode**: Defines the deltaq-mode parameter in aomenc. In aomenc-lavish, mode 6 is highly recommended for both SDR and HDR encodes (the effect is close to modes 1+5 with dark bias for SDR and bright bias for HDR). With vanilla aomenc, use 1 for SDR and 5 for HDR.
**If you use deltaq-mode 6, make sure you feed 10-bit data into the encoder as the bias table is not yet normalized depending on the source bitdepth.**
- Example: --deltaq-mode 1
- Default: None

**--graintable-method**: Defines the automatic method for creating a Film Grain Synthesis grain table file using grav1synth. The table is then automatically applied while encoding.
- --graintable-method 0 skips creation.
- --graintable-method 1 creates a table based on a user set range.
- The grain table file is placed in the same folder as the encoding script, named "'encoding_script'_grain.tbl". If it already exists, a new one is not created.
- Make sure you select a range which represents the source well and does not have any scene cuts to have a constant grain layer - 100-200 frames is good enough.
- Example: --graintable-method 0
- Default: 1

**--graintable-sat**: Defines the level of saturation to have in the graintable analysis clip. The recommended range is 0..1 where 0 means black-and-white and 1 does nothing. Uses the Avisynth+ built-in filter "Tweak".
- Example: --graintable-sat 0.2
- Default: 0 for aomenc, 1.0 for svt-av1

**--graintable**: Defines a (full) path to an existing Film Grain Synthesis grain table file, which you can get by using grav1synth. There are also some tables in the av1-graintables directory. Note that sometimes it is a good option to use a B/W grain table as ones with chroma grain can increase saturation of the video too much.
The lower resolution tables often contain a little more, or sharper grain compared to the higher resolution counterparts.
- Example: --graintable C:\Temp\grain.tbl
- Default: None

**--scd-method**: Defines the method for scene change detection.
- --scd-method 0 uses a QP file style list (with only keyframes) of scene changes. It attempts to find the file from the path where the encoding script is, searching also in subfolders if needed.
- --scd-method 1 uses ffmpeg for detection, and uses a separate Avisynth script. If it finds one from the encoding script path with the same name as the encoding script with '_scd' added at the end, it uses that.
  Otherwise a new file will be created based on the encoding script, loading only the source. Please make sure the source is loaded in the first line of the encoding script!
- --scd-method 2 uses ffmpeg for detection, and uses the encoding script to do it.
- --scd-method 3 uses PySceneDetect for detection and a separate script like in method 1.
- --scd-method 4 uses PySceneDetect for detection and the encoding script like in method 2.
- --scd-method 5 uses SCXviD for detection and a separate script like in method 1.
- --scd-method 6 uses SCXviD for detection and the encoding script like in method 2.
- Example: --scd-method 5
- Default: 3

**--scd-tonemap**: Defines if the Avisynth+ plugin DGHDRtoSDR should be used for tone mapping an HDR source in the scene change detection phase (improves accuracy).
- Example: --scd-tonemap 0
- Default: 0 for SDR, 1 for HDR sources

**--scdthresh**: Defines the threshold for scene change detection in ffmpeg or PySceneDetect. Lower values mean more scene changes detected, but also more false detections.
- Example: --scdthresh 0.4
- Default: 0.3 for --scd-method 1 and 2, 3.0 for --scd-method 3 and 4

**--downscale-scd**: Set this parameter to enable downscaling in the scene change detection script using the factor set by the parameter. Applies if --scd-method is 1, 3 or 5. Improves performance a lot without much effect on accuracy.
- Example: --downscale-scd 2
- Default: 4

**--decode-method**: Selects between avs2yuv64 or ffmpeg as the application that loads the encoding script and pipes to aomenc. There should not be a difference between the two, but in case of any problems, it might be a good idea to change the decoder.
- --decode-method 0 uses avs2yuv64.
- --decode-method 1 used ffmpeg.
- Example: --decode-method 0
- Default: 1

**--credits-start-frame**: Defines the starting frame of end credits to allow encoding them at a lower Q for increased space savings. Currently, the script will then assume that everything until the end of the video is credits and encodes them as a separate chunk.
- Example: --credits-start-frame 75120
- Default: None

**--credits-q**: Defines the Q value to use for encoding the credits.
- Example: --credits-q 36
- Default: 32

**--credits-cpu**: Defines the '--cpu-used' parameter for the credits section.
- Example: --credits-cpu 6
- Default: cpu + 1

**--mastering** and **--cll**: Defines the HDR related mastering display parameters. See https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Parameters.md

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
python chunk_norris.py my_video.avs --preset "720p" --q 18 --max-parallel-encodes 4 --scd-method 3
```

This command will use svt-av1 to encode the video specified in my_video.avs using the '720p' preset, a quality level of 18, and a maximum of 4 parallel encoding processes. It uses PySceneDetect for scene change detection.
