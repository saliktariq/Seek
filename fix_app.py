with open("seek/ui/app_window.py", "r") as f:
    code = f.read()

code = code.replace(
    "                self.events.put,\n                cancel_event=self.cancel_event,",
    "                self.events.put,\n                cancel_event=self.cancel_event,\n                config=self.config,"
)

with open("seek/ui/app_window.py", "w") as f:
    f.write(code)
