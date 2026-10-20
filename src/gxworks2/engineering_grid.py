"""Observe grid dots, prove a lattice and calibrate only its witnessed area."""
from __future__ import annotations
from collections import defaultdict
from .engineering_errors import NativeEngineeringDriverError


def _observed_dots(pixels):
    width, height = pixels.size
    rgb = pixels.convert('RGB')
    samples = rgb.load()
    dark = {(x, y) for y in range(3, min(height - 3, 140))
            for x in range(40, width - 24)
            if max(samples[x, y]) < 180
            and max(samples[x, y]) - min(samples[x, y]) < 12}
    dots = set()
    # Native dots are isolated one/two-pixel components. A short horizontal
    # slice through text or a port line is not independent grid evidence.
    while dark:
        first = dark.pop()
        pending, component = [first], [first]
        while pending:
            x, y = pending.pop()
            for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0),
                           (1, 0), (-1, 1), (0, 1), (1, 1)):
                point = (x + dx, y + dy)
                if point in dark:
                    dark.remove(point)
                    pending.append(point)
                    component.append(point)
        xs, ys = zip(*component)
        if max(xs) - min(xs) <= 1 and max(ys) - min(ys) <= 1:
            dots.add((min(xs), min(ys)))
    return rgb, dots


def _lattice_candidates(dots):
    candidates = []
    for pitch in range(8, 49):
        phases = defaultdict(set)
        for x, y in dots:
            if (((x - pitch, y) in dots or (x + pitch, y) in dots)
                    and ((x, y - pitch) in dots or (x, y + pitch) in dots)):
                phases[x % pitch, y % pitch].add((x, y))
        for phase, points in phases.items():
            rows = defaultdict(set)
            for x, y in points:
                rows[y].add(x)
            if sum(len(xs) >= 3 for xs in rows.values()) < 3:
                continue
            support, proof = [], set()
            for y, xs in rows.items():
                shared = xs & rows.get(y + pitch, set())
                chains = []
                for x in shared:
                    if x - pitch not in shared:
                        count = 1
                        while x + count * pitch in shared:
                            count += 1
                        chains.append((x, count))
                # Require an observed two-row, eight-column rectangle. Other
                # same-phase paired runs may be partly hidden by native blocks.
                if any(count >= 8 for _, count in chains):
                    support.extend((x, y) for x, count in chains if count >= 3)
                    proof.update((x + column * pitch, row_y)
                                 for x, count in chains if count >= 8
                                 for column in range(count) for row_y in (y, y + pitch))
            if support:
                candidates.append({'pitch': pitch, 'phase': phase, 'points': points,
                                   'proof': proof,
                                   'x': min(x for x, _ in support) - pitch,
                                   'y': min(y for _, y in support) - pitch})
    # Text can hide the vertical neighbour of a boundary dot. Keep that dot
    # as evidence for alias checking only when an observed immediate neighbour
    # already belongs to the fine lattice. Do not extend the lattice repeatedly:
    # a separate, compatible coarse grid must remain ambiguous.
    for candidate in candidates:
        pitch, points = candidate['pitch'], candidate['points']
        candidate['alias_points'] = points | {
            (x, y) for x, y in dots
            if (x % pitch, y % pitch) == candidate['phase'] and any(
                point in points for point in ((x-pitch, y), (x+pitch, y),
                                               (x, y-pitch), (x, y+pitch)))}
    # A complete lattice also supports integral multiples of its period. Only
    # discard such an alias when all its observed rectangles are explained by
    # the finer lattice; a second independent grid must remain ambiguous.
    candidates = [candidate for candidate in candidates if not any(
        candidate['pitch'] > other['pitch']
        and candidate['pitch'] % other['pitch'] == 0
        and candidate['phase'][0] % other['pitch'] == other['phase'][0]
        and candidate['phase'][1] % other['pitch'] == other['phase'][1]
        and candidate['proof'] <= other['alias_points']
        for other in candidates)]
    return candidates


