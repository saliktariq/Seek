import sys

with open("seek/ui/app_window.py", "r") as f:
    code = f.read()

if "label=\"Import links…\"," not in code:
    old_menu = """        self.file_menu.add_command(
            label="General settings…",
            command=self._open_general_settings,
        )"""
        
    new_menu = """        self.file_menu.add_command(
            label="Import links…",
            command=self._import_links,
        )
        self.file_menu.add_command(
            label="Export links…",
            command=self._export_links,
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(
            label="General settings…",
            command=self._open_general_settings,
        )"""
        
    code = code.replace(old_menu, new_menu)
    
    export_import_code = """    def _import_links(self) -> None:
        file_path = filedialog.askopenfilename(
            parent=self.root,
            title="Import Links",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        current = self.url_input.get("1.0", "end-1c").strip()
                        if current:
                            current += "\\n"
                        self.url_input.delete("1.0", "end")
                        self.url_input.insert("1.0", current + content)
                        self._on_links_modified()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to import links:\\n{e}")

    def _export_links(self) -> None:
        file_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Links",
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if file_path:
            try:
                content = self.url_input.get("1.0", "end-1c").strip()
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                messagebox.showinfo("Success", "Links exported successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export links:\\n{e}")
"""
    code = code.replace("    def _open_general_settings", export_import_code + "\n    def _open_general_settings")

with open("seek/ui/app_window.py", "w") as f:
    f.write(code)

