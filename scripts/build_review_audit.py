"""Build the local human-review workspace from an existing completed report."""

import argparse
from pathlib import Path

from debate_asb.end_to_end.audit import render_audit

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()
print(render_audit(args.directory.resolve()))
