import subprocess
import threading
import os

class ProcessRunner:
    def __init__(self, working_dir):
        self.working_dir = working_dir
        self.process = None

    def run_bat(self, bat_file, output_callback, done_callback):
        """
        Runs a .bat file in a separate thread, capturing stdout and stderr.
        output_callback(str) is called for each line of output.
        done_callback(int) is called with the return code when finished.
        """
        def target():
            try:
                # CREATE_NO_WINDOW flag prevents the console window from appearing on Windows
                creationflags = 0
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW

                # Ensure we use the absolute path and wrap it in quotes if needed
                # shell=True is required on Windows to execute .bat files properly
                # without explicitly calling cmd.exe /c
                self.process = subprocess.Popen(
                    f'"{bat_file}"',
                    cwd=self.working_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=creationflags,
                    shell=True,
                    encoding='utf-8',
                    errors='replace'
                )

                for line in iter(self.process.stdout.readline, ''):
                    if line:
                        output_callback(line)

                self.process.stdout.close()
                return_code = self.process.wait()
                done_callback(return_code)
            except Exception as e:
                output_callback(f"Error running {bat_file}: {e}\n")
                done_callback(-1)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()

    def terminate(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
