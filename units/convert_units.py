#!/usr/bin/env python3
"""Convert units.csv to units.json for the current Vega Strike engine.

usage: python3 convert_units.py [units.csv] [units.json]

Unlike parser.py (which assumes upstream's column list), this reads the column
names from the CSV's own header row, so every value keeps its real key.

It also adapts the WCU data to how the engine now handles upgrades:

* Upgrade rows (<name>__upgrades) get an Upgrade_Type plus the new-style keys
  the components read when an upgrade is bought ("shield"/"shield_facets",
  "armor", "repair").
* A ship's "Upgrades" column is no longer applied when the ship is created, so
  its effect is baked into the ship's own stats, the way the old engine applied
  it at load time (plain upgrade = replace, add_ = add, mult_ = multiply,
  nested Upgrades lists are followed). Matching entries in the ship's Cargo are
  marked as installed so they sit in upgrade space rather than the cargo hold.
"""
import csv
import json
import re
import sys

src = sys.argv[1] if len(sys.argv) > 1 else 'units.csv'
dst = sys.argv[2] if len(sys.argv) > 2 else 'units.json'

SHIELD_FACETS = ['Shield_Front_Top_Right', 'Shield_Back_Top_Left',
                 'Shield_Front_Bottom_Right', 'Shield_Front_Bottom_Left']
ARMOR = ['Armor_Front_Top_Left', 'Armor_Front_Top_Right', 'Armor_Front_Bottom_Left',
         'Armor_Front_Bottom_Right', 'Armor_Back_Top_Left', 'Armor_Back_Top_Right',
         'Armor_Back_Bottom_Left', 'Armor_Back_Bottom_Right']
# Stats an upgrade row can change on a ship. Shield_Leak and Jump_Drive_Delay
# are left out: nearly every upgrade row carries a placeholder 1 in them.
NUMERIC_STATS = SHIELD_FACETS + ARMOR + [
    'Shield_Back_Top_Right', 'Shield_Front_Top_Left', 'Shield_Back_Bottom_Right',
    'Shield_Back_Bottom_Left', 'Shield_Recharge', 'Hull', 'Primary_Capacitor',
    'Reactor_Recharge', 'Warp_Capacitor', 'Fuel_Capacity', 'Radar_Range',
    'Tracking_Cone', 'Max_Cone', 'Lock_Cone', 'Radar_Color', 'Hold_Volume',
    'Repair_Droid', 'ECM_Rating', 'Afterburner_Usage_Cost', 'Afterburner_Accel',
    'Forward_Accel', 'Retro_Accel', 'Left_Accel', 'Right_Accel', 'Top_Accel',
    'Bottom_Accel', 'Maneuver_Yaw', 'Maneuver_Pitch', 'Maneuver_Roll',
    'Yaw_Governor', 'Pitch_Governor', 'Roll_Governor',
    'Afterburner_Speed_Governor', 'Default_Speed_Governor', 'Can_Cloak',
    'Cloak_Min', 'Cloak_Rate', 'Cloak_Energy']
BOOL_STATS = ['ITTS', 'Can_Lock', 'Jump_Drive_Present']

log = []


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt(value):
    return str(int(value)) if value == int(value) else repr(round(value, 6))


def upgrade_names(text):
    return [m.split(';')[0] for m in re.findall(r'\{([^}]*)\}', text or '') if m.split(';')[0]]


def upgrade_type(name, row):
    """Component type for an upgrade row, or None when no component fits."""
    if name.startswith(('add_', 'mult_')):
        return None  # additive/multiplicative modifiers have no component equivalent
    if any(num(row.get(c)) for c in ARMOR):
        return 'Armor'
    if any(num(row.get(c)) for c in SHIELD_FACETS) and num(row.get('Shield_Recharge')):
        return 'Shield'
    if num(row.get('Reactor_Recharge')):
        return 'Reactor'
    if num(row.get('Radar_Range')):
        return 'Radar'
    if num(row.get('Repair_Droid')):
        return 'Repair_Bot'
    if num(row.get('Can_Cloak')):
        return 'Cloak'
    if row.get('Jump_Drive_Present', '').upper() == 'TRUE':
        return 'Jump_Drive'
    if num(row.get('Hull')):
        return 'Hull'
    return None


