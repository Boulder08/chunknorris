# Chunk Norris

A Python script for chunked encoding using an AV1 or x265 CLI encoder

---


## Overview

**Chunk Norris** is a simple Python script designed for chunked encoding of AV1 or HEVC streams. This script allows you to process large video files more efficiently by dividing them into smaller, manageable chunks for parallel encoding. It also offers the flexibility to set various encoding parameters and presets to suit your specific needs.


---


## Prerequisites

Before using **Chunk Norris**, ensure you have the following dependencies installed and properly configured on your system:

- Python 3.10.x (or a compatible 3.x version) + support modules (see the beginning of the script)
- (A scene change list in x264/x265 QP file format)
- Avisynth+ (+ the SCXviD plugin if scdmethod 2)
- (avs2yuv64 (well, the 32-bit one also works if your whole chain is 32-bit, just rename it to avs2yuv64.exe))
- ffmpeg
- av-scenechange if scdmethod 1 is enabled
- ffmpeg-python (Python module) **Note that you need to install ffmpeg-python, not ffmpeg**
- svt-av1-hdr / aomenc (the lavish mod is recommended) / rav1e / x265
- grav1synth (in case of --graintable-method 1)
- Vapoursynth in case you want to use the metrics based Q adjusting feature

Additionally, make sure that all the tools are accessible from your system's PATH or in the directory where you run this script.


---


## Process

1. The script creates a folder structure based on the AVS script name under the set base working folder, removing the existing folders with same name first.
   
2. It searches for the QP file in the specified folder or its subfolders, or uses the various scene change detection methods set by --scd-method.
 
3. The chunks to encode are created based on the scene changes. If a chunk (scene) length is less than the specified minimum,
   it will combine it with the next one and so on until the minimum length is met. The last scene can be shorter.
   The encoder parameters are picked up from the default parameters + selected preset.

4. The encoding queue is ordered from longest to shortest chunk. This ensures that there will not be any single long encodes running at the end.
   
5. If you have enabled the creation of a grain table or supply it with a separate parameter, it is applied during the encoding process.

6. If you use the qadjust mode, the script will analyse the video and adjust Q/CRF by chunk based on the results and/or your set target score.

7. You can control the amount of parallel encodes by a CLI parameter. Tune the amount according to your system, both CPU and memory-wise.
   
8. The encoded chunks are concatenated in their original order to a Matroska (AV1, HEVC with mkvmerge) or MP4 (HEVC with ffmpeg) container.


---


## Configuration

You can customize **Chunk Norris** by adjusting the following settings:

- **default_values**: Set common encoding parameters as a list in the script itself.
- **presets**: Add or edit the existing presets for a set of encoder options, found in presets.ini.
- **base_working_folder**: Define the base working folder where the script will organize its output, found in presets.ini under the **paths** section.
- **command-line arguments**: Modify encoding parameters such as preset, quality (q), minimum chunk length, and more by passing them as arguments when running the script.

The priority of settings is 1) from selected preset in presets.ini, 2) from selected encoder's default settings in presets.ini, 3) default values from script. That is, common keys from the preset will override the default settings etc.
  
  **There are some ready-made film grain tables available in the av1-graintables directory.**


---


## Usage

To use **Chunk Norris**, run the script from the command line with the following syntax:

```bash
python chunk_norris.py encode_script [options]
```

---


## Options

**--encoder**: Chooses the encoder to use. Currently available: aomenc, svt, rav1e, x265
- Example: --encoder aom
- Default: svt

**--preset**: Choose a preset, which you have in **presets.ini**. You can add your own and change the existing ones. Please see the file for naming convention and usage.
**Note: you can choose multiple presets as a comma separated list. The script will merge the presets based on the order, use the --list-parameters option to verify!**
- Example: --preset 720p
- Default: 1080p

