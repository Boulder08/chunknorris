# chunknorris
A very simple PoC-like Python script to do chunked, parallel encoding using the aomenc CLI encoder.

Requirements: Python 3.10.x (possibly just 3.x), Avisynth, avs2yuv64, ffmpeg, aomenc (the lavish mod recommended).
Optionally scene change list in x264/x265 QP file format, if you don't want to use ffmpeg for scene change detection.
Make sure you have all the tools in PATH or where you run the script.

Set common parameters in default_params and add/edit the presets as needed.
Set base_working_folder and scene_change_file_path according to your folder structure.


# Process

1. The script creates a folder structure based on the AVS script name under the set base working folder, removing the existing folders with same name first.
   
2. It searches for the QP file in the specified folder or its subfolders, or if --ffmpeg-scd is set, uses ffmpeg to scan for scene changes.
 
3. The chunks to encode are created based on the scene changes. If a chunk (scene) length is less than the specified minimum,
   it will combine it with the next one and so on until the minimum length is met. The last scene can be shorter.
   The encoder parameters are picked up from the default parameters + selected preset.

4. The encoding queue is ordered from longest to shortest chunk. This ensures that there will not be any single long encodes running at the end.
   The last scene is encoded in the first batch of chunks since we don't know its length based on the scene changes.

5. You can control the amount of parallel encodes by a CLI parameter. Tune the amount according to your system, both CPU and memory-wise.
   
6. The encoded chunks are concatenated in their original order to a Matroska container using ffmpeg.



# Usage

chunk_norris.py [-h] [--preset [PRESET]] [--q [Q]] [--min-chunk-length [MIN_CHUNK_LENGTH]]
                       [--max-parallel-encodes [MAX_PARALLEL_ENCODES]] [--threads [THREADS]]
                       [--noiselevel [NOISELEVEL]] [--graintable [GRAINTABLE]] [--ffmpeg-scd [FFMPEG_SCD]]
                       [--scdthresh [SCDTHRESH]] [--downscale-scd]
                       encode_script

                       
# Parameters

--preset: Choose a preset defined in the script. You can add your own and change the existing ones.
- Example: --preset "720p"
- Default: "1080p"

--q: Defines a Q value the encoder will use. It does a one-pass encode in Q mode, which is the closest to constant quality with a single pass.
- Example: --q 16
- Default: 14

--min-chunk-length: Defines the minimum encoded chunk length in frames. If there are detected scenes shorter than this amount, the script combines adjacent scenes until the condition is satisfied.
- Example: --min-chunk-length 100
- Default: 64 (the same as --lag-in-frames in the default parameters)

--max-parallel-encodes: Defines how many simultaneous encodes may run. Choose carefully, and try to avoid saturating your CPU or exhausting all your memory.
- Example: --max-parallel-encodes 8
- Default: 10

--threads: Defines the amount of threads each encoder may utilize. Keep it at least at 2 to allow threaded lookahead.
- Example: --threads 4
- Default: 8

--noiselevel: Defines the strength of the internal Film Grain Synthesis. Disabled if a grain table is used.
- Example: --noiselevel 20
- Default: 0

--graintable: Defines a (full) path to a Film Grain Synthesis grain table file, which you can get by using grav1synth.
- Example: --graintable C:\Temp\grain.tbl
- Default: None

--ffmpeg-scd: Defines the method for scene change detection.
- --ffmpeg-scd 0 uses a QP file style list of scene changes. It attempts to find the file from the path where the encoding script is, searching also in subfolders if needed.
- --ffmpeg-scd 1 uses ffmpeg for detection, and uses a separate Avisynth script. If it finds one from the encoding script path with the same name as the encoding script with '_scd' added at the end, it uses that.
  Otherwise a new file will be created based on the encoding script, loading only the source. Please make sure the source is loaded in the first line of the encoding script!
- --ffmpeg-scd 2 uses ffmpeg for detection, and uses the encoding script to do it.
- Example: --ffmpeg-scd 1
- Default: 0

--scdthresh: Defines the threshold for scene change detection in ffmpeg. Lower values mean more scene changes detected, but also more false detections.
- Example: --ffmpeg-scd 0.4
- Default: 0.3

--downscale-scd: Set this parameter to enable downscaling using ReduceBy2() in the scene change detection script (if --ffmpeg-scd is 1).
- Example: --downscale-scd
- Default: None

encode_script: Give the path (full or relative to the path where you run the script) to the Avisynth script you want to use for encoding.



# Example
python chunk_norris.py --max-parallel-encodes 8 --ffmpeg-scd 1 --scdthresh 0.275 --downscale-scd --q 15 withnail.avs