with open(src, encoding='latin-1', newline='') as f:
    rows = list(csv.reader(f))
header = rows[0]
units = []
for r in rows[1:]:
    if not r or not r[0] or r[0] == 'Key':  # skip the type row
        continue
    units.append(dict(zip(header, r)))
by_key = {u['Key']: u for u in units}

# --- upgrade rows ------------------------------------------------------------
for key, row in by_key.items():
    if not key.endswith('__upgrades'):
        continue
    kind = upgrade_type(key[:-len('__upgrades')], row)
    if kind is None:
        continue
    row['Upgrade_Type'] = kind
    if kind == 'Shield':
        facets = [c for c in SHIELD_FACETS if num(row.get(c))]
        row['shield_facets'] = str(len(facets))
        row['shield'] = fmt(num(row[facets[0]]))
    elif kind == 'Armor':
        # Same 8-to-4 facet conversion the engine uses for old-style ship armor.
        v = [num(row.get(c)) for c in ARMOR]
        facets = [sum(v[i] for i in idx) / 2 for idx in
                  ((0, 1, 2, 3), (4, 5, 6, 7), (0, 2, 4, 6), (1, 3, 5, 7))]
        row['armor'] = fmt(sum(facets) / 4)
    elif kind == 'Repair_Bot':
        row['repair'] = row['Repair_Droid']


# --- bake each ship's Upgrades list into its stats ---------------------------
def apply_upgrade(unit, name, depth=0):
    upg = by_key.get(name + '__upgrades')
    if upg is None:
        log.append(f"{unit['Key']}: upgrade '{name}' has no {name}__upgrades row, skipped")
        return
    mode = 'add' if name.startswith('add_') else 'mult' if name.startswith('mult_') else 'set'
    for col in NUMERIC_STATS:
        up = upg.get(col, '').strip()
        if not up or num(up) == 0:
            continue
        cur = num(unit.get(col))
        new = cur + num(up) if mode == 'add' else cur * num(up) if mode == 'mult' else num(up)
        unit[col] = fmt(new)
    for col in BOOL_STATS:
        if upg.get(col, '').upper() == 'TRUE':
            unit[col] = 'TRUE'
    if upg.get('Cargo_Import'):
        unit['Cargo_Import'] = unit.get('Cargo_Import', '') + upg['Cargo_Import']
    if depth < 4:
        for nested in upgrade_names(upg.get('Upgrades')):
            apply_upgrade(unit, nested, depth + 1)


def mark_installed(cargo, names):
    def mark(m):
        fields = m.group(1).split(';')
        if fields[0] in names and len(fields) >= 10:
            fields = fields[:10] + ['true'] + fields[11:]
        return '{' + ';'.join(fields) + '}'
    return re.sub(r'\{([^}]*)\}', mark, cargo)


baked = 0
for unit in units:
    if unit['Key'].endswith('__upgrades'):
        continue
    names = upgrade_names(unit.get('Upgrades'))
    if not names:
        continue
    for name in names:
        apply_upgrade(unit, name)
    if unit.get('Cargo'):
        unit['Cargo'] = mark_installed(unit['Cargo'], set(names))
    baked += 1

# The engine reads every value as a string; empty columns are left out.
out = [{k: v for k, v in u.items() if k and v != ''} for u in units]
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=4, ensure_ascii=False)
    f.write('\n')

print(f'{len(out)} units written to {dst}; upgrades baked into {baked} ships; '
      f'{sum(1 for u in units if "Upgrade_Type" in u)} upgrade rows typed')
for line in log:
    print('  ' + line)