**--cpu**: Defines the '--cpu-used' parameter in aomenc, '--preset' in svt-av1 or '-s' in rav1e. Lower is better, but also slower.
- Example: --cpu 6
- Default: 3

**--threads**: Defines the amount of threads each encoder may utilize. Keep it at least at 2 to allow threaded lookahead in aomenc and much better performance in svt-av1.
- Example: --threads 4
- Default: 6 for aomenc, rav1e and x265, 4 for svt-av1

**--q**: Defines a Q value the encoder will use. In aomenc, the script does a one-pass encode in Q mode, which is the closest to constant quality with a single pass. In svt-av1 and x265, CRF mode is used.
- Example: --q 16
- Default: 18 for aomenc, svt-av1 and x265, 60 for rav1e

**--min-chunk-length**: Defines the minimum encoded chunk length in frames. If there are detected scenes shorter than this amount, the script combines adjacent scenes until the condition is satisfied.
- Example: --min-chunk-length 100
- Default: 2 seconds of video, for svt fine-tuned to match the hierachical levels of a GOP optimally

**--max-parallel-encodes**: Defines how many simultaneous encodes may run. Choose carefully, and try to avoid saturating your CPU or exhausting all your memory. Rav1e most likely threads worse so a higher amount is recommended.
- Example: --max-parallel-encodes 8
- Default: 4

**--graintable-method**: Defines the automatic method for creating a Film Grain Synthesis grain table file using grav1synth. The table is then automatically applied while encoding.
- --graintable-method 0 skips creation.
- --graintable-method 1 creates a table based on a user set range.
- The grain table file is placed in the same folder as the encoding script, named "'encoding_script'_grain.tbl". If it already exists, a new one is not created.
- Make sure you select a range which represents the source well and does not have any scene cuts to have a constant grain layer - 100-200 frames is good enough.
- Example: --graintable-method 1
- Default: 0

**--graintable-sat**: Defines the level of saturation to have in the graintable analysis clip. The recommended range is 0..1 where 0 means black-and-white and 1 does nothing. Uses the Avisynth+ built-in filter "Tweak".
- Example: --graintable-sat 0.2
- Default: 0 for aomenc, 1.0 for svt-av1

**--graintable**: Defines a (full) path to an existing Film Grain Synthesis grain table file, which you can get by using grav1synth. There are also some tables in the av1-graintables directory. Note that sometimes it is a good option to use a B/W grain table as ones with chroma grain can increase saturation of the video too much.
The lower resolution tables often contain a little more, or sharper grain compared to the higher resolution counterparts.
- Example: --graintable C:\Temp\grain.tbl
- Default: None

**--create-graintable**: Enables the mode for only creating the film grain table.

**--scd-method**: Defines the method for scene change detection.
- --scd-method 0 uses a QP file style list (with only keyframes) of scene changes. It attempts to find the file from the path where the encoding script is, searching also in subfolders if needed.
- --scd-method 1 uses av-scenechange for detection, and uses a separate Avisynth script. If it finds one from the encoding script path with the same name as the encoding script with '_scd' added at the end, it uses that.
  Otherwise, a new file will be created based on the encoding script, loading only the source. Please make sure the source is loaded in the first line of the encoding script!
- --scd-method 2 uses SCXviD for detection and a separate script like in method 1.
- Example: --scd-method 2
- Default: 1

**--scd-tonemap**: Defines if the Avisynth+ plugin DGHDRtoSDR should be used for tone mapping an HDR source in the scene change detection phase.
- Example: --scd-tonemap 1
- Default: 0

**--scdetect-only**: Enables the mode for only running the scene change detection.

**--downscale-scd**: Set this parameter to enable downscaling in the scene change detection script using the factor set by the parameter. Improves performance a lot without much effect on accuracy.
- Example: --downscale-scd 4
- Default: 2

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
- Default: 32 for aomenc and svt-av1, 180 for rav1e and q + 8 for x265.

