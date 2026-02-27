import customtkinter as ctk
import threading
import os
import sys
from tkinter import messagebox
from git_manager import GitManager
from process_runner import ProcessRunner

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

            # 3. Run install.bat if needed
            if self.needs_install:
                self.update_status("Running installation...")
                self.append_log("Running install.bat...\n")
                
                # Ensure we pass the absolute path to the bat file
                bat_path = os.path.join(self.target_dir, "install.bat")
                install_code = self.run_bat_sync(bat_path)
                
                if install_code != 0:
                    self.update_status("Installation failed.")
                    self.append_log(f"install.bat exited with code {install_code}.\n")
                    self.after(0, self.progress_bar.stop)
                    self.after(0, self.show_logs_force)
                    self.after(0, lambda: self.action_btn.configure(state="normal", text="Retry"))
                    return # Stop sequence
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