def _select_lattice(candidates):
    horizontal_limit = None
    grid = candidates[0] if len(candidates) == 1 else None
    if len(candidates) == 2:
        left, right = sorted(candidates, key=lambda item: min(x for x, _ in item['proof']))
        pitch = left['pitch']
        phase_distance = min((left['phase'][0] - right['phase'][0]) % pitch,
                             (right['phase'][0] - left['phase'][0]) % pitch)
        if (right['pitch'] == pitch and left['phase'][1] == right['phase'][1]
                and left['y'] == right['y'] and phase_distance == 1):
            sides = []
            for candidate in (left, right):
                rows = defaultdict(set)
                for x, y in candidate['proof']:
                    rows[y].add(x)
                sides.append(rows)
            shared = sorted(sides[0].keys() & sides[1].keys())
            seams = [(max(sides[0][y]), min(sides[1][y])) for y in shared]
            # Wide native captures can shift one horizontal raster pixel at a
            # narrow vertical boundary. Prove adjacent, equal-period rows on
            # both sides; unrelated or overlapping lattices remain ambiguous.
            if (len(seams) >= 3 and all(pitch - 1 <= end - start <= 2 * pitch + 1
                                      for start, end in seams)
                    and max(end for _, end in seams) - min(end for _, end in seams) <= pitch):
                limit = min(x for x, _ in right['points']) - pitch
                stable_rows = [xs for y, xs in sides[0].items() if any(
                    all(x + column * pitch in xs and x + column * pitch < limit for column in range(8))
                    for x in xs)]
                if len(stable_rows) >= 3:
                    grid, horizontal_limit = left, limit
    if grid is None:
        raise NativeEngineeringDriverError('无法唯一确认当前 FBD 页面的二维原生网格。')
    return grid, horizontal_limit


def _is_lattice_dot(samples, width, height, x, y):
    if max(samples[x, y]) >= 180:
        return False
    seen, pending = {(x, y)}, [(x, y)]
    while pending:
        px, py = pending.pop()
        for dx, dy in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)):
            point = (px+dx, py+dy)
            if point in seen or not (0 <= point[0] < width and 0 <= point[1] < height):
                continue
            if max(samples[point[0], point[1]]) < 180:
                seen.add(point)
                if (len(seen) > 4 or max(a for a,b in seen)-min(a for a,b in seen)>1
                        or max(b for a,b in seen)-min(b for a,b in seen)>1):
                    return False
                pending.append(point)
    return min(a for a,b in seen) == x and min(b for a,b in seen) == y


def _first_lattice_band(samples, width, height, grid):
    # Only this network's confirmed phase can extend the initial dot band.
    points, rows = set(), []
    for y in range(grid['y']+grid['pitch'], height-3, grid['pitch']):
        row = {x for x in range(grid['x']+grid['pitch'], width-24, grid['pitch'])
               if x >= 3 and y >= 3 and _is_lattice_dot(samples, width, height, x, y)}
        if not any(all(x+i*grid['pitch'] in row for i in range(8)) for x in row):
            break
        rows.append(y)
        points.update((x,y) for x in row)
    if len(rows) < 2:
        raise NativeEngineeringDriverError('首个网络没有可确认的连续原生点阵。')
    return {'first_lattice_band': [rows[0], rows[-1]],
            'lattice_points': sorted([x, y] for x, y in points)}


def _grid_calibration(pixels, *, include_lattice=False):
    """Observe the native dot lattice; never guess a cached editor rectangle."""
    rgb, dots = _observed_dots(pixels)
    width, height = rgb.size
    grid, horizontal_limit = _select_lattice(_lattice_candidates(dots))
    result = {'pitch': grid['pitch'], 'x': grid['x'], 'y': grid['y'],
              'width': width, 'height': height}
    if horizontal_limit is not None:
        # The raster seam and its other phase have no established coordinates.
        result['calibrated_x_limit'] = horizontal_limit
    if include_lattice:
        result.update(_first_lattice_band(rgb.load(), width, height, grid))
    return result