**--credits-cpu**: Defines the '--cpu-used' parameter for the credits section.
- Example: --credits-cpu 6
- Default: cpu + 1

**--graintable-cpu**: Defines the '--cpu-used' parameter for the FGS analysis.
- Example: --graintable-cpu 6
- Default: Same as --cpu

**--master-display** and **--max-cll**: Defines the HDR related mastering display parameters. See https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/Parameters.md.

**NOTE:** If you use DGIndexNV to index the source file, you can copy-paste the data from the end of the .dgi file for these parameters and the script will automatically adjust the values according to what svt-av1 or rav1e expects. For x265, the values are passed as-is.

For example --master-display "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(40000000,50)" --max-cll "3241,902" would be transformed to --master-display G(0.265,0.69)B(0.15,0.06)R(0.68,0.32)WP(0.313,0.329)L(4000,0.005) --max-cll 3241,902 when processing and svt-av1 or rav1e is the encoder.

**--extracl**: Defines a string of parameters to feed to the encoder in addition to the preset. Remember to use double quotes, and there is no sanity check! The parameters you enter will override the ones from the default settings and selected presets.
- Example: --extracl "--ac-bias 1.5 --film-grain 10"
- Default: None

**--sample-start-frame** and **--sample-end-frame**: Defines the range to encode a sample from. The normal script and encode settings will be used, so you can validate for example the film grain/photon noise level using this parameter pair.
- Example: --sample-start-frame 10200 --sample-end-frame 10500
- Default: None

**--rpu**: Path to the Dolby Vision RPU file, enables Dolby Vision encoding mode. The script will split the RPU based on the chunks. Note that you need to have mkvmerge in PATH in order to be able to use the DoVi mode, ffmpeg loses the metadata when joining chunks at the end.
- Example: --rpu c:\encoding\rpus\movie_rpu.bin
- Default: None

**--cudasynth**: If you are using Donald Graft's CUDASynth version of DGDecode (improves HW decoding of video), you can enable this option for better performance when tonemapping HDR to SDR for scene change detection.

**--list-parameters**: Outputs a list of parameters that the selected encoder would use with your settings.

**--qadjust**: Enables a special mode for running an analysis pass of the source in order to adjust the Q/CRF value by chunk to make the final quality level more constant.
The chunk analysis data is output to a JSON file into the 'output' folder for validation. Works on svt-av1 (both SSIMU2 and Butteraugli) and x265 (SSIMU2)
**A lot of the code is from these two wonderful projects - thank you:**
https://github.com/nekotrix/auto-boost-algorithm (algo v2.0)
https://github.com/Akatmks/Akatsumekusa-Encoding-Scripts (namely the Butteraugli mean branch)
**A special thank you to Line-fr for creating the Vship plugin!**
https://codeberg.org/Line-fr/Vship

Requires Vapoursynth, (vstools,) BestSource, Vship/vszip.
The results are saved in the output folder in a separate JSON file. If the script finds an existing result file, it prompts you to either reuse the results or recreate the file.

**--qadjust-mode**: Determines the mode for CRF adjusting. The options are 1 for SSIMU2-based magic number boosting, 2 for Butteraugli target score with two passes and 3 for CVVDP target quality.
Please note that mode 2 works only with SVT-AV1.
- Example: --qadjust-mode 1
- Default: 3

**--qadjust-reuse**: Use this parameter to reuse the existing qadjust JSON file. It will save time for example in case your final encode has crashed etc. and the encoding parameters or filtering remains the same.
Note that the script will also ask you if you wish to reuse the JSON file in case it finds it while processing.
Also if you change the minimum chunk length from the value that was used for calculating the metrics, the script will not allow you to use this parameter.

**--qadjust-only**: Enables the mode which will only produce the qadjust JSON file and skip the final encode.

**--qadjust-b** and **--qadjust-c**: Define the 'b' and 'c' parameters for Bicubic resizing in case the final encode's resolution differs from the original one.
- Example: --qadjust-b -1.0 --qadjust-c 0.06
- Default: --qadjust-b -0.5, --qadjust-c 0.25

