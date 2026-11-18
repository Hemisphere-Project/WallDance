import json, re, sys
d = json.load(open("final.json"))
def wc(s): return len(s.split())
def wc2(s): return len([x for x in s.split() if re.search(r"[A-Za-z0-9]", x)])
print("verdict", wc(d["verdict"]), "(<=200)")
print("since_october", wc(d["since_october"]), "(<=150)")
print("details", wc(d["details"]), wc2(d["details"]), "(<=600)")
print("rethink", wc(d["rethink"]), "(<=250)")
for i, f in enumerate(d["findings"], 1):
    print(i, "evidence", wc(f["evidence"]), "(<=90)  rec", wc(f["recommendation"]), "(<=60)  |", f["severity"], f["confidence"], f["status"], f["prior_id"], f["effort"])
print("questions", len(d["questions_for_owner"]))
