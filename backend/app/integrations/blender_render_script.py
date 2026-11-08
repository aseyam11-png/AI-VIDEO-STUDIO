"""Run by Blender in background mode; arguments are supplied after --."""
import argparse
import sys

import bpy


def main() -> None:
    raw_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--frame-start", type=int, required=True)
    parser.add_argument("--frame-end", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    args = parser.parse_args(raw_args)

    scene = bpy.context.scene
    scene.frame_start = args.frame_start
    scene.frame_end = args.frame_end
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.fps = args.fps
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.filepath = args.output
    bpy.ops.render.render(animation=True)


if __name__ == "__main__":
    main()
