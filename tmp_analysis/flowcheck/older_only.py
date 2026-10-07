#!/usr/bin/env python3
"""Print the older-shots regression rows (report.py's table) as text."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report  # noqa: E402

tbl, worse, n = report.older_table(Path(sys.argv[1]))
print(f"{n} runs compared; worse cells: {len(worse)}")
for w in worse:
    print("  WORSE", w)
import re  # noqa: E402
for row in re.findall(r"<tr>(.*?)</tr>", tbl)[1:]:
    print(" | ".join(re.sub(r"<[^>]+>", "", c).replace("&rarr;", "->") for c in re.findall(r"<td[^>]*>(.*?)</td>", row)))
