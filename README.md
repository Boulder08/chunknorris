# chunknorris
A very simple PoC-like Python script to do chunked, parallel encoding using the aomenc CLI encoder.

Requirements: Python 3.10.x (possibly just 3.x), Avisynth, avs2yuv64, ffmpeg, aomenc (the lavish mod recommended).
Optionally scene change list in x264/x265 QP file format, if you don't want to use ffmpeg for scene change detection.
Make sure you have ffmpeg and the encoder in PATH or where you run the script.

Set common parameters in default_params and add/edit the presets as needed.
Set base_working_folder and scene_change_file_path according to your folder structure.

usage: chunk_norris.py [-h] [--preset [PRESET]] [--q [Q]] [--min-chunk-length [MIN_CHUNK_LENGTH]]
                       [--max-parallel-encodes [MAX_PARALLEL_ENCODES]] [--threads [THREADS]]
                       [--noiselevel [NOISELEVEL]] [--graintable [GRAINTABLE]] [--ffmpeg-scd]
                       [--scdthresh [SCDTHRESH]]
                       encode_script
                       
For example: python chunk_norris.py --preset 720p --q 14 --max-parallel-encodes 6 --graintable c:\temp\grain.tbl



1. The script creates a folder structure based on the AVS script name under the set base working folder, removing the existing folders with same name first.
   
2. It searches for the QP file in the specified folder or its subfolders, or if --ffmpeg-scd is set, uses ffmpeg to scan for scene changes.
 
3. The chunks to encode are created based on the QP file. If a chunk (scene) length is less than the specified minimum,
   it will combine it with the next one and so on until the minimum length is met. The last scene can be shorter.
   The encoder parameters are picked up from the default parameters + selected preset.

4. The encoding queue is ordered from longest to shortest chunk. This ensures that there will not be any single long encodes running at the end.
   The last scene is encoded in the first batch of chunks since we don't know its length based on the QP file.

5. You can control the amount of parallel encodes by a CLI parameter. Tune the amount according to your system, both CPU and memory-wise.
   
6. The encoded chunks are concatenated in their original order to a Matroska container using ffmpeg.
