#!/usr/bin/env python3
"""Describe leg_tracking_probe logs without deciding new gains or moving hardware."""

import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import statistics

from .leg_tracking_probe import DEG_PER_RAW


def rms(values):
    return math.sqrt(statistics.mean(v * v for v in values))


def sine_fit(times, values, frequency):
    """Least-squares offset + sin + cos, using real sample times (no resampling)."""
    design = [[1, math.sin(2 * math.pi * frequency * t),
               math.cos(2 * math.pi * frequency * t)] for t in times]
    matrix = [[sum(row[i] * row[j] for row in design) for j in range(3)] +
              [sum(row[i] * value for row, value in zip(design, values))] for i in range(3)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda i: abs(matrix[i][column]))
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        if abs(matrix[column][column]) < 1e-10:
            return None
        divisor = matrix[column][column]
        matrix[column] = [v / divisor for v in matrix[column]]
        for i in range(3):
            if i != column:
                factor = matrix[i][column]
                matrix[i] = [v - factor * x for v, x in zip(matrix[i], matrix[column])]
    offset, a, b = [row[3] for row in matrix]
    amplitude = math.hypot(a, b)
    residual = rms([value - (offset + a * row[1] + b * row[2])
                    for value, row in zip(values, design)])
    return {'amplitude': amplitude, 'phase': math.atan2(b, a), 'residual': residual}


def stage_metrics(rows):
    errors = [(r['state']['pos'] - r['target']) * DEG_PER_RAW for r in rows]
    gaps = [b['at'] - a['at'] for a, b in zip(rows, rows[1:])]
    result = {'stage': rows[0]['stage'], 'id': rows[0]['id'], 'kind': rows[0]['kind'],
              'samples': len(rows), 'rms_error_deg': rms(errors),
              'peak_error_deg': max(map(abs, errors)),
              'max_gap_ms': max(gaps, default=0) * 1000,
              'timing_gaps': sum(gap > 1.5 / rows[0]['hz'] for gap in gaps),
              'alarms': sum(bool(r['state']['err'] or r['state']['status']) for r in rows),
              'max_temp_c': max(r['state']['temp'] for r in rows),
              'min_voltage': min(r['state']['volt'] for r in rows),
              'goal_echo_mismatches': sum(r['target'] != r['state']['goal'] for r in rows)}
    holds = [r for r in rows if r['hold']]
    if holds:
        # The last second separates settled scatter from the initial response.
        tail = [r for r in holds if r['at'] >= holds[-1]['at'] - 1]
        result['hold_tail_rms_deg'] = rms([(r['state']['pos'] - r['target']) * DEG_PER_RAW for r in tail])
        result['hold_tail_range_deg'] = (max(r['state']['pos'] for r in tail) -
                                         min(r['state']['pos'] for r in tail)) * DEG_PER_RAW
    frequency = rows[0]['frequency_hz']
    if frequency:
        # Discard the first cycle. Fit target at TX time and feedback at RX time;
        # otherwise serial polling order appears as mechanical phase lag.
        start = rows[0]['command_at']
        steady = [r for r in rows if r['command_at'] - start >= 1 / frequency]
        if len(steady) >= 20 and steady[-1]['at'] - steady[0]['at'] >= 1 / frequency:
            target = sine_fit([r['command_at'] - start for r in steady],
                              [r['target'] for r in steady], frequency)
            actual = sine_fit([r['at'] - start for r in steady],
                              [r['state']['pos'] for r in steady], frequency)
            if target and actual and target['amplitude'] > 1 and actual['amplitude'] > 1:
                phase = (target['phase'] - actual['phase'] + math.pi) % (2 * math.pi) - math.pi
                result.update({'frequency_hz': frequency,
                               'lag_ms': phase / (2 * math.pi * frequency) * 1000,
                               'amplitude_ratio': actual['amplitude'] / target['amplitude'],
                               'sine_residual_deg': actual['residual'] * DEG_PER_RAW})
    return result


def analyze(rows):
    groups = defaultdict(list)
    meta = None
    before = {}
    result = None
    cleanup = None
    issues = []
    for row in rows:
        kind = row['type']
        if kind == 'meta':
            if meta is not None:
                raise ValueError('multiple captures in one log')
            meta = row
        elif kind == 'before':
            before[row['id']] = row
        elif kind == 'sample':
            numeric = [row['at'], row['command_at'], row['target'], row['state']['pos']]
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in numeric):
                raise ValueError('nonfinite or invalid sample')
            key = (row['stage'], row['id'])
            if groups[key] and row['at'] <= groups[key][-1]['at']:
                raise ValueError('sample times must increase within a stage')
            groups[key].append(row)
        elif kind == 'failure':
            issues.append(row['detail'])
        elif kind == 'cleanup':
            cleanup = row
        elif kind == 'result':
            result = row
    if not meta or meta.get('schema') != 'leg-tracking-v1':
        raise ValueError('not a leg-tracking-v1 capture')
    if not groups:
        raise ValueError('no tracking samples')
    if not result or not result.get('completed'):
        issues.append('capture incomplete or failed; metrics are partial')
    if not cleanup or not cleanup.get('confirmed'):
        issues.append('torque-off and P/D restoration not confirmed')
    if set(before) != set(meta['plan']['ids']):
        issues.append('joint snapshots incomplete')
    stages = [stage_metrics(samples) for samples in groups.values()]
    if any(s['timing_gaps'] or s['alarms'] for s in stages):
        issues.append('timing gaps or servo alarms; exclude affected stages from gain comparisons')
    pairs = []
    by_stage = defaultdict(list)
    for stage in stages:
        by_stage[stage['stage']].append(stage)
    for number, stage in by_stage.items():
        if len(stage) == 2 and all('lag_ms' in s for s in stage):
            pairs.append({'stage': number, 'ids': [s['id'] for s in stage],
                          'lag_difference_ms': abs(stage[0]['lag_ms'] - stage[1]['lag_ms'])})
    return {'schema': 'leg-tracking-analysis-v1', 'unloaded_only': True,
            'walking_verified': False, 'issues': issues, 'capture': meta,
            'snapshots': list(before.values()), 'stages': stages, 'pair_timing': pairs,
            'interpretation': 'Compare repeated trials with identical pose/profile. Lag, amplitude and '
                              'hold scatter are observations, not an automatic Kp/Kd prescription. '
                              'Echo mismatch may include feedback latency. Current units unverified.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--csv', type=Path, help='optional long-form samples for plotting')
    args = ap.parse_args()
    with args.capture.open() as source:
        rows = [json.loads(line) for line in source if line.strip()]
    report = analyze(rows)
    with args.output.open('x') as output:
        output.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    if args.csv:
        with args.csv.open('x', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(['stage', 'id', 'command_s', 'feedback_s', 'target_raw',
                             'position_raw', 'error_deg', 'speed_raw', 'load_raw',
                             'studio_current_ma_unverified', 'voltage', 'temp_c', 'reply_error', 'status'])
            for row in rows:
                if row['type'] == 'sample':
                    s = row['state']
                    writer.writerow([row['stage'], row['id'], row['command_at'], row['at'],
                                     row['target'], s['pos'], (s['pos'] - row['target']) * DEG_PER_RAW,
                                     s['speed'], s['load'], s['current_ma'], s['volt'], s['temp'],
                                     s['err'], s['status']])
    print(json.dumps({'output': str(args.output), 'stages': len(report['stages']), 'issues': report['issues']}))
    if report['issues']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
