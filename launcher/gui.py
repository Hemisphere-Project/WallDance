import customtkinter as ctk
import threading
import re
import os
import sys
from tkinter import messagebox
from git_manager import GitManager
from process_runner import ProcessRunner

# Patterns to detect in install.bat output
INSTALL_PATTERNS = {
    "python_missing": {
        "pattern": r"ERROR:.*Python 3 is missing",
        "title": "Python 3 Required",
        "message": (
            "Python 3.10-3.12 is required but was not found on this system.\n\n"
            "Please install Python from:\n"
            "  https://www.python.org/downloads/windows/\n\n"
            "Or run:  winget install Python.Python.3.12\n\n"
            "After installing Python, click 'Retry' to resume installation."
        ),
        "severity": "critical",
    },
    "uv_missing": {
        "pattern": r"ERROR:.*uv is missing",
        "title": "Package Manager (uv) Required",
        "message": (
            "The 'uv' package manager is required but could not be installed.\n\n"
            "Please install it from:\n"
            "  https://docs.astral.sh/uv/getting-started/installation/\n\n"
            "Or run:  winget install astral-sh.uv\n\n"
            "After installing uv, click 'Retry' to resume installation."
        ),
        "severity": "critical",
    },
    "dep_failed": {
        "pattern": r"ERROR:.*Dependency installation failed",
        "title": "Dependency Installation Failed",
        "message": (
            "Some dependencies could not be installed.\n\n"
            "This may be caused by network issues or missing system libraries.\n\n"
            "Check the logs for details, then click 'Retry'."
        ),
        "severity": "critical",
    },
    "no_gpu": {
        "pattern": r"No NVIDIA GPU detected",
        "title": "No NVIDIA GPU Detected",
        "message": (
            "No NVIDIA GPU was found on this machine.\n\n"
            "WallDance will run in CPU-only mode (lower FPS).\n"
            "This is fine for testing or machines without a dedicated GPU.\n\n"
            "Continue with CPU-only installation?"
        ),
        "severity": "info",
    },
    "cuda_unavailable": {
        "pattern": r"WARNING:.*CUDA not available to PyTorch",
        "title": "CUDA Not Available",
        "message": (
            "An NVIDIA GPU was detected, but PyTorch cannot use CUDA.\n\n"
            "The installer will attempt to fix this automatically.\n"
            "If this persists, ensure NVIDIA CUDA drivers are installed:\n"
            "  https://developer.nvidia.com/cuda-downloads\n\n"
            "The app will fall back to CPU mode if CUDA cannot be enabled."
        ),
        "severity": "warning",
    },
    "cuda_fixed": {
        "pattern": r"OK:.*Automatic PyTorch upgrade succeeded",
        "title": "CUDA Fixed",
        "message": "CUDA support was automatically restored.",
        "severity": "success",
    },
    "cuda_still_unavailable": {
        "pattern": r"WARNING:.*CUDA still not available after auto-fix",
        "title": "CUDA Unavailable (Auto-Fix Failed)",
        "message": (
            "The automatic CUDA fix did not work.\n\n"
            "WallDance will run in CPU-only mode (lower FPS but functional).\n\n"
            "To enable GPU acceleration later:\n"
            "1. Install NVIDIA CUDA drivers\n"
            "2. Run install.bat again from the WallDance folder\n\n"
            "Continue in CPU mode?"
        ),
        "severity": "choice",
    },
    "arch_mismatch": {
        "pattern": r"WARNING:.*GPU architecture mismatch",
        "title": "GPU Architecture Mismatch",
        "message": (
            "Your GPU requires a different PyTorch/CUDA build.\n\n"
            "The installer will attempt to find a compatible version automatically.\n"
            "If this fails, WallDance will fall back to CPU mode."
        ),
        "severity": "warning",
    },
}


