import customtkinter as ctk
import threading
import re
import os
import sys
from tkinter import messagebox
from git_manager import GitManager, UpdateStatus, DirtyWorkingTreeError
from process_runner import ProcessRunner

# Patterns to detect in install.bat output (only actionable ones)
INSTALL_ERROR_PATTERNS = {
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
    },
    "dep_failed": {
        "pattern": r"ERROR:.*Dependency installation failed",
        "title": "Dependency Installation Failed",
        "message": (
            "Some dependencies could not be installed.\n\n"
            "This may be caused by network issues or missing system libraries.\n\n"
            "Check the logs for details, then click 'Retry'."
        ),
    },
}

# Patterns that indicate CPU-only mode (not errors, just informational)
CPU_MODE_PATTERNS = [
    r"CUDA still not available after auto-fix",
    r"No NVIDIA GPU detected",
    r"CUDA not available to PyTorch.*Continuing in CPU mode",
]


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
                
                status = UpdateStatus.UNKNOWN
                try:
                    status = self.git_manager.check_updates()
                except Exception as e:
                    self.append_log(f"Failed to check updates (offline?): {e}\n")

                if status in (UpdateStatus.BEHIND, UpdateStatus.DIVERGED):
                    self.append_log("Update available.\n")
                    # Refuse before prompting if tracked files were edited locally
                    # (a force-sync would overwrite them). Fail safe: if the dirty
                    # check itself errors, treat the tree as dirty.
                    dirty = None
                    try:
                        dirty = self.git_manager.dirty_files()
                    except Exception as e:
                        self.append_log(f"Could not verify local changes: {e}\n")
                    if dirty is None or dirty:
                        self._warn_update_skipped_dirty(dirty)
                    else:
                        if status is UpdateStatus.BEHIND:
                            # Ask user in main thread safely
                            update_choice = self.ask_update_sync()
                        else:  # DIVERGED: updating discards local commits
                            update_choice = self.ask_choice_sync(
                                "Local Version Differs",
                                "The server version and this machine's version have both "
                                "changed. Updating will PERMANENTLY DISCARD the local "
                                "commits and sync to the server version.\n\n"
                                "Untracked working data (models/, projects/, recordings) "
                                "is not affected.\n\nDiscard local commits and update?",
                                yes_text="Discard and Update",
                                no_text="Keep Local Version",
                            )
                        if update_choice:
                            self.update_status("Updating repository...")
                            self.append_log("Syncing to latest version...\n")
                            try:
                                self.needs_install = self.git_manager.update()
                                self.append_log("Update complete.\n")
                            except DirtyWorkingTreeError as e:
                                # Raced edit between the check and the sync
                                self._warn_update_skipped_dirty(e.files)
                            except Exception as e:
                                self.append_log(f"Failed to update: {e}\n")
                                self.show_error_sync("Update Failed", f"Failed to update: {e}")
                elif status is UpdateStatus.AHEAD:
                    self.append_log("Local version is ahead of the server - skipping update.\n")
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
        
        full_log = "\n".join(self.install_log_lines)
        
        if install_code == 0:
            # Success — check if we ended up in CPU-only mode
            is_cpu_mode = any(re.search(p, full_log) for p in CPU_MODE_PATTERNS)
            
            if is_cpu_mode:
                choice = self.ask_choice_sync(
                    "CPU Mode",
                    "Installation completed successfully, but GPU/CUDA is not available.\n\n"
                    "WallDance will run in CPU-only mode (lower FPS but functional).\n\n"
                    "To enable GPU later: install NVIDIA CUDA drivers,\n"
                    "then re-launch WallDance (install will re-run automatically).\n\n"
                    "Continue in CPU mode?",
                    yes_text="Continue",
                    no_text="Exit (fix later)"
                )
                if not choice:
                    self.update_status("Install done. Fix CUDA/GPU and re-launch.")
                    self.append_log("User chose to exit and fix GPU/CUDA.\n")
                    self.after(0, self.progress_bar.stop)
                    self.after(0, lambda: self.action_btn.configure(state="normal", text="Retry"))
                    return False
            
            self.append_log("Installation complete.\n")
            return True
        else:
            # Failure — find the most specific error message
            info = None
            for key, pat_info in INSTALL_ERROR_PATTERNS.items():
                if re.search(pat_info["pattern"], full_log):
                    info = pat_info
                    break
            
            if not info:
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
                self.append_log("Retrying installation...\n")
                return self._run_install_interactive()  # Recursive retry
            else:
                self.update_status("Installation paused. Fix dependencies and re-launch.")
                self.append_log("User chose to exit and fix dependencies.\n")
                self.after(0, self.progress_bar.stop)
                self.after(0, lambda: self.action_btn.configure(state="normal", text="Retry"))
                return False

    def run_bat_monitored(self, bat_file):
        """Run a bat file, collecting output lines for post-analysis."""
        event = threading.Event()
        return_code = [-1]

        def on_output(line):
            self.install_log_lines.append(line.rstrip())
            self.after(0, self.append_log, line)

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

    def _warn_update_skipped_dirty(self, dirty):
        """Tell the user the update was refused because tracked files were edited."""
        if dirty:
            self.append_log("Update skipped: local changes to tracked files:\n")
            for f in dirty:
                self.append_log(f"  {f}\n")
            shown = "\n".join(dirty[:10])
            if len(dirty) > 10:
                shown += f"\n... and {len(dirty) - 10} more"
        else:
            self.append_log("Update skipped: could not verify local changes.\n")
            shown = "(could not list the files - see the log)"
        self.show_warning_sync(
            "Update Skipped - Local Changes",
            "An update is available but was NOT applied, because these files "
            "have local changes that an update would overwrite:\n\n"
            f"{shown}\n\n"
            "WallDance will start with the current version.\n\n"
            "To update later: back up your edits, restore the original files, "
            "then relaunch. Untracked working data (models/, projects/, "
            "recordings) is never affected by updates.",
        )

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


