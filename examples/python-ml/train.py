"""Train step: fit a trivial linear model and store the slope."""

import csv
import json
import pathlib

xs, ys = [], []
with open("data/raw.csv") as fh:
    for row in csv.DictReader(fh):
        xs.append(float(row["x"]))
        ys.append(float(row["y"]))

n = len(xs)
slope = (n * sum(x * y for x, y in zip(xs, ys)) - sum(xs) * sum(ys)) / (
    n * sum(x * x for x in xs) - sum(xs) ** 2
)
pathlib.Path("results").mkdir(exist_ok=True)
with open("results/model.json", "w") as fh:
    json.dump({"slope": round(slope, 4)}, fh)
print("Trained model -> results/model.json")
