import subprocess, sys, time, os
p = subprocess.Popen([sys.executable, "pipe_child.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
for _ in range(3):
    print("launcher got:", p.stdout.readline().strip())
os._exit(0)   # launcher window closed: reader gone, child keeps running
