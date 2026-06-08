"""Prepare step: generate a small dataset."""

import csv
import pathlib

pathlib.Path("data").mkdir(exist_ok=True)
with open("data/raw.csv", "w", newline="") as fh:
    writer = csv.writer(fh)
    writer.writerow(["x", "y"])
    for i in range(10):
        writer.writerow([i, 2 * i + 1])
print("Prepared data/raw.csv")
