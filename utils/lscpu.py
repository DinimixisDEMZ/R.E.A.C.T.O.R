"""
Parsers para la salida de lscpu.
"""


def parse_lscpu_numeric(s):
    if not s:
        return 0.0
    try:
        token = s.split()[0]
        cleaned = "".join(c for c in token if c.isdigit() or c in '.,-')
        if '.' in cleaned and ',' in cleaned:
            if cleaned.rfind('.') > cleaned.rfind(','):
                cleaned = cleaned.replace(',', '')
            else:
                cleaned = cleaned.replace('.', '').replace(',', '.')
        elif ',' in cleaned:
            parts = cleaned.split(',')
            cleaned = parts[0] + '.' + parts[1] if len(parts) == 2 else cleaned.replace(',', '')
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def parse_lscpu_cache(s):
    if not s:
        return 0.0
    try:
        parts = s.split('(')[0].strip().split()
        if not parts:
            return 0.0
        val = parse_lscpu_numeric(parts[0])
        if len(parts) > 1:
            u = parts[1].upper()
            if "KIB" in u or "KB" in u:
                val /= 1024.0
            elif "GIB" in u or "GB" in u:
                val *= 1024.0
        return val
    except (ValueError, TypeError):
        return 0.0


def make_lscpu_finder(flat_map):
    def find(*keys):
        for key in keys:
            k = key.lower()
            if k in flat_map:
                return flat_map[k]
        for key in keys:
            sk = key.lower()
            for lk, v in flat_map.items():
                if sk in lk:
                    return v
        return None
    return find
