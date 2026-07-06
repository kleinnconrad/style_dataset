import os
import json
import re
from datetime import datetime
from collections import defaultdict

def generate_overview():
    data_dir = "data"
    readme_path = "README.md"
    
    if not os.path.exists(data_dir):
        print(f"Directory '{data_dir}' not found.")
        return

    files = [f for f in os.listdir(data_dir) if f.endswith(".json")]
    total_files = len(files)
    
    total_records = 0
    field_stats = defaultdict(lambda: {"filled": 0, "distinct_values": set()})
    all_keys = set()
    
    for filename in files:
        filepath = os.path.join(data_dir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not isinstance(data, list):
                    continue
                
                total_records += len(data)
                
                for record in data:
                    for key, value in record.items():
                        all_keys.add(key)
                        
                        # Determine if field is considered "filled"
                        is_filled = False
                        if value is not None:
                            if isinstance(value, list) and len(value) > 0:
                                is_filled = True
                                for item in value:
                                    field_stats[key]["distinct_values"].add(str(item))
                            elif not isinstance(value, list) and value != "":
                                is_filled = True
                                field_stats[key]["distinct_values"].add(str(value))
                        
                        if is_filled:
                            field_stats[key]["filled"] += 1
                            
            except json.JSONDecodeError:
                print(f"Error decoding JSON from {filename}")

    # Generate Markdown Table
    md_lines = []
    md_lines.append(f"**Last Updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    md_lines.append("")
    md_lines.append(f"- **Total Days/Files:** {total_files}")
    md_lines.append(f"- **Total Outfits:** {total_records}")
    md_lines.append("")
    md_lines.append("| Variable | Fill Rate | Distinct Values |")
    md_lines.append("|----------|-----------|-----------------|")
    
    for key in sorted(list(all_keys)):
        filled_count = field_stats[key]["filled"]
        fill_rate = (filled_count / total_records * 100) if total_records > 0 else 0
        distinct_count = len(field_stats[key]["distinct_values"])
        md_lines.append(f"| `{key}` | {fill_rate:.1f}% ({filled_count}) | {distinct_count} |")
        
    overview_text = "\n".join(md_lines) + "\n"
    
    # Update README.md
    if os.path.exists(readme_path):
        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        pattern = r"(<!-- DATASET_OVERVIEW_START -->).*?(<!-- DATASET_OVERVIEW_END -->)"
        replacement = r"\1\n" + overview_text + r"\2"
        
        new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        
        if new_content != content:
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("README.md has been updated with the dataset overview.")
        else:
            print("No changes made to README.md or marker tags not found.")
    else:
        print(f"File '{readme_path}' not found.")

if __name__ == "__main__":
    generate_overview()
