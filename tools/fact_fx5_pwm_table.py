"""Parse the reviewed AB PWM table layout independently of Core profiles.

The caller must establish both CPU-group headings and the exact reviewed clause.
Numerical cells are read from the source, never imported from execution code.
"""
import re


def parse_pwm_hardware_table(clause):
    text = clause.replace('[GLYPH-F06D]', 'μ').replace('µ', 'μ')
    header = 'Output number (s1) ON time (s2) Period CPU module '
    parts = text.split(header)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError('Unrecognized PWM hardware table layout')
    cell = r'Y(\d+) to Y(\d+) (\d+) μs more (\d+) μs more'
    simple = re.fullmatch(cell + ' ' + cell, parts[1].strip())
    combined = re.fullmatch(r'High-speed pulse input/ output module Y(\d+) to Y(\d+) '
        r'Y(\d+) to Y(\d+) (\d+) μs more (\d+) μs more '
        r'Y(\d+) to Y(\d+) \[GLYPH-F0C9\] (\d+) μs more (\d+) μs more', parts[2].strip())
    if simple is None or combined is None:
        raise ValueError('Unrecognized PWM hardware cells')
    a, b = [int(v) for v in simple.groups()], [int(v) for v in combined.groups()]
    def row(models, hardware, lo, hi, on, period):
        if lo > hi or on <= 0 or period <= 0:
            raise ValueError('Invalid PWM hardware cell interval')
        return {'models': models, 'hardware': hardware,
            'outputs': ['Y' + str(i) for i in range(lo, hi + 1)],
            'minimum_ON_us': on, 'minimum_period_us': period}
    return [row(['FX5S', 'FX5UJ'], 'CPU_module', *a[:4]),
        row(['FX5S', 'FX5UJ'], 'CPU_module', *a[4:]),
        row(['FX5U', 'FX5UC'], 'CPU_module', b[0], b[1], b[4], b[5]),
        row(['FX5U', 'FX5UC'], 'CPU_module', *b[6:]),
        row(['FX5U', 'FX5UC'], 'high_speed_pulse_IO_module', b[2], b[3], b[4], b[5])]
