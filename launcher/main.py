import os
import sys
import shutil
import customtkinter as ctk
from tkinter import filedialog, messagebox
from gui import LauncherGUI
import install_manager

class SetupDialog(ctk.CTk):
    def __init__(self, default_path):
        super().__init__()
        self.title("WallDance Setup")
        self.geometry("550x200")
        self.resizable(False, False)
        self.install_path = None
        
        self.grid_columnconfigure(1, weight=1)
        
        self.lbl = ctk.CTkLabel(self, text="Welcome to WallDance!\n\n Select an installation folder:", font=("Arial", 14, "bold"))
        self.lbl.grid(row=0, column=0, columnspan=3, pady=(20, 10), padx=20, sticky="w")
        
        self.path_entry = ctk.CTkEntry(self, width=350)
        self.path_entry.insert(0, default_path)
        self.path_entry.grid(row=1, column=0, columnspan=2, pady=10, padx=(20, 10), sticky="ew")
        
        self.browse_btn = ctk.CTkButton(self, text="Browse...", width=80, command=self.browse)
        self.browse_btn.grid(row=1, column=2, pady=10, padx=(0, 20))
        
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=2, column=0, columnspan=3, pady=20)
        
        self.install_btn = ctk.CTkButton(self.btn_frame, text="Install", command=self.install)
        self.install_btn.pack(side="left", padx=10)
        
        self.cancel_btn = ctk.CTkButton(self.btn_frame, text="Cancel", command=self.cancel, fg_color="gray")
        self.cancel_btn.pack(side="left", padx=10)
        
        # Center window
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_width()) // 2
        y = (self.winfo_screenheight() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def browse(self):
        dir_path = filedialog.askdirectory(initialdir="C:\\")
        if dir_path:
            if dir_path == "C:\\" or dir_path == "C:/":
                dir_path = "C:\\WallDance"
            elif os.path.basename(dir_path).lower() != "walldance":
                dir_path = os.path.join(dir_path, "WallDance")
            
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, dir_path)

    def install(self):
        self.install_path = self.path_entry.get()
        self.destroy()

    def cancel(self):
        self.destroy()
        sys.exit(0)

def main():
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    
    is_frozen = getattr(sys, 'frozen', False)
    
    if is_frozen:
        # Running as compiled executable
        current_exe = sys.executable
        base_dir = os.path.dirname(current_exe)
        target_dir = os.path.join(base_dir, "WallDance")
        
        # Check if we need to show setup
        needs_setup = not os.path.exists(target_dir) and "--no-setup" not in sys.argv
        
        if needs_setup:
            setup = SetupDialog(default_path="C:\\WallDance")
            setup.mainloop()
            
            final_dir = setup.install_path
            if not final_dir:
                sys.exit(0)
                
            final_dir = os.path.abspath(final_dir)
            base_dir_abs = os.path.abspath(base_dir)
            
            if final_dir != base_dir_abs:
                try:
                    os.makedirs(final_dir, exist_ok=True)
                    
                    new_exe_path = os.path.join(final_dir, os.path.basename(current_exe))
                    shutil.copy2(current_exe, new_exe_path)
                    
                    install_manager.create_desktop_shortcut(new_exe_path)
                    
                    # Restart the new exe with --no-setup flag and delete the current one
                    install_manager.restart_and_delete(current_exe, new_exe_path, "--no-setup")
                    sys.exit(0)
                except Exception as e:
                    root = ctk.CTk()
                    root.withdraw()
                    messagebox.showerror("Error", f"Failed to install to {final_dir}:\n{e}")
                    sys.exit(1)
            else:
                # Running in the correct folder already
                install_manager.create_desktop_shortcut(current_exe)
    else:
        # Running as script
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
    target_dir = os.path.join(base_dir, "WallDance")
    repo_url = "https://github.com/Hemisphere-Project/WallDance.git"
    
    app = LauncherGUI(repo_url, target_dir)
    app.mainloop()

if __name__ == "__main__":
    main()