**--qadjust-skip**: Defines how many frames the calculation should skip to speed up the process. Setting skip to 1 means all frames will be used.
- Example: --qadjust-skip 4
- Default: 1 for CVVDP, for other metrics 1 for resolutions less than HD, 3 for HD and above.

**--qadjust-cpu**: Defines the '--preset' parameter used by the analysis for svt-av1
- Default: 7

**--qadjust-workers**: Defines how many parallel workers and threads (for Vship) will be launched. Avoid using too high values especially with UHD sources as GPU memory is often the limiting factor
- Example: --qadjust-workers 4,1
- Default: 1,1

**--qadjust-target**: Defines the target Butteraugli or CVVDP score. Note: if you already have run the analysis and the JSON file is found, you can use --qadjust-reuse and --qadjust-target to recalculate the final CRFs for chunks as needed.
- Example: --qadjust-target 9.8
- Default: None

**--qadjust-min-q** and **--qadjust-max-q**: Determines the range of allowed q for CVVDP based adjustment. This affects also the probing phase.
- Example: --qadjust-min-q 15.0 --qadjust-max-q 32.0
- Default: min 15.0, max 35.0

**--cvvdp-min-luma** and **cvvdp-max-luma**: Determines which range of average luma will have a damping effect when the CVVDP based adjustment raises the q of a chunk due to a better score than the target.
CVVDP tends to score a little too well with dark frames, and raising q will easily start removing details. These parameters damp the effect in order to prevent this from happening.
SDR material uses an exponential ramp and HDR a logarithmic one to determine the damping for chunks with average luma between set min and max.
Chunks with average luma below cvvdp-min-luma will not have their q raised even if they score better than your set target is.

- Example: --cvvdp-min-luma 0.00015 --cvvdp-max-luma 0.003
- Default: min 0.00035, max 0.0025 for both SDR and HDR

**--probes**: Defines how many probing encodes will be done for estimating the CVVDP score/q curve.
- Example: --probes 6
- Default: 8 if range between qadjust-min-q and qadjust-max-q is 20 points or more, 7 if range is 15-19 and 5 if less than 15.

**--cvvdp-model**: Defines the display model the CVVDP analysis uses. See https://codeberg.org/Line-fr/Vship/src/branch/main/doc/CVVDP.md for more information.
- Example: --cvvdp-model standard_4k
- Default: standard_4k for SDR, standard_hdr_pq for HDR

**--cvvdp-config**: Defines the full path to an override JSON file. See https://codeberg.org/Line-fr/Vship/src/branch/main/doc/CVVDP.md for more information and how to use the file.
Please note that presets.ini contains the key 'model_config_json' which you can use to autoload the file. The example file is one I use in my own encodes for my home theater setup.
**It is very important to set the parameters according to your own setup to get accurate CVVDP results!**

**encode_script**: Give the path (full or relative to the path where you run the script) to the Avisynth script you want to use for encoding.
The script enables the "resizeToDisplay" parameter so Vship will upscale both the source and encoded file to match the display resolution from the model/config file.


---


## Output

The script will create the following folders in the specified base_working_folder:

- **output**: Contains the final concatenated video file or the sample clip and the log files.
- **scripts**: Stores Avisynth scripts for each scene.
- **chunks**: Stores encoded video chunks.

The final concatenated video file will be named based on the input Avisynth script and saved in the output folder.


---


## Example

Here's an example of how to use Chunk Norris:

```bash
python chunk_norris.py my_video.avs --preset "720p" --q 18 --max-parallel-encodes 4 --scd-method 1
```

This command will use svt-av1 to encode the video specified in my_video.avs using the '720p' preset, a quality level of 18, and a maximum of 4 parallel encoding processes. It uses av-scenechange for scene change detection.
