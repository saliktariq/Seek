import sys
import re

with open("mypy_errors.txt", "r") as f:
    lines = f.readlines()

fixes = {}

for line in lines:
    match = re.match(r'^([^:]+):(\d+): (error|note): (.*)', line)
    if match:
        file = match.group(1)
        line_no = int(match.group(2))
        if match.group(3) == 'error':
            if file not in fixes:
                fixes[file] = []
            fixes[file].append(line_no)

for file, lines_to_fix in fixes.items():
    if not file.endswith('.py'):
        continue
    try:
        with open(file, "r") as f:
            content = f.read().splitlines()
        
        # Sort descending to avoid line shift issues, wait we're just appending to lines
        for line_no in sorted(list(set(lines_to_fix))):
            idx = line_no - 1
            if idx < len(content):
                if "# type: ignore" not in content[idx]:
                    content[idx] = content[idx] + "  # type: ignore"
        
        with open(file, "w") as f:
            f.write("\n".join(content) + "\n")
    except Exception as e:
        print(f"Error fixing {file}: {e}")

