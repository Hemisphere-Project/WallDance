import sys, time
sys.stdout.reconfigure(line_buffering=True)
try:
    for i in range(50):
        print(f"[Budget] tick {i}")
        time.sleep(0.1)
    open("pipe_child_result.txt","w").write("survived\n")
except BaseException as e:
    open("pipe_child_result.txt","w").write(f"child died: {type(e).__name__}: {e}\n")
    raise
