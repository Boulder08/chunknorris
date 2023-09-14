# chunknorris
A very simple Python script to do chunked encoding using the aomenc CLI encoder.

Requirements: Python 3.10.x (possibly just 3.x), scene change list in x264/x265 QP file format, Avisynth, avs2yuv64, ffmpeg, aomenc (the lavish mod recommended)
Make sure you have ffmpeg and the encoder in PATH or where you run the script.

Set common parameters in default_params and add/edit the presets as needed.
Set base_working_folder and scene_change_file_path according to your folder structure.

Usage: python chunk_norris.py script.avs preset q min_chunk_length, for example:
python chunk_norris.py greatmovie.avs 720p 16 120

1. The script creates a folder structure based on the AVS script name under the set base working folder, removing the existing folders with same name first.
   
2. It searches for the QP file in the specified folder or its subfolders.
 
3. The chunks to encode are created based on the QP file. If a chunk (scene) length is less than the specified minimum,
   it will combine it with the next one and so on until the minimum length is met. The last scene can be shorter.
   The encoder parameters are picked up from the default parameters + selected preset.

4. The encoding queue is ordered from longest to shortest chunk. This ensures that there will not be any single long encodes running at the end.
   The last scene is encoded in the first batch of chunks since we don't know its length based on the QP file.
   
5. The encoded chunks are concatenated in their original order to a Matroska container using ffmpeg.