class LauncherGUI(ctk.CTk):
    def __init__(self, repo_url, target_dir):
        super().__init__()

        self.repo_url = repo_url
        self.target_dir = target_dir
        self.git_manager = GitManager(repo_url, target_dir)
        self.process_runner = ProcessRunner(target_dir)

        self.title("WallDance Launcher")
        self.geometry("600x400")
        self.resizable(False, False)

        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Status Label
        self.status_label = ctk.CTkLabel(self, text="Initializing...", font=("Arial", 16, "bold"))
        self.status_label.grid(row=0, column=0, pady=(20, 10), padx=20, sticky="ew")

        # Progress Bar
        self.progress_bar = ctk.CTkProgressBar(self, mode="indeterminate")
        self.progress_bar.grid(row=1, column=0, pady=10, padx=40, sticky="ew")
        self.progress_bar.start()

        # Log Text Box (Visible by default)
        self.log_box = ctk.CTkTextbox(self, state="disabled", font=("Consolas", 12))
        self.log_box.grid(row=2, column=0, pady=10, padx=20, sticky="nsew")

        # Buttons Frame
        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.button_frame.grid(row=3, column=0, pady=20, padx=20, sticky="ew")
        self.button_frame.grid_columnconfigure((0, 1), weight=1)

        self.toggle_logs_btn = ctk.CTkButton(self.button_frame, text="Hide Logs", command=self.toggle_logs)
        self.toggle_logs_btn.grid(row=0, column=0, padx=10)

        self.action_btn = ctk.CTkButton(self.button_frame, text="Start", command=self.start_sequence, state="disabled")
        self.action_btn.grid(row=0, column=1, padx=10)

        self.logs_visible = True
        self.geometry("600x500") # Start with expanded window
        self.needs_install = False
        
        # Install monitoring state
        self.install_log_lines = []
        self.install_alerts_shown = set()

        # Start the sequence automatically after a short delay
        self.after(500, self.start_sequence)

    def update_status(self, text):
        self.after(0, lambda: self.status_label.configure(text=text))

    def append_log(self, text):
        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _append)

    def toggle_logs(self):
        if self.logs_visible:
            self.log_box.grid_remove()
            self.toggle_logs_btn.configure(text="Show Logs")
            self.geometry("600x200")
        else:
            self.log_box.grid()
            self.toggle_logs_btn.configure(text="Hide Logs")
            self.geometry("600x500")
        self.logs_visible = not self.logs_visible

    def show_logs_force(self):
        if not self.logs_visible:
            self.toggle_logs()

    def start_sequence(self):
        self.action_btn.configure(state="disabled")
        threading.Thread(target=self._sequence_thread, daemon=True).start()

    def _sequence_thread(self):
        try:
            # 1. Check if cloned
            if not self.git_manager.is_cloned():
                self.update_status("First run: Cloning repository...")
                self.append_log("Cloning repository from GitHub...\n")
                self.git_manager.clone()
                self.append_log("Clone complete.\n")
                self.needs_install = True
                
                # Create desktop shortcut immediately after clone
                # so user can resume later if install is interrupted
                self._ensure_desktop_shortcut()
            else:
                # 2. Check for updates
                self.update_status("Checking for updates...")
                self.append_log("Checking for updates...\n")
                
                has_updates = False
                try:
                    has_updates = self.git_manager.check_updates()
                except Exception as e:
                    self.append_log(f"Failed to check updates (offline?): {e}\n")

                if has_updates:
                    self.append_log("Update available.\n")
                    # Ask user in main thread safely
                    update_choice = self.ask_update_sync()
                    if update_choice:
                        if self.git_manager.has_local_changes():
                            self.show_warning_sync("Conflict", "You have local changes that might conflict. Skipping update to prevent data loss.")
                            self.append_log("Skipped update due to local changes.\n")
                        else:
                            self.update_status("Updating repository...")
                            self.append_log("Pulling latest changes...\n")
                            try:
                                self.needs_install = self.git_manager.pull()
                                self.append_log("Update complete.\n")
                            except Exception as e:
                                self.append_log(f"Failed to pull updates: {e}\n")
                                self.show_error_sync("Update Failed", f"Failed to update: {e}")
                else:
                    self.append_log("No updates available.\n")

            # 3. Run install.bat if needed (with interactive monitoring)
            if self.needs_install:
                install_ok = self._run_install_interactive()
                if not install_ok:
                    return  # Stop sequence — user chose to cancel/retry later
                self.needs_install = False

            # 4. Run run.bat
            self.update_status("WallDance is running...")
            self.append_log("Running run.bat...\n")
            self.after(0, self.progress_bar.stop)
            
            def hide_if_no_logs():
                if not self.logs_visible:
                    self.withdraw()
            self.after(2000, hide_if_no_logs)

            bat_path = os.path.join(self.target_dir, "run.bat")
            return_code = self.run_bat_sync(bat_path)

            if return_code == 0:
                self.append_log("WallDance finished successfully.\n")
                self.after(0, self.destroy) # Exit launcher
            else:
                self.update_status("WallDance closed.")
                self.append_log(f"run.bat exited with code {return_code}.\n")
                self.after(0, self.deiconify) # Show window again if hidden
                self.after(0, self.show_logs_force)
                self.after(0, lambda: self.action_btn.configure(state="normal", text="Restart"))

        except Exception as e:
            self.update_status("An error occurred.")
            self.append_log(f"Unexpected error: {e}\n")
            self.after(0, self.progress_bar.stop)
            self.after(0, self.show_logs_force)
            self.after(0, lambda: self.action_btn.configure(state="normal", text="Retry"))

    def _ensure_desktop_shortcut(self):
        """Create desktop shortcut pointing to the launcher exe (frozen only)."""
        try:
            if getattr(sys, 'frozen', False):
                import install_manager
                install_manager.create_desktop_shortcut(sys.executable)
                self.append_log("Desktop shortcut created.\n")
        except Exception as e:
            self.append_log(f"Could not create desktop shortcut: {e}\n")

    def _run_install_interactive(self):
        """
        Run install.bat with real-time output monitoring.
        Detects issues and shows interactive dialogs.
        Returns True if install succeeded (or user chose to continue),
        False if user chose to cancel/retry later.
        """
        self.update_status("Running installation...")
        self.append_log("Running install.bat...\n")
        self.install_log_lines = []
        self.install_alerts_shown = set()
        
        bat_path = os.path.join(self.target_dir, "install.bat")
        
        # Use monitored version that checks patterns on each line
        install_code = self.run_bat_monitored(bat_path)
        
        if install_code == 0:
            # Success — but check if there were warnings we should summarize
            full_log = "\n".join(self.install_log_lines)
            
            # Check for CPU-only mode (no GPU or CUDA fix failed)
            if re.search(r"CUDA still not available|No NVIDIA GPU detected", full_log):
                if "cuda_still_unavailable" not in self.install_alerts_shown:
                    choice = self.ask_choice_sync(
                        "CPU Mode",
                        "Installation completed, but GPU/CUDA is not available.\n\n"
                        "WallDance will run in CPU-only mode (lower FPS but functional).\n\n"
                        "You can fix this later by installing NVIDIA CUDA drivers\n"
                        "and running install.bat again from the WallDance folder.\n\n"
                        "Continue in CPU mode?",
                        yes_text="Continue",
                        no_text="Exit (fix later)"
                    )
                    if not choice:
                        self.update_status("Installation paused. Fix dependencies and re-launch.")
                        self.append_log("User chose to exit and fix dependencies.\n")
                        self.after(0, self.progress_bar.stop)
                        self.after(0, lambda: self.action_btn.configure(state="normal", text="Retry"))
                        return False
            
            self.append_log("Installation complete.\n")
            return True
        else:
            # Failure — analyze why and show appropriate dialog
            full_log = "\n".join(self.install_log_lines)
            
            # Determine specific error
            if re.search(INSTALL_PATTERNS["python_missing"]["pattern"], full_log):
                info = INSTALL_PATTERNS["python_missing"]
            elif re.search(INSTALL_PATTERNS["uv_missing"]["pattern"], full_log):
                info = INSTALL_PATTERNS["uv_missing"]
            elif re.search(INSTALL_PATTERNS["dep_failed"]["pattern"], full_log):
                info = INSTALL_PATTERNS["dep_failed"]
            else:
                info = {
                    "title": "Installation Failed",
                    "message": f"install.bat exited with error code {install_code}.\n\nCheck the logs for details, then click 'Retry'.",
                }
            
            choice = self.ask_choice_sync(
                info["title"],
                info["message"],
                yes_text="Retry",
                no_text="Exit (fix later)"
            )
            
            if choice:
                # User wants to retry
                self.append_log("Retrying installation...\n")
                return self._run_install_interactive()  # Recursive retry
            else:
                self.update_status("Installation paused. Fix dependencies and re-launch.")
                self.append_log("User chose to exit and fix dependencies.\n")
                self.after(0, self.progress_bar.stop)
                self.after(0, lambda: self.action_btn.configure(state="normal", text="Retry"))
                return False

    def run_bat_monitored(self, bat_file):
        """Run a bat file while monitoring output for known patterns."""
        event = threading.Event()
        return_code = [-1]

        def on_output(line):
            self.install_log_lines.append(line.rstrip())
            self.after(0, self.append_log, line)
            
            # Check for informational patterns while install is running
            for key, info in INSTALL_PATTERNS.items():
                if key in self.install_alerts_shown:
                    continue
                if re.search(info["pattern"], line):
                    self.install_alerts_shown.add(key)
                    severity = info.get("severity", "info")
                    
                    if severity == "info":
                        # Non-blocking info: just show as info dialog
                        self.show_info_sync(info["title"], info["message"])
                    elif severity == "warning":
                        # Show warning but let install continue
                        self.show_warning_sync(info["title"], info["message"])
                    elif severity == "success":
                        self.show_info_sync(info["title"], info["message"])
                    # critical and choice are handled after install completes

        def on_done(code):
            return_code[0] = code
            event.set()

        self.process_runner.run_bat(bat_file, on_output, on_done)
        event.wait()
        return return_code[0]

    def run_bat_sync(self, bat_file):
        # We need to block the sequence thread until the bat file finishes
        event = threading.Event()
        return_code = [-1]

        def on_output(line):
            # Schedule UI update in main thread
            self.after(0, self.append_log, line)

        def on_done(code):
            return_code[0] = code
            event.set()

        self.process_runner.run_bat(bat_file, on_output, on_done)
        event.wait()
        return return_code[0]

    def ask_update_sync(self):
        result = [False]
        event = threading.Event()
        def prompt():
            result[0] = messagebox.askyesno("Update Available", "A new version of WallDance is available. Do you want to update?")
            event.set()
        self.after(0, prompt)
        event.wait()
        return result[0]

    def show_warning_sync(self, title, message):
        event = threading.Event()
        def prompt():
            messagebox.showwarning(title, message)
            event.set()
        self.after(0, prompt)
        event.wait()

    def show_error_sync(self, title, message):
        event = threading.Event()
        def prompt():
            messagebox.showerror(title, message)
            event.set()
        self.after(0, prompt)
        event.wait()

    def show_info_sync(self, title, message):
        event = threading.Event()
        def prompt():
            messagebox.showinfo(title, message)
            event.set()
        self.after(0, prompt)
        event.wait()

    def ask_choice_sync(self, title, message, yes_text="Yes", no_text="No"):
        """Show a yes/no dialog and return True if user chose yes."""
        result = [False]
        event = threading.Event()
        def prompt():
            result[0] = messagebox.askyesno(title, message)
            event.set()
        self.after(0, prompt)
        event.wait()
        return result[0]


