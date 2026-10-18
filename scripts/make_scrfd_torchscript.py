"""Make the face detector's weights (tagpup.ml.scrfd_adaface): insightface's SCRFD 10g (buffalo_l's det_10g.onnx)
turned into a torch module (onnx2torch) and traced once for each square it is run at, each saved as a TorchScript file.

    <a Python with torch, onnx and onnx2torch> scripts/make_scrfd_torchscript.py <det_10g.onnx> 640 <file> 1280 <file>

The converted module cannot be traced for every size at once, so each square has a file of its own; the app loads
them with torch alone (DECISIONS 2026-10-18: no second runtime in the project), and onnx and onnx2torch are needed
here only, so they are not in requirements.txt. A sandbox venv that sees the main one's torch will do; run it with -I. The files and the folder they go in are named by the sentence the app
gives when they are missing (scrfd_adaface.missing_weights). Runs on the processor; writes only the files it is given,
each after checking the traced module answers as the converted one does.
"""
import argparse
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("onnx", help="insightface's det_10g.onnx")
    parser.add_argument("squares", nargs="+", help="pairs of a square's side and the file it is saved as")
    args = parser.parse_args(argv)
    if len(args.squares) % 2 or not all(side.isdigit() for side in args.squares[::2]):
        parser.error("give each square as its side and its file: 640 <file> 1280 <file>")
    import onnx
    import torch
    from onnx2torch import convert

    # The model loaded, not its path: given a path, onnx2torch reopens a temporary file by name, which Windows refuses
    # while it is open.
    model = convert(onnx.load(args.onnx)).eval()
    for side, path in zip(map(int, args.squares[::2]), args.squares[1::2]):
        example = torch.rand(1, 3, side, side) * 2 - 1
        with torch.no_grad():
            traced = torch.jit.trace(model, torch.zeros(1, 3, side, side), check_trace=False)
            gap = max(float((a - b).abs().max()) for a, b in zip(model(example), traced(example)))
        if gap > 1e-4:
            print("The traced detector at %d answers apart from the converted one (%.2g): nothing saved." % (side, gap))
            return 1
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # Under another name until it is whole: a run stopped part-way leaves no file the app would take for the weights.
        traced.save(path + ".part")
        os.replace(path + ".part", path)
        print("Saved the detector at %d x %d: %s" % (side, side, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
