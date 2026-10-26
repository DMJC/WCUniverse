import json
import re

def convert_numbers(obj):
    if isinstance(obj, dict):
        return {k: convert_numbers(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numbers(item) for item in obj]
    elif isinstance(obj, str):
        # Match int or float
        if re.fullmatch(r"-?\d+\.\d+", obj):
            return float(obj)
        elif re.fullmatch(r"-?\d+", obj):
            return int(obj)
    return obj

def fix_json_file(input_path, output_path):
    with open(input_path, 'r') as f:
        data = json.load(f)
    
    fixed_data = convert_numbers(data)

    with open(output_path, 'w') as f:
        json.dump(fixed_data, f, indent=4)

    print(f"Fixed JSON saved to {output_path}")

# Example usage
fix_json_file("weapons.json", "weapons_fixed.json")
